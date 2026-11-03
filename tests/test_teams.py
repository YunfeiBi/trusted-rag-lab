from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import select,text
from sqlalchemy.orm import Session

from backend.app.config import ROOT
from backend.app.models import Membership,QuestionRecord,TeamMembership,User
from tests.conftest import login
from tests.test_documents import FakeEmbedding,upload,worker_for
from tests.test_search import FakeRerank
from tests.test_questions import FakeChat


def create(client,owner):
    response=client.post('/api/teams',headers=owner,json={'name':'合成实验团队','description':'仅测试组织权限'})
    assert response.status_code==201,response.text
    return response.json()


def join(client,team,owner,member):
    response=client.post('/api/team-applications',headers=member,json={'code':team['code'],'message':'合成申请'})
    assert response.status_code==201,response.text
    result=client.post(f"/api/teams/{team['id']}/applications/{response.json()['id']}/review",headers=owner,json={'decision':'approved'})
    assert result.status_code==200,result.text


def test_only_verified_teacher_can_create_no_self_elevation(environment):
    client,_,_=environment
    student=login(client,'member')
    assert client.post('/api/teams',headers=student,json={'name':'student team'}).status_code==403
    requested=client.patch('/api/auth/me',headers=student,json={'display_name':'申请教师身份','qq_email':'student.request@qq.com','academic_identity':'teacher'})
    assert requested.status_code==200 and requested.json()['teacher_verified'] is False
    assert client.post('/api/teams',headers=student,json={'name':'still not verified'}).status_code==403
    response=client.post('/api/auth/register',json={'username':'待确认教师','password':'test-password-only','qq_email':'pending.teacher@qq.com','academic_identity':'teacher'})
    assert response.status_code==201 and response.json()['teacher_verified'] is False
    teacher=login(client,'待确认教师')
    assert client.post('/api/teams',headers=teacher,json={'name':'spoofed'}).status_code==403
    assert client.patch('/api/auth/me',headers=teacher,json={'display_name':'教师','qq_email':'pending.teacher@qq.com','teacher_verified':True}).status_code==422
    assert client.post('/api/auth/register',json={'username':'冒用教师','password':'test-password-only','qq_email':'spoof@qq.com','academic_identity':'teacher','teacher_verified':True}).status_code==422
    assert client.post('/api/projects',headers=teacher,json={'name':'not approved'}).status_code==422
    assert client.get('/api/projects',headers=teacher).json()==[]
    assert create(client,login(client))['role']=='owner'


def test_approval_public_library_and_private_project_boundary(environment):
    client,engine,ids=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    team=create(client,owner)
    application=client.post('/api/team-applications',headers=member,json={'code':team['code']}).json()
    assert client.get('/api/teams/'+team['id']+'/members',headers=member).status_code==404
    assert client.get('/api/projects/'+team['library_project_id'],headers=member).status_code==404
    assert client.get('/api/teams/'+team['id']+'/applications',headers=outsider).status_code==404
    review=f"/api/teams/{team['id']}/applications/{application['id']}/review"
    assert client.post(review,headers=member,json={'decision':'approved'}).status_code==404
    assert client.post(review,headers=owner,json={'decision':'rejected','note':'请补充信息'}).status_code==200
    assert client.get('/api/team-applications',headers=member).json()[0]['review_note']=='请补充信息'
    join(client,team,owner,member)
    assert client.get('/api/projects/'+team['library_project_id'],headers=member).status_code==200
    public=upload(client,{'project':team['library_project_id']},owner).json()
    worker_for(client,engine).run_once()
    assert client.get('/api/documents/'+public['document_id']+'/source',headers=member).status_code==200
    project=client.post('/api/projects',headers=owner,json={'name':'内部项目','team_id':team['id']}).json()
    assert client.get('/api/projects/'+project['id'],headers=member).status_code==404
    assert client.put('/api/projects/'+project['id']+'/members/'+ids['outsider'],headers=owner,json={'role':'member'}).status_code==409
    assert client.put('/api/projects/'+project['id']+'/members/'+ids['member'],headers=owner,json={'role':'member'}).status_code==200
    assert client.get('/api/projects/'+project['id'],headers=member).status_code==200
    assert client.patch('/api/projects/'+team['library_project_id'],headers=owner,json={'name':'rename'}).status_code==409
    assert client.delete('/api/projects/'+team['library_project_id']+'/members/'+ids['member'],headers=owner).status_code==409


