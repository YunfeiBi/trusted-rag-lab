"""Human-labelled retrieval experiments; no automatic answer truth grading."""
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import select

from .audit import record_event
from .dependencies import CurrentUser, Db, bearer, require_membership
from .models import Chunk, EvaluationCase, EvaluationRun
from .search import SearchInput, authorized_scope, check_access, retrieve
from .security import token_digest

router = APIRouter(prefix='/api')


class CaseInput(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    question: str = Field(min_length=1,max_length=500)
    answerable: bool = True
    gold_chunk_ids: list[UUID] = Field(default_factory=list,max_length=20)
    notes: str = Field(default='',max_length=2000)
    human_confirmed: bool = False

    @model_validator(mode='after')
    def gold_requirement(self):
        if self.answerable != bool(self.gold_chunk_ids):
            raise ValueError('Answerable cases need evidence; negative cases have no gold evidence')
        if len(self.gold_chunk_ids) != len(set(self.gold_chunk_ids)):
            raise ValueError('Duplicate evidence')
        return self


class RunInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    allow_external_processing: bool = False


def current_ids(db,project_id,user_id,ids):
    return {str(r) for r in db.scalars(authorized_scope(project_id,user_id).with_only_columns(Chunk.id).where(Chunk.id.in_([UUID(i) for i in ids])))} if ids else set()


def case_output(db,case,user_id):
    valid = current_ids(db,case.project_id,user_id,case.gold_chunk_ids)==set(case.gold_chunk_ids)
    return {'id':str(case.id),'question':case.question,'answerable':case.answerable,'gold_chunk_ids':case.gold_chunk_ids if valid else [],'notes':case.notes,'human_confirmed':case.human_confirmed,'evidence_current':valid,'created_at':case.created_at}


@router.get('/projects/{project_id}/evaluation-cases')
def cases(project_id:UUID,user:CurrentUser,db:Db,offset:int=Query(0,ge=0),limit:int=Query(50,ge=1,le=200)):
    require_membership(db,project_id,user.id)
    return [case_output(db,c,user.id) for c in db.scalars(select(EvaluationCase).where(EvaluationCase.project_id==project_id).order_by(EvaluationCase.created_at.desc()).offset(offset).limit(limit))]


@router.post('/projects/{project_id}/evaluation-cases',status_code=201)
def create_case(project_id:UUID,data:CaseInput,user:CurrentUser,db:Db,request:Request):
    require_membership(db,project_id,user.id,admin=True,lock=True)
    ids = [str(i) for i in data.gold_chunk_ids]
    if current_ids(db,project_id,user.id,ids)!=set(ids):
        raise HTTPException(422,'标准证据必须来自当前项目可用版本，请重新选择。')
    case = EvaluationCase(project_id=project_id,created_by=user.id,question=data.question,answerable=data.answerable,gold_chunk_ids=ids,notes=data.notes,human_confirmed=data.human_confirmed)
    db.add(case)
    db.flush()
    record_event(db,project_id,user.id,'evaluation.case_created',case.id,request.state.trace_id)
    db.commit()
    return case_output(db,case,user.id)


def metrics(gold,ranked):
    if not gold:
        return {'recall_at_5':None,'reciprocal_rank':None,'hit':None}
    return {'recall_at_5':len(set(ranked)&set(gold))/len(gold),
            'reciprocal_rank':next((1/(i+1) for i,c in enumerate(ranked) if c in gold),0),
            'hit':bool(set(ranked)&set(gold))}


@router.post('/evaluation-cases/{case_id}/runs',status_code=201)
def run_case(case_id:UUID,data:RunInput,user:CurrentUser,db:Db,request:Request,credentials:Annotated[HTTPAuthorizationCredentials,Depends(bearer)]):
    case = db.get(EvaluationCase,case_id)
    if not case:
        raise HTTPException(404,'评测样例不存在')
    user_id, project_id = user.id,case.project_id
    digest = token_digest(credentials.credentials)
    check_access(db,project_id,user_id,digest)
    if not data.allow_external_processing:
        raise HTTPException(422,'请确认允许向量和重排服务处理问题及授权证据。')
    if not case.human_confirmed:
        raise HTTPException(409,'请由人工复核后重新创建已确认样例，草稿不可用于评测。')
    if not case_output(db,case,user_id)['evidence_current']:
        raise HTTPException(409,'标准证据版本已变化，请重新标注。')
    snapshot = {'question':case.question,'gold_chunk_ids':case.gold_chunk_ids,'answerable':case.answerable,
                'case_id':str(case.id),'top_k':5,'candidate_limit':20,'fusion_k':60,
                'embedding_model':request.app.state.embedding_client.model,'rerank_model':request.app.state.rerank_client.model}
    results = []
    for label,mode,rerank in [('vector','vector',False),('hybrid','hybrid',False),('hybrid_rerank','hybrid',True)]:
        try:
            found = retrieve(db,SearchInput(project_id=project_id,query=snapshot['question'],mode=mode,use_rerank=rerank,top_k=5,allow_external_processing=True),user_id,request.app.state.embedding_client,request.app.state.rerank_client,digest)
            ranked = [e['chunk_id'] for e in found['results']]
            results.append({'mode':label,'status':found['status'],'chunk_ids':ranked,'effective_mode':found['effective_mode'],'rerank_status':found['rerank_status'],'timings_ms':found['timings_ms'],'warnings':found['warnings'],**metrics(snapshot['gold_chunk_ids'],ranked)})
        except HTTPException as exc:
            if exc.status_code!=503:
                raise
            results.append({'mode':label,'status':'unavailable','chunk_ids':[],'recall_at_5':None,'reciprocal_rank':None,'hit':None})
    db.rollback()
    check_access(db,project_id,user_id,digest)
    ids = set(snapshot['gold_chunk_ids']) | {i for r in results for i in r['chunk_ids']}
    if current_ids(db,project_id,user_id,ids)!=ids:
        raise HTTPException(409,'评测期间资料版本已变化，结果未保存，请重新运行。')
    result = EvaluationRun(project_id=project_id,user_id=user_id,case_id=case_id,snapshot=snapshot,results=results)
    db.add(result)
    db.flush()
    record_event(db,project_id,user_id,'evaluation.run_completed',result.id,request.state.trace_id,{'case_count':1})
    db.commit()
    return {'id':str(result.id),'snapshot':snapshot,'results':results,'created_at':result.created_at,'evidence_current':True}


@router.get('/projects/{project_id}/evaluation-runs')
def runs(project_id:UUID,user:CurrentUser,db:Db,offset:int=Query(0,ge=0),limit:int=Query(20,ge=1,le=50)):
    require_membership(db,project_id,user.id)
    rows = db.scalars(select(EvaluationRun).where(EvaluationRun.project_id==project_id,EvaluationRun.user_id==user.id).order_by(EvaluationRun.created_at.desc()).offset(offset).limit(limit))
    output = []
    for r in rows:
        ids = set(r.snapshot['gold_chunk_ids']) | {i for x in r.results for i in x['chunk_ids']}
        valid = current_ids(db,project_id,user.id,ids)==ids
        output.append({'id':str(r.id),'snapshot':r.snapshot if valid else {'question':r.snapshot['question']},'results':r.results if valid else [],'created_at':r.created_at,'evidence_current':valid})
    return output
