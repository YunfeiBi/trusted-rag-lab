from typing import Annotated
from uuid import UUID

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LoginSession, Membership, Project, User, utcnow
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


def require_membership(db: Session, project_id: UUID, user_id: UUID, admin=False, lock=False):
    # Mutations lock the project before reading membership to serialize admin changes.
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