def test_concurrent_applications_and_review_are_single_grant(environment):
    client,engine,ids=environment
    owner,member=login(client),login(client,'member')
    team=create(client,owner)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post('/api/team-applications',headers=member,json={'code':team['code']}),range(2)))
    assert sorted(r.status_code for r in responses)==[201,409]
    application=next(r.json() for r in responses if r.status_code==201)
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses=list(pool.map(lambda _:client.post(f"/api/teams/{team['id']}/applications/{application['id']}/review",headers=owner,json={'decision':'approved'}),range(2)))
    assert sorted(r.status_code for r in responses)==[200,409]
    with Session(engine) as db:
        assert db.get(TeamMembership,(UUID(team['id']),UUID(ids['member']))).role=='member'


def test_cancellation_and_request_privacy(environment):
    client,_,_=environment
    owner,member,outsider=login(client),login(client,'member'),login(client,'outsider')
    team=create(client,owner)
    application=client.post('/api/team-applications',headers=member,json={'code':team['code']}).json()
    assert client.delete('/api/team-applications/'+application['id'],headers=outsider).status_code==404
    assert client.get('/api/team-applications',headers=outsider).json()==[]
    assert client.delete('/api/team-applications/'+application['id'],headers=member).status_code==204
    assert client.post(f"/api/teams/{team['id']}/applications/{application['id']}/review",headers=owner,json={'decision':'approved'}).status_code==409


def test_team_removal_revokes_source_search_qa_history_and_rejoin_does_not_restore_projects(environment):
    client,engine,ids=environment
    owner,member=login(client),login(client,'member')
    team=create(client,owner);join(client,team,owner,member)
    project=client.post('/api/projects',headers=owner,json={'name':'私有资料','team_id':team['id']}).json()
    path='/api/projects/'+project['id']
    client.put(path+'/members/'+ids['member'],headers=owner,json={'role':'member'})
    doc=upload(client,{'project':project['id']},owner).json();worker_for(client,engine).run_once()
    client.app.state.embedding_client=FakeEmbedding();client.app.state.rerank_client=FakeRerank();client.app.state.chat_client=FakeChat()
    question=client.post('/api/questions',headers=member,json={'project_id':project['id'],'question':'membership','retrieval_mode':'keyword','allow_external_processing':True})
    assert question.status_code==200
    assert client.delete('/api/teams/'+team['id']+'/members/'+ids['member'],headers=owner).status_code==204
    for endpoint in [path+'/members','/api/documents/'+doc['document_id']+'/source','/api/questions/'+question.json()['id']]:
        assert client.get(endpoint,headers=member).status_code==404
    assert client.post('/api/search',headers=member,json={'project_id':project['id'],'query':'membership','mode':'keyword'}).status_code==404
    join(client,team,owner,member)
    assert client.get('/api/projects/'+team['library_project_id'],headers=member).status_code==200
    assert client.get(path,headers=member).status_code==404
    with Session(engine) as db:
        assert db.get(Membership,(UUID(project['id']),UUID(ids['member']))) is None


