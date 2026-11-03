from datetime import timedelta
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select, text

from .dependencies import CurrentUser, Db, bearer, require_membership
from .models import LoginSession, Membership, Project, User, utcnow
from .schemas import LoginInput, LoginOutput, MemberInput, MemberOutput, ProjectInput, ProjectOutput, UserOutput
from .security import DUMMY_HASH, token_digest, verify_password
from .audit import record_event

router = APIRouter(prefix='/api')


@router.get('/health/live')
def live():
    return {'status': 'ok'}


@router.get('/health/ready')
def ready(db: Db, request: Request):
    revision = db.execute(text('SELECT version_num FROM alembic_version')).scalar_one()
    if revision != request.app.state.migration_head:
        raise HTTPException(503, '数据库迁移版本不匹配')
    return {'status': 'ok', 'migration': revision}


@router.post('/auth/login', response_model=LoginOutput)
def login(data: LoginInput, db: Db, request: Request):
    user = db.scalar(select(User).where(User.username == data.username))
    valid = verify_password(data.password, user.password_hash if user else DUMMY_HASH)
    if not valid or user is None or not user.is_active:
        raise HTTPException(401, '用户名或密码错误')
    token = secrets.token_urlsafe(32)
    expires = utcnow() + timedelta(hours=request.app.state.settings.session_hours)
    db.add(LoginSession(token_hash=token_digest(token), user_id=user.id, expires_at=expires))
    db.commit()
    return LoginOutput(access_token=token, expires_at=expires, user=UserOutput.model_validate(user))


@router.get('/auth/me', response_model=UserOutput)
def me(user: CurrentUser):
    return user


@router.post('/auth/logout', status_code=204)
def logout(user: CurrentUser, db: Db, credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer)]):
    db.delete(db.get(LoginSession, token_digest(credentials.credentials)))
    db.commit()
    return Response(status_code=204)


def project_output(project, membership):
    return ProjectOutput(id=project.id, name=project.name, description=project.description, role=membership.role)


@router.get('/projects', response_model=list[ProjectOutput])
def list_projects(user: CurrentUser, db: Db):
    rows = db.execute(select(Project, Membership).join(Membership).where(Membership.user_id == user.id).order_by(Project.created_at))
    return [project_output(p, m) for p, m in rows]


@router.post('/projects', response_model=ProjectOutput, status_code=201)
def create_project(data: ProjectInput, user: CurrentUser, db: Db, request:Request):
    project = Project(**data.model_dump())
    db.add(project)
    db.flush()
    membership = Membership(project_id=project.id, user_id=user.id, role='admin')
    db.add(membership)
    record_event(db,project.id,user.id,'project.created',project.id,request.state.trace_id)
    db.commit()
    return project_output(project, membership)


@router.get('/projects/{project_id}', response_model=ProjectOutput)
def read_project(project_id: UUID, user: CurrentUser, db: Db):
    project, membership = require_membership(db, project_id, user.id)
    return project_output(project, membership)


@router.get('/projects/{project_id}/members', response_model=list[MemberOutput])
def members(project_id: UUID, user: CurrentUser, db: Db):
    require_membership(db, project_id, user.id, admin=True)
    rows = db.execute(select(User, Membership.role).join(Membership, User.id == Membership.user_id).where(Membership.project_id == project_id).order_by(User.username))
    return [MemberOutput(user=UserOutput.model_validate(u), role=role) for u, role in rows]


def protect_last_admin(db, project_id, target, new_role):
    if target and target.role == 'admin' and new_role != 'admin':
        count = db.scalar(select(func.count()).select_from(Membership).where(Membership.project_id == project_id, Membership.role == 'admin'))
        if count <= 1:
            raise HTTPException(409, '不能撤销或降级最后一位项目管理员')


@router.put('/projects/{project_id}/members/{user_id}', response_model=MemberOutput)
def put_member(project_id: UUID, user_id: UUID, data: MemberInput, user: CurrentUser, db: Db, request:Request):
    require_membership(db, project_id, user.id, admin=True, lock=True)
    target_user = db.get(User, user_id)
    if not target_user or not target_user.is_active:
        raise HTTPException(404, '用户不存在或已禁用')
    membership = db.get(Membership, (project_id, user_id))
    protect_last_admin(db, project_id, membership, data.role)
    if membership:
        membership.role = data.role
    else:
        db.add(Membership(project_id=project_id, user_id=user_id, role=data.role))
    record_event(db,project_id,user.id,'member.updated',user_id,request.state.trace_id,{'role':data.role})
    db.commit()
    return MemberOutput(user=UserOutput.model_validate(target_user), role=data.role)


@router.delete('/projects/{project_id}/members/{user_id}', status_code=204)
def delete_member(project_id: UUID, user_id: UUID, user: CurrentUser, db: Db, request:Request):
    require_membership(db, project_id, user.id, admin=True, lock=True)
    target = db.get(Membership, (project_id, user_id))
    if target is None:
        raise HTTPException(404, '成员不存在')
    protect_last_admin(db, project_id, target, None)
    db.delete(target)
    record_event(db,project_id,user.id,'member.removed',user_id,request.state.trace_id)
    db.commit()
    return Response(status_code=204)


@router.get('/users', response_model=list[UserOutput])
def find_user(user: CurrentUser, db: Db, username: str = Query(min_length=1, max_length=64)):
    manages_project = db.scalar(select(Membership.project_id).where(Membership.user_id == user.id, Membership.role == 'admin').limit(1))
    if manages_project is None:
        raise HTTPException(403, '需要项目管理员权限')
    return db.scalars(select(User).where(User.username == username, User.is_active.is_(True))).all()
