from uuid import UUID
import pytest

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import Chunk,EvaluationRun
from backend.app.evaluation import metrics
from tests.conftest import login
from tests.test_documents import FakeEmbedding,upload,worker_for
from tests.test_search import FakeRerank


def prepare(environment):
    client,engine,ids=environment
    owner=login(client)
    result=upload(client,ids,owner,text='membership preferred evidence for access control').json()
    worker_for(client,engine).run_once()
    with Session(engine) as db:
        gold=str(db.scalar(select(Chunk.id).where(Chunk.version_id==UUID(result['version_id']))))
    client.app.state.embedding_client=FakeEmbedding()
    client.app.state.rerank_client=FakeRerank()
    return client,engine,ids,owner,result,gold


def test_task_center_and_audit_permissions(environment):
    client,_,ids,owner,doc,_=prepare(environment)
    member,outsider=login(client,'member'),login(client,'outsider')
    tasks='/api/projects/'+ids['project']+'/tasks'
    assert client.get(tasks,headers=outsider).status_code==404
    result=client.get(tasks,headers=member).json()
    assert result[0]['id']==doc['task_id'] and 'storage_key' not in str(result)
    assert client.get(tasks+'?status=failed',headers=owner).json()==[]
    audit='/api/projects/'+ids['project']+'/audit-events'
    assert client.get(audit,headers=member).status_code==403
    result=client.get(audit,headers=owner).json()
    assert result[0]['action']=='document.version_uploaded'
    assert result[0]['trace_id'] and 'membership preferred evidence' not in str(result)
    client.post('/api/documents/'+doc['document_id']+'/withdraw',headers=owner)
    assert client.get(tasks,headers=member).json()==[]
    assert client.get(audit,headers=owner).json()[0]['action']=='document.withdrawn'


def test_evaluation_confirmation_consent_metrics_and_private_runs(environment):
    client,_,ids,owner,_,gold=prepare(environment)
    path='/api/projects/'+ids['project']+'/evaluation-cases'
    payload={'question':'membership','gold_chunk_ids':[gold],'human_confirmed':True}
    assert client.post(path,headers=login(client,'member'),json=payload).status_code==403
    draft=client.post(path,headers=owner,json={**payload,'human_confirmed':False}).json()
    assert client.post('/api/evaluation-cases/'+draft['id']+'/runs',headers=owner,json={'allow_external_processing':True}).status_code==409
    case=client.post(path,headers=owner,json=payload).json()
    run='/api/evaluation-cases/'+case['id']+'/runs'
    assert client.post(run,headers=owner,json={}).status_code==422
    response=client.post(run,headers=owner,json={'allow_external_processing':True})
    assert response.status_code==201,response.text
    assert len(response.json()['results'])==3
    assert all(r['recall_at_5']==1 and r['reciprocal_rank']==1 for r in response.json()['results'])
    history='/api/projects/'+ids['project']+'/evaluation-runs'
    assert len(client.get(history,headers=owner).json())==1
    assert client.get(history,headers=login(client,'member')).json()==[]


def test_evaluation_evidence_staleness_and_project_scope(environment):
    client,engine,ids,owner,doc,gold=prepare(environment)
    path='/api/projects/'+ids['project']+'/evaluation-cases'
    outsider=login(client,'outsider')
    other=client.post('/api/projects',headers=outsider,json={'name':'private'}).json()
    assert client.post('/api/projects/'+other['id']+'/evaluation-cases',headers=outsider,json={'question':'test','gold_chunk_ids':[gold]}).status_code==422
    case=client.post(path,headers=owner,json={'question':'membership','gold_chunk_ids':[gold],'human_confirmed':True}).json()
    assert client.post('/api/evaluation-cases/'+case['id']+'/runs',headers=owner,json={'allow_external_processing':True}).status_code==201
    new=upload(client,ids,owner,document=doc['document_id'],text='membership replacement').json()
    worker_for(client,engine).run_once()
    assert new['version_id']!=doc['version_id']
    assert client.get(path,headers=owner).json()[0]['evidence_current'] is False
    history=client.get('/api/projects/'+ids['project']+'/evaluation-runs',headers=owner).json()[0]
    assert history['evidence_current'] is False and history['results']==[]
    assert client.post('/api/evaluation-cases/'+case['id']+'/runs',headers=owner,json={'allow_external_processing':True}).status_code==409


def test_metrics_do_not_score_unanswerable_as_perfect():
    assert metrics([],[])['recall_at_5'] is None
    assert metrics(['a','b'],['x','b'])=={'recall_at_5':.5,'reciprocal_rank':.5,'hit':True}


@pytest.mark.parametrize('logout',[False,True])
def test_evaluation_scope_change_during_external_call_is_not_saved(environment,logout):
    client,engine,ids,owner,doc,gold=prepare(environment)
    case=client.post('/api/projects/'+ids['project']+'/evaluation-cases',headers=owner,json={'question':'membership','gold_chunk_ids':[gold],'human_confirmed':True}).json()

    class ChangingEmbedding(FakeEmbedding):
        def embed(self,texts):
            if self.calls==2:
                if logout:
                    assert client.post('/api/auth/logout',headers=owner).status_code==204
                else:
                    assert client.post('/api/documents/'+doc['document_id']+'/withdraw',headers=owner).status_code==200
            return super().embed(texts)

    client.app.state.embedding_client=ChangingEmbedding()
    response=client.post('/api/evaluation-cases/'+case['id']+'/runs',headers=owner,json={'allow_external_processing':True})
    assert response.status_code==(404 if logout else 409)
    with Session(engine) as db:
        assert db.scalar(select(EvaluationRun)) is None
