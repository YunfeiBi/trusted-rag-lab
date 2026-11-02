import logging
from time import perf_counter
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import select

from .dependencies import CurrentUser, Db, bearer
from .generation import PROMPT_VERSION, validate_claims
from .ingestion import IngestionError
from .models import Chunk, QuestionRecord
from .search import SearchInput, authorized_scope, check_access, retrieve
from .security import token_digest

router = APIRouter(prefix='/api')
logger = logging.getLogger('trusted_rag.requests')
MESSAGES = {
    'insufficient_evidence':'现有资料不足以支持回答，请补充资料或换一种提问方式。',
    'generation_unavailable':'生成服务暂不可用，可先查看下方授权证据片段。',
    'invalid_citations':'生成内容未通过结构、引用或原文摘录校验，已停止展示该草稿，请查看证据或重新提问。',
    'evidence_changed':'资料或版本已变化，当前不展示该回答，请重新提问。',
}


class QuestionInput(BaseModel):
    model_config = ConfigDict(extra='forbid',str_strip_whitespace=True)
    project_id: UUID
    question: str = Field(min_length=1,max_length=500)
    retrieval_mode: Literal['keyword','vector','hybrid'] = 'hybrid'
    use_rerank: bool = True
    allow_external_processing: bool = False


def valid_evidence(db, project_id, user_id, evidence, session_hash=None):
    check_access(db,project_id,user_id,session_hash)
    if not evidence:
        return True
    rows = db.execute(authorized_scope(project_id,user_id).where(Chunk.id.in_([UUID(e['chunk_id']) for e in evidence]))).mappings()
    current = {str(r['chunk_id']):r for r in rows}
    return all(e['chunk_id'] in current and str(current[e['chunk_id']]['version_id'])==e['version_id'] and current[e['chunk_id']]['text']==e['text'] for e in evidence)


def output_record(db,record,user_id,session_hash=None):
    valid = valid_evidence(db,record.project_id,user_id,record.evidence,session_hash)
    status = record.status if valid else 'evidence_changed'
    details = dict(record.details)
    if not valid:
        details['warnings'] = [{'code':'HISTORY_EVIDENCE_CHANGED','message':'历史依据已失效或不再是当前版本，已隐藏历史回答与证据。'}]
    return {'id':str(record.id),'project_id':str(record.project_id),'question':record.question,
            'status':status,'answer':record.answer if valid else MESSAGES[status],
            'claims':record.claims if valid else [],'citations':record.citations if valid else [],
            'evidence':record.evidence if valid else [],'details':details,
            'trace_id':record.trace_id,'created_at':record.created_at,
            'citation_check':'reference_and_quote_only' if status=='answered' else 'not_answered'}


@router.post('/questions')
def ask(data: QuestionInput, user: CurrentUser, db: Db, request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials,Depends(bearer)]):
    user_id = user.id
    session_hash = token_digest(credentials.credentials)
    check_access(db,data.project_id,user_id,session_hash)
    if not data.allow_external_processing:
        raise HTTPException(422,'请确认允许将问题和有效证据发送至生成服务，以及所选向量和重排序服务')
    start = perf_counter()
    retrieved = retrieve(db,SearchInput(project_id=data.project_id,query=data.question,mode=data.retrieval_mode,
        use_rerank=data.use_rerank,allow_external_processing=True),user_id,request.app.state.embedding_client,request.app.state.rerank_client,session_hash)
    evidence = retrieved['results']
    status = 'insufficient_evidence'
    answer = MESSAGES[status]
    claims,citations = [],[]
    generation_ms = 0
    details = {'warnings':list(retrieved['warnings']),'retrieval':{k:retrieved[k] for k in ['requested_mode','effective_mode','rerank_status','candidates','timings_ms']},
               'prompt_version':PROMPT_VERSION,'chat_model':request.app.state.chat_client.model}
    if evidence:
        if not valid_evidence(db,data.project_id,user_id,evidence,session_hash):
            status = 'evidence_changed'
            answer = MESSAGES[status]
            evidence = []
        else:
            db.rollback()
            timer = perf_counter()
            try:
                draft = request.app.state.chat_client.generate(data.question,evidence)
                answerable,generated,claims,citations = validate_claims(draft,evidence)
                if answerable:
                    status,answer = 'answered',generated
                else:
                    status,answer = 'insufficient_evidence',MESSAGES['insufficient_evidence']
            except IngestionError as exc:
                status = 'invalid_citations' if exc.code=='GENERATION_INVALID_RESPONSE' else 'generation_unavailable'
                answer = MESSAGES[status]
                claims,citations = [],[]
                details['warnings'].append({'code':exc.code,'message':answer})
            except (ValueError,KeyError,TypeError,ValidationError):
                status,answer = 'invalid_citations',MESSAGES['invalid_citations']
                claims,citations = [],[]
                details['warnings'].append({'code':'CITATION_VALIDATION_FAILED','message':answer})
            generation_ms = (perf_counter()-timer)*1000
    # Final checks and saving are in a short transaction; never persist rejected model drafts.
    db.rollback()
    check_access(db,data.project_id,user_id,session_hash)
    if not valid_evidence(db,data.project_id,user_id,evidence,session_hash):
        status,answer = 'evidence_changed',MESSAGES['evidence_changed']
        evidence,claims,citations = [],[],[]
        details['warnings'].append({'code':'EVIDENCE_CHANGED','message':answer})
    details['generation_ms'] = round(generation_ms,2)
    details['total_ms'] = round((perf_counter()-start)*1000,2)
    record = QuestionRecord(user_id=user_id,project_id=data.project_id,question=data.question,answer=answer,status=status,
        claims=claims,citations=citations,evidence=evidence,details=details,trace_id=request.state.trace_id)
    db.add(record)
    db.commit()
    logger.info('event=question trace_id=%s status=%s evidence=%s generation_ms=%.2f',request.state.trace_id,status,len(evidence),generation_ms)
    return output_record(db,record,user_id,session_hash)


@router.get('/questions')
def history(user: CurrentUser, db: Db, project_id: UUID, offset: int = Query(0,ge=0),limit: int = Query(20,ge=1,le=50)):
    check_access(db,project_id,user.id)
    records = db.scalars(select(QuestionRecord).where(QuestionRecord.user_id==user.id,QuestionRecord.project_id==project_id).order_by(QuestionRecord.created_at.desc()).offset(offset).limit(limit)).all()
    items = []
    for record in records:
        valid = valid_evidence(db,project_id,user.id,record.evidence)
        items.append({'id':str(record.id),'question':record.question,'status':record.status if valid else 'evidence_changed','created_at':record.created_at})
    return items


@router.get('/questions/{question_id}')
def question(question_id: UUID, user: CurrentUser, db: Db,
             credentials: Annotated[HTTPAuthorizationCredentials,Depends(bearer)]):
    record = db.get(QuestionRecord,question_id)
    if not record or record.user_id!=user.id:
        raise HTTPException(404,'记录不存在或不可访问')
    return output_record(db,record,user.id,token_digest(credentials.credentials))
