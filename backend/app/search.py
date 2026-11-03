"""Authorized retrieval: lexical/vector candidates, RRF, rerank and final revalidation."""
import math
import logging
import re
from time import perf_counter
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, HTTPException, Request
from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
from typing import Annotated
import jieba
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Float, cast, func, literal_column, select

from .dependencies import CurrentUser, Db, bearer
from .ingestion import IngestionError
from .models import Chunk, Document, DocumentVersion, LoginSession, Membership, Project, StoredVector, TeamMembership, User, utcnow
from .security import token_digest

router = APIRouter(prefix='/api')
logger = logging.getLogger('trusted_rag.requests')


class SearchInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    project_id: UUID
    query: str = Field(min_length=1, max_length=500)
    mode: Literal['keyword','vector','hybrid'] = 'hybrid'
    top_k: int = Field(5, ge=1, le=10)
    candidate_limit: int = Field(20, ge=5, le=50)
    use_rerank: bool = True
    allow_external_processing: bool = False

    @model_validator(mode='after')
    def limits(self):
        if self.top_k > self.candidate_limit:
            raise ValueError('top_k must not exceed candidate_limit')
        return self


class EvidenceValidationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: UUID
    chunk_ids: list[UUID] = Field(max_length=10)


def check_access(db, project_id, user_id, session_hash=None):
    query = select(Membership.user_id).join(User, User.id == Membership.user_id).join(Project,Project.id==Membership.project_id).join(TeamMembership,(TeamMembership.team_id==Project.team_id)&(TeamMembership.user_id==User.id)).where(
        Membership.project_id == project_id, Membership.user_id == user_id, User.is_active.is_(True))
    if session_hash:
        query = query.join(LoginSession,LoginSession.user_id == User.id).where(LoginSession.token_hash == session_hash,LoginSession.expires_at > utcnow())
    member = db.scalar(query)
    if member is None:
        raise HTTPException(404, '项目不存在或不可访问，请刷新项目列表')


def authorized_scope(project_id, user_id):
    return select(
        Chunk.id.label('chunk_id'), Chunk.version_id, Chunk.ordinal, Chunk.text, Chunk.page,
        Chunk.section, Chunk.line_start, Chunk.line_end,
        Document.id.label('document_id'), Document.title, DocumentVersion.number.label('version_number'),
        DocumentVersion.filename, DocumentVersion.embedding_model,
    ).join(DocumentVersion, DocumentVersion.id == Chunk.version_id).join(Document, Document.id == DocumentVersion.document_id).join(
        Membership, Membership.project_id == Document.project_id).join(User, User.id == Membership.user_id).join(Project,Project.id==Document.project_id).join(TeamMembership,(TeamMembership.team_id==Project.team_id)&(TeamMembership.user_id==User.id)).where(
        Document.project_id == project_id, Membership.user_id == user_id, User.is_active.is_(True),
        Document.is_active.is_(True), Document.current_version_id == DocumentVersion.id,
        DocumentVersion.status == 'ready',
    )


def keyword_terms(query):
    return list(dict.fromkeys(token.lower() for token in jieba.cut(query) if re.fullmatch(r'\w+',token,flags=re.UNICODE)))[:64]


def keyword_candidates(db, scope, query, limit):
    terms = keyword_terms(query)
    if not terms:
        return []
    # Tokens are data bound as a parameter, not interpolated SQL.
    tsquery = func.to_tsquery(literal_column("'pg_catalog.simple'::regconfig"), ' | '.join("'"+term+"'" for term in terms))
    rank = func.ts_rank_cd(Chunk.search_vector, tsquery)
    rows = db.execute(scope.add_columns(rank.label('keyword_score')).where(Chunk.search_vector.op('@@')(tsquery)).order_by(rank.desc(),Chunk.id).limit(limit)).mappings()
    return [dict(row) for row in rows]


def vector_candidates(db, scope, vector, model, limit):
    distance = Chunk.embedding.op('OPERATOR(public.<=>)', return_type=Float)(cast(vector,StoredVector(1024)))
    rows = db.execute(scope.add_columns(distance.label('vector_distance')).where(DocumentVersion.embedding_model == model).order_by(distance,Chunk.id).limit(limit)).mappings()
    return [dict(row) for row in rows if math.isfinite(row['vector_distance'])]


