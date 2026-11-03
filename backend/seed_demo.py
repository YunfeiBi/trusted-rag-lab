"""Create local demo accounts without printing passwords or resetting existing users."""
import json
import secrets
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from backend.app.config import ROOT, Settings
from backend.app.models import Membership, Project, Team, TeamMembership, User
from backend.app.security import hash_password


def main():
    destination = ROOT / '.local' / 'demo_accounts.json'
    destination.parent.mkdir(exist_ok=True)
    records = json.loads(destination.read_text(encoding='utf-8')) if destination.exists() else {'accounts': [], 'projects': {}}
    account_records = {a['username']: a for a in records['accounts']}
    definitions = [('demo_admin', '演示管理员'), ('demo_member', '项目成员'), ('demo_other', '另一项目管理员')]
    engine = create_engine(Settings.load().database_url, hide_parameters=True)
    with Session(engine) as db, db.begin():
        users = {}
        for username, display_name in definitions:
            user = db.scalar(select(User).where(User.username == username))
            if user is None:
                password = secrets.token_urlsafe(18)
                user = User(username=username, display_name=display_name, password_hash=hash_password(password))
                db.add(user)
                db.flush()
                account_records[username] = {'username': username, 'password': password, 'display_name': display_name}
            elif username not in account_records:
                raise SystemExit('Existing demo account has no local password record. No passwords were reset.')
            users[username] = user
        for label, name, owner in [('a', '科研项目 A', 'demo_admin'), ('b', '独立项目 B', 'demo_other')]:
            project_id = records['projects'].get(label)
            project = db.get(Project, uuid.UUID(project_id)) if project_id else None
            if project is None:
                team=Team(name=name+'（演示团队）',description='初始化合成演示团队，不代表教师身份确认。',code=secrets.token_hex(6).upper())
                db.add(team);db.flush()
                db.add(TeamMembership(team_id=team.id,user_id=users[owner].id,role='owner'))
                if label=='a':
                    db.add(TeamMembership(team_id=team.id,user_id=users['demo_member'].id,role='member'))
                public=Project(name='团队公共资料',description='演示团队公共资料',team_id=team.id,is_team_library=True)
                db.add(public);db.flush()
                db.add(Membership(project_id=public.id,user_id=users[owner].id,role='admin'))
                if label=='a':
                    db.add(Membership(project_id=public.id,user_id=users['demo_member'].id,role='member'))
                project = Project(name=name, description='课程本机演示项目，当前为基础权限功能',team_id=team.id)
                db.add(project)
                db.flush()
                db.add(Membership(project_id=project.id, user_id=users[owner].id, role='admin'))
                if label == 'a':
                    db.add(Membership(project_id=project.id, user_id=users['demo_member'].id, role='member'))
                records['projects'][label] = str(project.id)
        # Write credentials before commit; retries retain the record even if commit fails.
        records['accounts'] = list(account_records.values())
        temporary = destination.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding='utf-8')
        temporary.replace(destination)
    engine.dispose()
    print('Demo accounts ready. Passwords are only in .local/demo_accounts.json (Git ignored).')


if __name__ == '__main__':
    main()
