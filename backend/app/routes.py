from datetime import timedelta
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError

from .dependencies import CurrentUser, Db, bearer, require_membership
from .models import LoginSession, Membership, Project, User, utcnow
from .schemas import ContactUserOutput, LoginInput, LoginOutput, MemberInput, MemberOutput, ProfileInput, ProjectInput, ProjectOutput, ProjectUpdate, RegisterInput, UserOutput, normalize_username
from .security import DUMMY_HASH, hash_password, token_digest, verify_password
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
    return LoginOutput(access_token=token, expires_at=expires, user=ContactUserOutput.model_validate(user))


@router.post('/auth/register',response_model=ContactUserOutput,status_code=201)
def register(data:RegisterInput,db:Db):
    user=User(username=data.username,display_name=data.username,qq_email=data.qq_email,password_hash=hash_password(data.password))
    db.add(user)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'用户名或QQ邮箱已被使用，请更换后重试。') from None
    return user


@router.get('/auth/me', response_model=ContactUserOutput)
def me(user: CurrentUser):
    return user


@router.patch('/auth/me',response_model=ContactUserOutput)
def update_profile(data:ProfileInput,user:CurrentUser,db:Db):
    user.display_name,user.qq_email=data.display_name,data.qq_email
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409,'该QQ邮箱已被使用，请检查后重试。') from None
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


@router.patch('/projects/{project_id}',response_model=ProjectOutput)
def update_project(project_id:UUID,data:ProjectUpdate,user:CurrentUser,db:Db,request:Request):
    project,membership=require_membership(db,project_id,user.id,admin=True,lock=True)
    for field,value in data.model_dump(exclude_unset=True).items():
        setattr(project,field,value)
    record_event(db,project_id,user.id,'project.updated',project_id,request.state.trace_id)
    db.commit()
    return project_output(project,membership)


@router.get('/projects/{project_id}/members', response_model=list[MemberOutput])
def members(project_id: UUID, user: CurrentUser, db: Db):
    require_membership(db, project_id, user.id)
    rows = db.execute(select(User, Membership.role).join(Membership, User.id == Membership.user_id).where(Membership.project_id == project_id).order_by(User.username))
    return [MemberOutput(user=ContactUserOutput.model_validate(u), role=role) for u, role in rows]


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
    return MemberOutput(user=ContactUserOutput.model_validate(target_user), role=data.role)


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
    try:
        username=normalize_username(username)
    except ValueError:
        raise HTTPException(422,'用户名格式不正确。') from None
    return db.scalars(select(User).where(User.username == username, User.is_active.is_(True))).all()
