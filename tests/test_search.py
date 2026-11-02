from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.ingestion import IngestionError
from backend.app.models import Chunk
from tests.conftest import login
from tests.test_documents import FakeEmbedding, upload, worker_for


class FakeRerank:
    model = 'test-rerank'

    def __init__(self):
        self.documents = []

    def rerank(self, query, documents):
        self.documents.append(documents)
        rows = [{'index':i,'score':0.9 if 'preferred' in text else 0.1} for i,text in enumerate(documents)]
        return sorted(rows,key=lambda r:(-r['score'],r['index']))


@pytest.fixture
def search_env(environment):
    client,engine,ids = environment
    client.app.state.embedding_client = FakeEmbedding()
    client.app.state.rerank_client = FakeRerank()
    owner = login(client)
    return client,engine,ids,owner


def ingest(client,engine,ids,owner,text,document=None,model=None):
    response = upload(client,ids,owner,text=text,document=document)
    assert response.status_code == 202
    assert worker_for(client,engine,model).run_once()
    return response.json()


def search(client,ids,headers,**changes):
    data = {'project_id':ids['project'],'query':'membership','mode':'hybrid','allow_external_processing':True,**changes}
    return client.post('/api/search',headers=headers,json=data)


def test_search_auth_consent_and_parameter_limits(search_env):
    client,_,ids,owner = search_env
    outsider = login(client,'outsider')
    assert search(client,ids,{}).status_code == 401
    assert search(client,ids,outsider).status_code == 404
    assert search(client,ids,owner,allow_external_processing=False).status_code == 422
    for changes in [{'query':'  '},{'query':'x'*501},{'top_k':11},{'candidate_limit':51},{'top_k':10,'candidate_limit':5}]:
        assert search(client,ids,owner,**changes).status_code == 422
    assert client.app.state.embedding_client.calls == 0


def test_empty_corpus_does_not_call_services(search_env):
    client,_,ids,owner = search_env
    data = search(client,ids,owner).json()
    assert data['status']=='no_results' and data['results']==[]
    assert data['warnings'][0]['code']=='EMPTY_CORPUS'
    assert client.app.state.embedding_client.calls == 0
    assert client.app.state.rerank_client.documents == []


def test_chinese_keyword_search_is_local(search_env):
    client,engine,ids,owner = search_env
    ingest(client,engine,ids,owner,'项目成员通过角色权限管理文档访问。')
    result = search(client,ids,owner,query='权限',mode='keyword',allow_external_processing=False)
    assert result.status_code == 200
    data = result.json()
    assert data['results'] and '权限' in data['results'][0]['text']
    assert data['effective_mode']=='keyword' and data['rerank_status']=='not_requested'
    assert data['results'][0]['channels']==['keyword']
    assert client.app.state.embedding_client.calls == 0 and client.app.state.rerank_client.documents == []


def test_vector_order_and_fusion_rerank(search_env):
    client,engine,ids,owner = search_env
    one = ingest(client,engine,ids,owner,'membership preferred evidence')
    two = ingest(client,engine,ids,owner,'membership unrelated evidence')
    with Session(engine) as db,db.begin():
        db.scalar(select(Chunk).where(Chunk.version_id==UUID(two['version_id']))).embedding = [0.0,1.0]+[0.0]*1022
    pure = search(client,ids,owner,mode='vector').json()
    assert pure['results'][0]['version_id']==one['version_id']
    assert pure['rerank_status']=='not_requested'
    fused = search(client,ids,owner).json()
    assert fused['rerank_status']=='applied'
    assert len(fused['results'])==2 and fused['candidates']['fused']==2
    assert fused['results'][0]['text']=='membership preferred evidence'
    assert fused['results'][0]['channels']==['keyword','vector']
    assert fused['results'][0]['scores']['rerank_score']==0.9
    assert 'embedding' not in fused['results'][0] and 'storage_key' not in str(fused)
    assert fused['trace_id']


