from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from sqlalchemy import func,select
from sqlalchemy.orm import Session

from backend.app.models import AuditEvent,Document,DocumentVersion,IngestionTask,Membership,Project,Team,TeamApplication,TeamMembership,User
from backend.app.storage import storage_path
from tests.conftest import login
from tests.test_documents import FakeEmbedding,upload,worker_for
from tests.test_questions import FakeChat
from tests.test_search import FakeRerank
from tests.test_teams import create,join


def delete(client,team,headers,name=None):
    return client.request('DELETE','/api/teams/'+team['id'],headers=headers,json={'confirm_name':name if name is not None else team['name']})


def test_only_owner_can_preview_delete_and_exact_name_required(environment):
    client,engine,ids=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    team=create(client,owner);join(client,team,owner,member)
    preview='/api/teams/'+team['id']+'/deletion-preview'
    assert client.get(preview).status_code==401
    assert client.get(preview,headers=outsider).status_code==404
    assert client.get(preview,headers=member).status_code==403
    client.put('/api/teams/'+team['id']+'/members/'+ids['member'],headers=owner,json={'role':'admin'})
    assert delete(client,team,member).status_code==403
    assert delete(client,team,outsider).status_code==404
    assert delete(client,team,owner,name='incorrect name').status_code==409
    preview_data=client.get(preview,headers=owner).json()
    assert preview_data['members']==2 and preview_data['projects']==0
    with Session(engine) as db:
        assert db.get(Team,UUID(team['id'])).is_active


def test_delete_revokes_all_scopes_cancels_jobs_and_retains_data(environment):
    client,engine,ids=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    team=create(client,owner);join(client,team,owner,member)
    project=client.post('/api/projects',headers=owner,json={'name':'to delete','team_id':team['id']}).json()
    client.put('/api/projects/'+project['id']+'/members/'+ids['member'],headers=owner,json={'role':'member'})
    ready=upload(client,{'project':project['id']},owner,text='membership controls access to retained source').json()
    worker=worker_for(client,engine);worker.run_once()
    processing=upload(client,{'project':project['id']},owner).json();lease=worker.claim()
    queued=upload(client,{'project':team['library_project_id']},owner).json()
    application=client.post('/api/team-applications',headers=outsider,json={'code':team['code']}).json()
    preview=client.get('/api/teams/'+team['id']+'/deletion-preview',headers=owner).json()
    assert preview['projects']==1 and preview['documents']==3 and preview['pending_tasks']==2
    assert delete(client,team,owner).status_code==204
    worker.process(*lease)
    for headers in [owner,member]:
        for path in ['/api/teams/'+team['id']+'/members','/api/projects/'+project['id'],'/api/documents/'+ready['document_id']+'/source','/api/projects/'+team['library_project_id']+'/documents']:
            assert client.get(path,headers=headers).status_code==404
        assert team['id'] not in {t['id'] for t in client.get('/api/teams',headers=headers).json()}
        assert project['id'] not in {p['id'] for p in client.get('/api/projects?include_libraries=true',headers=headers).json()}
    assert client.post('/api/team-applications',headers=outsider,json={'code':team['code']}).status_code==404
    history=client.get('/api/team-applications',headers=outsider).json()[0]
    assert history['team_deleted'] is True and history['status']=='cancelled'
    with Session(engine) as db:
        archived=db.get(Team,UUID(team['id']))
        assert not archived.is_active and archived.deleted_at
        assert db.scalar(select(func.count()).select_from(TeamMembership).where(TeamMembership.team_id==archived.id))==0
        pids=db.scalars(select(Project.id).where(Project.team_id==archived.id)).all()
        assert db.scalar(select(func.count()).select_from(Membership).where(Membership.project_id.in_(pids)))==0
        assert db.get(Document,UUID(ready['document_id'])) is not None
        version=db.get(DocumentVersion,UUID(ready['version_id']))
        assert storage_path(client.app.state.settings.storage_dir,version.storage_key).exists()
        for record in [processing,queued]:
            task=db.get(IngestionTask,UUID(record['task_id']))
            assert task.status=='cancelled' and task.lease_token is None
            assert db.get(DocumentVersion,UUID(record['version_id'])).status=='cancelled'
        assert db.get(TeamApplication,UUID(application['id'])).review_note=='团队已删除，申请自动关闭。'
        assert db.scalar(select(AuditEvent).where(AuditEvent.action=='team.deleted',AuditEvent.target_id==team['id'])) is not None
        assert db.get(User,UUID(ids['owner'])).is_active
    # Unrelated original team/project remains available to both identities.
    assert client.get('/api/projects/'+ids['project'],headers=member).status_code==200
    assert delete(client,team,owner).status_code==404


def test_delete_during_embedding_prevents_publication(environment):
    client,engine,_=environment
    owner=login(client);team=create(client,owner)
    record=upload(client,{'project':team['library_project_id']},owner).json()
    class DeletingEmbedding(FakeEmbedding):
        def embed(self,texts):
            assert delete(client,team,owner).status_code==204
            return super().embed(texts)
    worker_for(client,engine,DeletingEmbedding()).run_once()
    with Session(engine) as db:
        assert db.get(IngestionTask,UUID(record['task_id'])).status=='cancelled'
        assert db.get(DocumentVersion,UUID(record['version_id'])).status=='cancelled'


def test_delete_during_generation_discards_answer(environment):
    client,engine,_=environment
    owner=login(client);team=create(client,owner)
    upload(client,{'project':team['library_project_id']},owner,text='membership controls access to project documents')
    worker_for(client,engine).run_once()
    client.app.state.embedding_client=FakeEmbedding();client.app.state.rerank_client=FakeRerank();chat=FakeChat();client.app.state.chat_client=chat
    chat.hook=lambda:delete(client,team,owner).raise_for_status()
    response=client.post('/api/questions',headers=owner,json={'project_id':team['library_project_id'],'question':'membership','retrieval_mode':'keyword','allow_external_processing':True})
    assert response.status_code==404


def test_inactive_team_denied_even_if_stale_memberships_exist(environment):
    client,engine,ids=environment
    owner,member=login(client),login(client,'member')
    with Session(engine) as db,db.begin():
        db.get(Team,UUID(ids['team'])).is_active=False
    assert client.get('/api/projects/'+ids['project'],headers=member).status_code==404
    assert client.get('/api/projects',headers=owner).json()==[]
    assert client.post('/api/search',headers=member,json={'project_id':ids['project'],'query':'membership','mode':'keyword'}).status_code==404


def test_concurrent_deletion_has_one_audit_event(environment):
    client,engine,_=environment
    owner=login(client);team=create(client,owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:delete(client,team,owner),range(2)))
    assert sorted(r.status_code for r in responses)==[204,404]
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action=='team.deleted',AuditEvent.target_id==team['id']))==1
