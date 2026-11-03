import re
import uuid
from dataclasses import replace

from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from backend.app.config import ROOT, Settings
from backend.app.main import create_app
from backend.app.models import Membership, Project, Team, TeamMembership, User
from backend.app.security import hash_password


@pytest.fixture(scope='session')
def hashes():
    return hash_password('test-password-only')


@pytest.fixture
def environment(hashes, tmp_path):
    settings = replace(Settings.load(), storage_dir=tmp_path / 'storage')
    schema = 'test_rag_' + uuid.uuid4().hex
    assert re.fullmatch(r'test_rag_[0-9a-f]{32}', schema)
    control = create_engine(settings.database_url, hide_parameters=True)
    with control.begin() as conn:
        conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    # Do not include public: Alembic must never see the application's version table.
    engine = create_engine(settings.database_url, hide_parameters=True, connect_args={'options': f'-csearch_path={schema}'})
    try:
        migration = Config(str(ROOT / 'alembic.ini'))
        with engine.begin() as conn:
            migration.attributes['connection'] = conn
            command.upgrade(migration, 'head')
            assert conn.execute(text('SELECT current_schema()')).scalar_one() == schema
            assert conn.execute(text("SELECT count(*) FROM information_schema.tables WHERE table_schema=:schema AND table_name IN ('app_users','app_sessions','projects','memberships')"), {'schema': schema}).scalar_one() == 4
        with Session(engine) as db, db.begin():
            users = [User(username=n, display_name=n, password_hash=hashes) for n in ['owner', 'member', 'outsider']]
            db.add_all(users)
            db.flush()
            team=Team(name='Protected team',code='PROTECTED001')
            other=Team(name='Other team',code='OTHERTEAM001')
            db.add_all([team,other]);db.flush()
            db.add_all([TeamMembership(team_id=team.id,user_id=users[0].id,role='owner'),TeamMembership(team_id=team.id,user_id=users[1].id,role='member'),TeamMembership(team_id=other.id,user_id=users[2].id,role='owner')])
            users[0].academic_identity='teacher';users[0].teacher_verified=True
            project = Project(name='Protected project',team_id=team.id)
            db.add(project)
            db.flush()
            public=Project(name='Public team library',team_id=team.id,is_team_library=True)
            other_public=Project(name='Other public library',team_id=other.id,is_team_library=True)
            db.add_all([public,other_public]);db.flush()
            db.add_all([Membership(project_id=public.id,user_id=users[0].id,role='admin'),Membership(project_id=public.id,user_id=users[1].id,role='member'),Membership(project_id=other_public.id,user_id=users[2].id,role='admin')])
            db.add_all([Membership(project_id=project.id, user_id=users[0].id, role='admin'), Membership(project_id=project.id, user_id=users[1].id, role='member')])
            ids = {u.username: str(u.id) for u in users}
            ids['project'] = str(project.id)
            ids['team']=str(team.id)
            ids['library']=str(public.id)
        app = create_app(settings, engine)
        with TestClient(app) as client:
            yield client, engine, ids
    finally:
        engine.dispose()
        # Only the randomly generated schema created by this fixture is removed.
        with control.begin() as conn:
            conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        control.dispose()


def login(client, username='owner'):
    response = client.post('/api/auth/login', json={'username': username, 'password': 'test-password-only'})
    assert response.status_code == 200, response.text
    return {'Authorization': 'Bearer ' + response.json()['access_token']}