def test_role_delegation_owner_transfer_and_last_project_admin_protection(environment):
    client,engine,ids=environment
    owner,member,other=login(client),login(client,'member'),login(client,'outsider')
    team=create(client,owner);join(client,team,owner,member);join(client,team,owner,other)
    assert client.post('/api/teams/'+team['id']+'/leave',headers=owner).status_code==409
    assert client.post('/api/teams/'+team['id']+'/transfer/'+ids['member'],headers=owner).status_code==409
    assert client.put('/api/teams/'+team['id']+'/members/'+ids['member'],headers=owner,json={'role':'admin'}).status_code==200
    assert client.put('/api/teams/'+team['id']+'/members/'+ids['outsider'],headers=member,json={'role':'admin'}).status_code==403
    project=client.post('/api/projects',headers=member,json={'name':'学生维护项目','team_id':team['id']}).json()
    assert client.post('/api/teams/'+team['id']+'/leave',headers=member).status_code==409
    assert client.put('/api/projects/'+project['id']+'/members/'+ids['owner'],headers=member,json={'role':'admin'}).status_code==200
    assert client.post('/api/teams/'+team['id']+'/leave',headers=member).status_code==204
    with Session(engine) as db,db.begin():
        user=db.get(User,UUID(ids['outsider']));user.academic_identity='teacher';user.teacher_verified=True
    assert client.post('/api/teams/'+team['id']+'/transfer/'+ids['outsider'],headers=owner).status_code==200
    assert client.put('/api/projects/'+project['id']+'/members/'+ids['outsider'],headers=owner,json={'role':'admin'}).status_code==200
    assert client.post('/api/teams/'+team['id']+'/leave',headers=owner).status_code==204
    assert client.get('/api/projects/'+project['id'],headers=owner).status_code==404


def test_nonempty_migration_preserves_private_grants_without_claiming_teacher_identity(environment):
    client,engine,_=environment
    config=Config(str(ROOT/'alembic.ini'))
    with engine.begin() as conn:
        config.attributes['connection']=conn
        command.downgrade(config,'0006_accounts')
        before=conn.execute(text('SELECT id FROM projects')).scalars().all()
        grants=conn.execute(text('SELECT project_id,user_id,role FROM memberships')).all()
        command.upgrade(config,'head')
        after=conn.execute(text('SELECT id FROM projects WHERE NOT is_team_library')).scalars().all()
        assert set(before)==set(after)
        for project,user,role in grants:
            assert conn.execute(text('SELECT role FROM memberships WHERE project_id=:project AND user_id=:user'),{'project':project,'user':user}).scalar_one()==role
        assert conn.execute(text('SELECT count(*) FROM teams')).scalar_one()==len(before)
        assert conn.execute(text('SELECT count(*) FROM app_users WHERE teacher_verified')).scalar_one()==0
    assert client.get('/api/health/ready').status_code==200


def test_stale_project_grant_without_team_membership_is_denied(environment):
    client,engine,ids=environment
    owner,member=login(client),login(client,'member')
    doc=upload(client,ids,owner).json();worker_for(client,engine).run_once()
    with Session(engine) as db,db.begin():
        db.delete(db.get(TeamMembership,(UUID(ids['team']),UUID(ids['member']))))
        assert db.get(Membership,(UUID(ids['project']),UUID(ids['member']))) is not None
    assert client.get('/api/documents/'+doc['document_id']+'/source',headers=member).status_code==404
    assert client.get('/api/projects',headers=member).json()==[]
    assert client.post('/api/search',headers=member,json={'project_id':ids['project'],'query':'membership','mode':'keyword'}).status_code==404


def test_team_revoked_during_generation_discards_answer(environment):
    client,engine,ids=environment
    owner,member=login(client),login(client,'member')
    upload(client,ids,owner);worker_for(client,engine).run_once()
    client.app.state.embedding_client=FakeEmbedding();client.app.state.rerank_client=FakeRerank();chat=FakeChat();client.app.state.chat_client=chat
    def revoke():
        with Session(engine) as db,db.begin():
            db.delete(db.get(TeamMembership,(UUID(ids['team']),UUID(ids['member']))))
    chat.hook=revoke
    response=client.post('/api/questions',headers=member,json={'project_id':ids['project'],'question':'membership','retrieval_mode':'keyword','allow_external_processing':True})
    assert response.status_code==404
    with Session(engine) as db:
        assert db.scalar(select(QuestionRecord)) is None
