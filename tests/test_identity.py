from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.app.config import ROOT
from backend.app.models import LoginSession, User, utcnow
from backend.app.security import token_digest
from tests.conftest import login


def test_unauthenticated_and_no_password_echo(environment):
    client, _, _ = environment
    assert client.get('/api/projects').status_code == 401
    result = client.post('/api/auth/login', json={'username': 'owner', 'password': 'do-not-echo', 'user_id': 'spoof'})
    assert result.status_code == 422
    assert 'do-not-echo' not in result.text
    assert result.json()['trace_id'] == result.headers['x-request-id']
    assert result.headers['cache-control'] == 'no-store'


def test_login_logout_and_hashed_session(environment):
    client, engine, _ = environment
    assert client.post('/api/auth/login', json={'username': 'owner', 'password': 'wrong'}).status_code == 401
    headers = login(client)
    assert client.get('/api/auth/me', headers=headers).json()['username'] == 'owner'
    with Session(engine) as db:
        stored = db.scalars(select(LoginSession)).one()
        assert stored.token_hash == token_digest(headers['Authorization'].split()[1])
        assert headers['Authorization'].split()[1] != stored.token_hash
        assert db.scalar(select(User.password_hash).where(User.username == 'owner')) != 'test-password-only'
    assert client.post('/api/auth/logout', headers=headers).status_code == 204
    assert client.get('/api/auth/me', headers=headers).status_code == 401


def test_expired_and_disabled_sessions(environment):
    client, engine, ids = environment
    headers = login(client)
    with Session(engine) as db, db.begin():
        db.get(LoginSession, token_digest(headers['Authorization'].split()[1])).expires_at = utcnow() - timedelta(seconds=1)
    assert client.get('/api/projects', headers=headers).status_code == 401
    headers = login(client)
    with Session(engine) as db, db.begin():
        db.get(User, UUID(ids['owner'])).is_active = False
    assert client.get('/api/projects', headers=headers).status_code == 401
    assert client.post('/api/auth/login', json={'username': 'owner', 'password': 'test-password-only'}).status_code == 401


def test_project_scope_and_member_management_denial(environment):
    client, _, ids = environment
    outsider = login(client, 'outsider')
    member = login(client, 'member')
    path = '/api/projects/' + ids['project']
    assert client.get('/api/projects', headers=outsider).json() == []
    assert client.get(path, headers=outsider).status_code == 404
    assert client.get(path, headers=member).json()['role'] == 'member'
    assert client.get(path+'/members', headers=member).status_code == 403
    assert client.put(path+'/members/'+ids['outsider'], headers=member, json={'role': 'admin'}).status_code == 403
    assert client.get('/api/users?username=owner', headers=member).status_code == 403


def test_revocation_applies_to_existing_session(environment):
    client, _, ids = environment
    owner, member = login(client), login(client, 'member')
    path = '/api/projects/' + ids['project']
    assert client.delete(path+'/members/'+ids['member'], headers=owner).status_code == 204
    assert client.get(path, headers=member).status_code == 404
    assert client.get('/api/projects', headers=member).json() == []
    assert client.put(path+'/members/'+ids['member'], headers=owner, json={'role': 'member'}).status_code == 200
    assert client.get(path, headers=member).status_code == 200


def test_last_admin_protection_and_transfer(environment):
    client, _, ids = environment
    owner, member = login(client), login(client, 'member')
    path = '/api/projects/'+ids['project']+'/members/'
    assert client.delete(path+ids['owner'], headers=owner).status_code == 409
    assert client.put(path+ids['owner'], headers=owner, json={'role': 'member'}).status_code == 409
    assert client.put(path+ids['member'], headers=owner, json={'role': 'admin'}).status_code == 200
    assert client.delete(path+ids['owner'], headers=member).status_code == 204
    assert client.get('/api/projects/'+ids['project'], headers=owner).status_code == 404


def test_concurrent_admin_removal_keeps_one_admin(environment):
    client, _, ids = environment
    owner, member = login(client), login(client, 'member')
    path = '/api/projects/'+ids['project']+'/members/'
    assert client.put(path+ids['member'], headers=owner, json={'role': 'admin'}).status_code == 200
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.delete, path+ids[name], headers=headers) for name, headers in [('owner', owner), ('member', member)]]
        statuses = sorted(f.result().status_code for f in futures)
    assert statuses == [204, 409]


def test_project_creation_and_input_constraints(environment):
    client, _, _ = environment
    headers = login(client, 'outsider')
    assert client.post('/api/projects', headers=headers, json={'name': '   '}).status_code == 422
    created = client.post('/api/projects', headers=headers, json={'name': 'New project', 'description': 'owned'})
    assert created.status_code == 201
    assert created.json()['role'] == 'admin'
    members = client.get('/api/projects/'+created.json()['id']+'/members', headers=headers).json()
    assert len(members) == 1 and members[0]['user']['username'] == 'outsider'


def test_migration_round_trip_and_readiness(environment):
    client, engine, _ = environment
    assert client.get('/api/health/ready').status_code == 200
    config = Config(str(ROOT / 'alembic.ini'))
    with engine.begin() as conn:
        config.attributes['connection'] = conn
        command.downgrade(config, 'base')
        assert conn.execute(text("SELECT to_regclass(current_schema() || '.app_users')")).scalar() is None
        command.upgrade(config, 'head')
    assert client.get('/api/health/ready').status_code == 200