def fuse(keyword, vector, mode, limit):
    fused = {}
    channels = [('keyword',keyword),('vector',vector)]
    for channel, rows in channels:
        for rank, row in enumerate(rows,1):
            entry = fused.setdefault(row['chunk_id'], {**row,'channels':[],'fusion_score':0.0})
            entry['channels'].append(channel)
            entry['fusion_score'] += 1 / (60+rank)
            if channel == 'keyword':
                entry['keyword_score'] = float(row['keyword_score'])
            else:
                entry['vector_similarity'] = 1-float(row['vector_distance'])
    if mode == 'keyword':
        key = lambda entry: (-entry['keyword_score'], str(entry['chunk_id']))
    elif mode == 'vector':
        key = lambda entry: (-entry['vector_similarity'],str(entry['chunk_id']))
    else:
        key = lambda entry: (-entry['fusion_score'],str(entry['chunk_id']))
    return sorted(fused.values(),key=key)[:limit]


def revalidate(db, project_id, user_id, candidates, session_hash=None):
    check_access(db,project_id,user_id,session_hash)
    if not candidates:
        return []
    rows = db.execute(authorized_scope(project_id,user_id).where(Chunk.id.in_([r['chunk_id'] for r in candidates]))).mappings()
    fresh = {r['chunk_id']:dict(r) for r in rows}
    results = []
    for old in candidates:
        if old['chunk_id'] in fresh:
            scores = {key: old[key] for key in ['channels','fusion_score','keyword_score','vector_similarity','rerank_score'] if key in old}
            results.append({**fresh[old['chunk_id']],**scores})
    return results