def test_historical_withdrawn_and_private_evidence_excluded(search_env):
    client,engine,ids,owner = search_env
    one = ingest(client,engine,ids,owner,'membership OLD_VERSION_SECRET')
    two = ingest(client,engine,ids,owner,'membership preferred current',document=one['document_id'])
    removed = ingest(client,engine,ids,owner,'membership WITHDRAWN_SECRET')
    assert client.post('/api/documents/'+removed['document_id']+'/withdraw',headers=owner).status_code == 200
    outsider = login(client,'outsider')
    project = client.post('/api/projects',headers=outsider,json={'name':'private'}).json()
    ingest(client,engine,{'project':project['id']},outsider,'membership PRIVATE_PROJECT_SECRET')
    extra = ingest(client,engine,ids,owner,'membership extra current')
    result = search(client,ids,login(client,'member'))
    assert result.status_code==200
    data = result.json()
    assert {r['version_id'] for r in data['results']}=={two['version_id'],extra['version_id']}
    sent = str(client.app.state.rerank_client.documents)
    for text in [result.text,sent]:
        assert all(secret not in text for secret in ['OLD_VERSION_SECRET','WITHDRAWN_SECRET','PRIVATE_PROJECT_SECRET'])


def test_model_mismatch_skips_incompatible_vectors(search_env):
    client,engine,ids,owner = search_env
    model = FakeEmbedding()
    model.model = 'old-model'
    ingest(client,engine,ids,owner,'membership lexical evidence',model=model)
    result = search(client,ids,owner).json()
    assert result['effective_mode']=='keyword'
    assert result['results'] and result['candidates']['vector']==0
    assert result['warnings'][0]['code']=='INDEX_MODEL_MISMATCH'
    assert client.app.state.embedding_client.calls==0
    assert search(client,ids,owner,mode='vector').json()['results']==[]


def test_partial_model_mismatch_is_reported(search_env):
    client,engine,ids,owner = search_env
    ingest(client,engine,ids,owner,'membership current model')
    old = FakeEmbedding()
    old.model = 'old-model'
    ingest(client,engine,ids,owner,'membership old model',model=old)
    data = search(client,ids,owner).json()
    assert data['candidates']['vector']==1 and data['candidates']['keyword']==2
    assert data['status']=='degraded'
    assert any(w['code']=='PARTIAL_MODEL_MISMATCH' for w in data['warnings'])


def test_vector_failure_hybrid_fallback_but_baseline_fails(search_env):
    client,engine,ids,owner = search_env
    ingest(client,engine,ids,owner,'membership local result')
    class DownEmbedding(FakeEmbedding):
        def embed(self,texts):
            raise IngestionError('EMBEDDING_UNAVAILABLE')
    client.app.state.embedding_client = DownEmbedding()
    result = search(client,ids,owner).json()
    assert result['status']=='degraded' and result['effective_mode']=='keyword' and result['results']
    assert search(client,ids,owner,mode='vector').status_code==503


@pytest.mark.parametrize('invalid',[False,True])
def test_rerank_failure_and_invalid_response_fall_back(search_env,invalid):
    client,engine,ids,owner = search_env
    for text in ['membership one','membership two']:
        ingest(client,engine,ids,owner,text)
    class BadRerank:
        def rerank(self,query,documents):
            if invalid:
                return [{'index':0,'score':1.0},{'index':0,'score':0.1}]
            raise IngestionError('RERANK_UNAVAILABLE')
    client.app.state.rerank_client = BadRerank()
    data = search(client,ids,owner).json()
    assert data['status']=='degraded' and data['rerank_status']=='unavailable'
    assert len(data['results'])==2


