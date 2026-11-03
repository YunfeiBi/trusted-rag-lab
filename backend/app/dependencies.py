from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LoginSession, Membership, Project, Team, TeamMembership, User, utcnow
from .security import token_digest

bearer = HTTPBearer(auto_error=False)


def get_db(request: Request):
    with request.app.state.session_factory() as session:
        yield session


Db = Annotated[Session, Depends(get_db)]


def current_user(db: Db, credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]):
    if credentials is None:
        raise HTTPException(401, '请先登录', headers={'WWW-Authenticate': 'Bearer'})
    login = db.get(LoginSession, token_digest(credentials.credentials))
    if login is None or login.expires_at <= utcnow():
        raise HTTPException(401, '会话无效或已过期', headers={'WWW-Authenticate': 'Bearer'})
    user = db.get(User, login.user_id)
    if user is None or not user.is_active:
        raise HTTPException(401, '会话无效或已过期')
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require_team(db,team_id,user_id,*,admin=False,owner=False,lock=False):
    query=select(Team).where(Team.id==team_id)
    if lock:
        query=query.with_for_update().execution_options(populate_existing=True)
    team=db.scalar(query)
    member=db.scalar(select(TeamMembership).where(TeamMembership.team_id==team_id,TeamMembership.user_id==user_id).execution_options(populate_existing=True)) if team else None
    if member is None:
        raise HTTPException(404,'团队不存在或你尚未获得团队访问权限')
    if owner and member.role!='owner':
        raise HTTPException(403,'需要团队所有者权限')
    if admin and member.role not in {'owner','admin'}:
        raise HTTPException(403,'需要团队管理员权限')
    return team,member


def require_membership(db: Session, project_id: UUID, user_id: UUID, admin=False, lock=False):
    # Mutations lock the project before reading membership to serialize admin changes.
    snapshot=db.get(Project,project_id)
    if snapshot is None:
        raise HTTPException(404,'项目不存在或不可访问')
    require_team(db,snapshot.team_id,user_id,lock=lock)
    query = select(Project).where(Project.id == project_id)
    if lock:
        query = query.with_for_update()
    project = db.scalar(query)
    membership = db.get(Membership, (project_id, user_id)) if project else None
    if membership is None:
        raise HTTPException(404, '项目不存在或不可访问')
    if admin and membership.role != 'admin':
        raise HTTPException(403, '需要项目管理员权限')
    return project, membership