def retrieve(db, data, user_id, embedding, reranker, session_hash=None):
    start = perf_counter()
    timings = {}
    warnings = []
    counts = {'keyword':0,'vector':0,'fused':0,'filtered':0}
    rerank_status = 'not_requested'

    def warning(code,message):
        if not any(w['code']==code for w in warnings):
            warnings.append({'code':code,'message':message})

    check_access(db,data.project_id,user_id,session_hash)
    if data.mode != 'keyword' and not data.allow_external_processing:
        raise HTTPException(422,'请确认允许将问题发送至向量服务，并在启用重排序时发送授权候选片段')
    scope = authorized_scope(data.project_id,user_id)
    corpus_count = db.scalar(select(func.count()).select_from(scope.subquery()))
    compatible_count = db.scalar(select(func.count()).select_from(scope.where(DocumentVersion.embedding_model == embedding.model).subquery())) if data.mode != 'keyword' else 0
    db.rollback()  # Never hold a transaction open during model requests.
    keyword, vector = [], []
    if corpus_count and data.mode != 'keyword':
        if 0 < compatible_count < corpus_count:
            warning('PARTIAL_MODEL_MISMATCH','部分当前片段使用了不同向量模型，向量路仅搜索兼容片段；可重新入库以统一模型。')
        if not compatible_count:
            warning('INDEX_MODEL_MISMATCH','当前可用资料未使用本次向量模型建库，请按当前模型重新入库；关键词检索仍可使用。')
        else:
            timer = perf_counter()
            try:
                query_vector = embedding.embed([data.query])[0]
                if len(query_vector) != 1024 or any(type(v) not in (int,float) or not math.isfinite(v) for v in query_vector) or not any(query_vector):
                    raise IngestionError('EMBEDDING_INVALID_RESPONSE')
            except IngestionError:
                if data.mode == 'vector':
                    raise HTTPException(503,'向量服务不可用，请稍后重试或切换关键词检索')
                warning('VECTOR_UNAVAILABLE','向量服务暂不可用，本次仅使用关键词召回。')
                query_vector = None
            timings['embedding'] = (perf_counter()-timer)*1000
            if query_vector is not None:
                timer = perf_counter()
                vector = vector_candidates(db,scope,query_vector,embedding.model,data.candidate_limit)
                timings['vector'] = (perf_counter()-timer)*1000
    if corpus_count and data.mode != 'vector':
        timer = perf_counter()
        keyword = keyword_candidates(db,scope,data.query,data.candidate_limit)
        timings['keyword'] = (perf_counter()-timer)*1000
    counts['keyword'],counts['vector'] = len(keyword),len(vector)
    timer = perf_counter()
    candidates = fuse(keyword,vector,data.mode,data.candidate_limit)
    timings['fusion'] = (perf_counter()-timer)*1000
    counts['fused'] = len(candidates)
    db.rollback()
    candidates = revalidate(db,data.project_id,user_id,candidates,session_hash)
    db.rollback()
    if len(candidates)<counts['fused']:
        warning('SCOPE_CHANGED','检索期间资料或权限发生变化，已排除失效片段；可重新搜索。')
    if data.mode=='hybrid' and data.use_rerank and len(candidates)>1:
        timer = perf_counter()
        try:
            ranked = reranker.rerank(data.query,[r['text'] for r in candidates])
            if any(type(r['index']) is not int for r in ranked) or sorted(r['index'] for r in ranked) != list(range(len(candidates))) or any(type(r['score']) not in (int,float) or not math.isfinite(r['score']) for r in ranked) or [r['score'] for r in ranked] != sorted((r['score'] for r in ranked),reverse=True):
                raise IngestionError('RERANK_INVALID_RESPONSE')
            candidates = [{**candidates[r['index']],'rerank_score':float(r['score'])} for r in ranked]
            rerank_status = 'applied'
        except IngestionError:
            warning('RERANK_UNAVAILABLE','重排序服务暂不可用，本次保留融合排序。')
            rerank_status = 'unavailable'
        timings['rerank'] = (perf_counter()-timer)*1000
    elif data.mode=='hybrid' and data.use_rerank:
        rerank_status = 'not_needed'
    timer = perf_counter()
    before = len(candidates)
    candidates = revalidate(db,data.project_id,user_id,candidates,session_hash)
    if len(candidates)<before:
        warning('SCOPE_CHANGED','检索期间资料或权限发生变化，已排除失效片段；可重新搜索。')
    counts['filtered'] = counts['fused']-len(candidates)
    timings['revalidate'] = (perf_counter()-timer)*1000
    # No rerank call is needed for zero/one candidate. Empty corpus never calls a model.
    results = []
    for rank,row in enumerate(candidates[:data.top_k],1):
        results.append({
            'evidence_id':f'E{rank}', 'chunk_id':str(row['chunk_id']), 'document_id':str(row['document_id']),
            'version_id':str(row['version_id']), 'version_number':row['version_number'], 'title':row['title'],
            'filename':row['filename'], 'text':row['text'], 'page':row['page'], 'section':row['section'],
            'line_start':row['line_start'], 'line_end':row['line_end'], 'ordinal':row['ordinal'],
            'channels':row['channels'], 'scores':{key:round(float(row[key]),6) for key in ['keyword_score','vector_similarity','fusion_score','rerank_score'] if key in row},
            'source_path':f'/documents/{row["document_id"]}/source?version_id={row["version_id"]}',
        })
    if not corpus_count:
        warning('EMPTY_CORPUS','当前项目暂无可用资料，请先完成资料入库。')
    effective = 'hybrid' if keyword and vector else 'keyword' if keyword else 'vector' if vector else 'none'
    timings['total'] = (perf_counter()-start)*1000
    return {'project_id':str(data.project_id), 'query':data.query, 'requested_mode':data.mode, 'effective_mode':effective,
            'status':'degraded' if warnings and corpus_count else 'ok' if results else 'no_results',
            'rerank_status':rerank_status, 'warnings':warnings, 'candidates':counts,
            'timings_ms':{key:round(value,2) for key,value in timings.items()}, 'results':results}


@router.post('/search')
def search(data: SearchInput, user: CurrentUser, db: Db, request: Request,
           credentials: Annotated[HTTPAuthorizationCredentials,Depends(bearer)]):
    result = retrieve(db,data,user.id,request.app.state.embedding_client,request.app.state.rerank_client,token_digest(credentials.credentials))
    result['trace_id'] = request.state.trace_id
    logger.info('event=search trace_id=%s mode=%s effective=%s results=%s rerank=%s timings_ms=%s',
                request.state.trace_id,data.mode,result['effective_mode'],len(result['results']),result['rerank_status'],result['timings_ms'])
    return result


@router.post('/search/validate')
def validate_evidence(data: EvidenceValidationInput, user: CurrentUser, db: Db):
    check_access(db,data.project_id,user.id)
    rows = db.execute(authorized_scope(data.project_id,user.id).where(Chunk.id.in_(data.chunk_ids))).mappings() if data.chunk_ids else []
    return {'valid_chunk_ids':[str(row['chunk_id']) for row in rows]}