def test_withdraw_during_rerank_removes_result(search_env):
    client,engine,ids,owner = search_env
    removed = ingest(client,engine,ids,owner,'membership SECRET_REMOVED_IN_FLIGHT')
    retained = ingest(client,engine,ids,owner,'membership preferred retained')
    class MutatingRerank(FakeRerank):
        def rerank(self,query,documents):
            assert client.post('/api/documents/'+removed['document_id']+'/withdraw',headers=owner).status_code==200
            return super().rerank(query,documents)
    client.app.state.rerank_client = MutatingRerank()
    result = search(client,ids,owner)
    assert 'SECRET_REMOVED_IN_FLIGHT' not in result.text
    assert [r['version_id'] for r in result.json()['results']]==[retained['version_id']]
    assert result.json()['candidates']['filtered']==1


@pytest.mark.parametrize('logout',[False,True])
def test_revocation_or_logout_during_rerank_returns_no_evidence(search_env,logout):
    client,engine,ids,owner = search_env
    for text in ['membership SECRET_ONE','membership SECRET_TWO']:
        ingest(client,engine,ids,owner,text)
    member = login(client,'member')
    class RevokingRerank(FakeRerank):
        def rerank(self,query,documents):
            if logout:
                assert client.post('/api/auth/logout',headers=member).status_code==204
            else:
                assert client.delete('/api/projects/'+ids['project']+'/members/'+ids['member'],headers=owner).status_code==204
            return super().rerank(query,documents)
    client.app.state.rerank_client = RevokingRerank()
    result = search(client,ids,member)
    assert result.status_code==404 and 'SECRET_' not in result.text


def test_version_switch_during_rerank_removes_historical_evidence(search_env):
    client,engine,ids,owner = search_env
    old = ingest(client,engine,ids,owner,'membership OLD_IN_FLIGHT')
    ingest(client,engine,ids,owner,'membership preferred retained')
    class UpdatingRerank(FakeRerank):
        def rerank(self,query,documents):
            ingest(client,engine,ids,owner,'membership new current',document=old['document_id'])
            return super().rerank(query,documents)
    client.app.state.rerank_client = UpdatingRerank()
    result = search(client,ids,owner)
    assert result.status_code==200 and 'OLD_IN_FLIGHT' not in result.text
    assert result.json()['candidates']['filtered']==1


def test_evidence_validation_checks_current_scope_without_models(search_env):
    client,engine,ids,owner = search_env
    record = ingest(client,engine,ids,owner,'membership visible evidence')
    with Session(engine) as db:
        chunk_id = str(db.scalar(select(Chunk.id)))
    data = {'project_id':ids['project'],'chunk_ids':[chunk_id,str(uuid4())]}
    valid = client.post('/api/search/validate',headers=owner,json=data)
    assert valid.json()['valid_chunk_ids']==[chunk_id]
    assert client.post('/api/search/validate',headers=login(client,'outsider'),json=data).status_code==404
    assert client.post('/api/search/validate',headers=owner,json={**data,'chunk_ids':[str(uuid4()) for _ in range(11)]}).status_code==422
    assert client.post('/api/documents/'+record['document_id']+'/withdraw',headers=owner).status_code==200
    assert client.post('/api/search/validate',headers=owner,json=data).json()['valid_chunk_ids']==[]
    assert client.app.state.embedding_client.calls==0 and client.app.state.rerank_client.documents==[]


def test_withdraw_during_embedding_prevents_rerank_disclosure(search_env):
    client,engine,ids,owner = search_env
    record = ingest(client,engine,ids,owner,'membership SECRET_WITHDRAWN_BEFORE_RERANK')
    class WithdrawingEmbedding(FakeEmbedding):
        def embed(self,texts):
            assert client.post('/api/documents/'+record['document_id']+'/withdraw',headers=owner).status_code==200
            return super().embed(texts)
    client.app.state.embedding_client = WithdrawingEmbedding()
    result = search(client,ids,owner)
    assert result.status_code==200 and result.json()['results']==[]
    assert client.app.state.rerank_client.documents==[]
    assert 'SECRET_WITHDRAWN_BEFORE_RERANK' not in result.text
