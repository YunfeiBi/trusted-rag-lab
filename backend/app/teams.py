"""Approved team membership is required in addition to project membership."""
import secrets
from typing import Literal
from uuid import UUID

from fastapi import APIRouter,HTTPException,Query,Request
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import delete,select
from sqlalchemy.exc import IntegrityError

from .audit import record_event
from .dependencies import CurrentUser,Db,require_team
from .models import Membership,Project,Team,TeamApplication,TeamMembership,User,utcnow
from .schemas import ContactUserOutput

router=APIRouter(prefix='/api')


class TeamInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name:str=Field(min_length=1,max_length=100)
    description:str=Field(default='',max_length=2000)


class ApplicationInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    code:str=Field(min_length=1,max_length=16)
    message:str=Field(default='',max_length=1000)


class ReviewInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    decision:Literal['approved','rejected']
    note:str=Field(default='',max_length=500)


class TeamRoleInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    role:Literal['admin','member']


def library(db,team_id):
    project=db.scalar(select(Project).where(Project.team_id==team_id,Project.is_team_library.is_(True)))
    if not project:
        raise HTTPException(409,'团队公共资料库尚未初始化')
    return project


def team_output(db,team,member):
    public=library(db,team.id)
    return {'id':str(team.id),'name':team.name,'description':team.description,'code':team.code,'role':member.role,'library_project_id':str(public.id)}


@router.get('/teams')
def teams(user:CurrentUser,db:Db):
    rows=db.execute(select(Team,TeamMembership).join(TeamMembership).where(TeamMembership.user_id==user.id).order_by(Team.created_at,Team.id))
    return [team_output(db,t,m) for t,m in rows]


@router.post('/teams',status_code=201)
def create_team(data:TeamInput,user:CurrentUser,db:Db,request:Request):
    if not user.teacher_verified or user.academic_identity!='teacher':
        raise HTTPException(403,'只有已确认教师身份的账号可以创建团队，学生及待确认教师不能创建。')
    team=Team(name=data.name,description=data.description,code=secrets.token_hex(6).upper())
    db.add(team);db.flush()
    member=TeamMembership(team_id=team.id,user_id=user.id,role='owner')
    db.add(member)
    public=Project(name='团队公共资料',description='团队全体已批准成员可访问的公共资料库。',team_id=team.id,is_team_library=True)
    db.add(public);db.flush()
    db.add(Membership(project_id=public.id,user_id=user.id,role='admin'))
    record_event(db,public.id,user.id,'team.created',team.id,request.state.trace_id)
    db.commit()
    return team_output(db,team,member)


@router.patch('/teams/{team_id}')
def update_team(team_id:UUID,data:TeamInput,user:CurrentUser,db:Db,request:Request):
    team,member=require_team(db,team_id,user.id,admin=True,lock=True)
    team.name,team.description=data.name,data.description
    record_event(db,library(db,team_id).id,user.id,'team.updated',team_id,request.state.trace_id)
    db.commit()
    return team_output(db,team,member)


@router.post('/team-applications',status_code=201)
def apply(data:ApplicationInput,user:CurrentUser,db:Db):
    team=db.scalar(select(Team).where(Team.code==data.code.upper()).with_for_update())
    if not team:
        raise HTTPException(404,'团队编号无效，请向团队管理员确认。')
    if db.get(TeamMembership,(team.id,user.id)):
        raise HTTPException(409,'你已经是该团队成员')
    application=db.scalar(select(TeamApplication).where(TeamApplication.team_id==team.id,TeamApplication.user_id==user.id))
    if application and application.status=='pending':
        raise HTTPException(409,'申请正在审批，请勿重复提交')
    if not application:
        application=TeamApplication(team_id=team.id,user_id=user.id)
        db.add(application)
    application.status='pending';application.message=data.message;application.review_note='';application.reviewed_by=None;application.reviewed_at=None;application.created_at=utcnow()
    try:
        db.commit()
    except IntegrityError:
        db.rollback();raise HTTPException(409,'申请已提交，请刷新状态') from None
    return {'id':str(application.id),'team_name':team.name,'status':application.status}


@router.get('/team-applications')
def my_applications(user:CurrentUser,db:Db):
    rows=db.execute(select(TeamApplication,Team.name).join(Team,Team.id==TeamApplication.team_id).where(TeamApplication.user_id==user.id).order_by(TeamApplication.created_at.desc()).limit(100))
    return [{'id':str(a.id),'team_name':name,'team_id':str(a.team_id),'status':a.status,'review_note':a.review_note,'created_at':a.created_at} for a,name in rows]


@router.delete('/team-applications/{application_id}',status_code=204)
def cancel_application(application_id:UUID,user:CurrentUser,db:Db):
    snapshot=db.get(TeamApplication,application_id)
    if not snapshot or snapshot.user_id!=user.id:
        raise HTTPException(404,'申请不存在或不可访问')
    db.scalar(select(Team).where(Team.id==snapshot.team_id).with_for_update())
    application=db.scalar(select(TeamApplication).where(TeamApplication.id==application_id).execution_options(populate_existing=True))
    if application.status!='pending':
        raise HTTPException(409,'只能撤回待审批申请')
    application.status='cancelled';db.commit()


@router.get('/teams/{team_id}/applications')
def applications(team_id:UUID,user:CurrentUser,db:Db,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    require_team(db,team_id,user.id,admin=True)
    rows=db.execute(select(TeamApplication,User).join(User,User.id==TeamApplication.user_id).where(TeamApplication.team_id==team_id).order_by(TeamApplication.created_at.desc()).offset(offset).limit(limit))
    return [{'id':str(a.id),'user':ContactUserOutput.model_validate(u),'status':a.status,'message':a.message,'review_note':a.review_note,'created_at':a.created_at} for a,u in rows]


@router.post('/teams/{team_id}/applications/{application_id}/review')
def review(team_id:UUID,application_id:UUID,data:ReviewInput,user:CurrentUser,db:Db,request:Request):
    require_team(db,team_id,user.id,admin=True,lock=True)
    application=db.get(TeamApplication,application_id)
    if not application or application.team_id!=team_id:
        raise HTTPException(404,'申请不存在')
    if application.status!='pending':
        raise HTTPException(409,'申请已处理或撤回，请刷新')
    applicant=db.get(User,application.user_id)
    if not applicant or not applicant.is_active:
        raise HTTPException(409,'申请账号已停用')
    public=library(db,team_id)
    if data.decision=='approved':
        if db.get(TeamMembership,(team_id,application.user_id)):
            raise HTTPException(409,'申请人已加入团队')
        db.add(TeamMembership(team_id=team_id,user_id=application.user_id,role='member'))
        db.add(Membership(project_id=public.id,user_id=application.user_id,role='member'))
    application.status=data.decision;application.review_note=data.note;application.reviewed_by=user.id;application.reviewed_at=utcnow()
    record_event(db,public.id,user.id,'team.application_reviewed',application.id,request.state.trace_id,{'status':data.decision})
    db.commit()
    return {'id':str(application.id),'status':application.status}


@router.get('/teams/{team_id}/members')
def team_members(team_id:UUID,user:CurrentUser,db:Db):
    require_team(db,team_id,user.id)
    rows=db.execute(select(TeamMembership,User).join(User,User.id==TeamMembership.user_id).where(TeamMembership.team_id==team_id).order_by(User.username))
    return [{'user':ContactUserOutput.model_validate(u),'role':m.role,'joined_at':m.joined_at} for m,u in rows]


@router.put('/teams/{team_id}/members/{user_id}')
def team_role(team_id:UUID,user_id:UUID,data:TeamRoleInput,user:CurrentUser,db:Db,request:Request):
    require_team(db,team_id,user.id,owner=True,lock=True)
    member=db.get(TeamMembership,(team_id,user_id))
    if not member:
        raise HTTPException(404,'团队成员不存在')
    if member.role=='owner':
        raise HTTPException(409,'请通过转移所有权变更团队所有者')
    member.role=data.role
    public=library(db,team_id)
    db.get(Membership,(public.id,user_id)).role='admin' if data.role=='admin' else 'member'
    record_event(db,public.id,user.id,'team.member_role',user_id,request.state.trace_id,{'role':data.role})
    db.commit()
    return {'role':member.role}


def remove_member(team_id,user_id,user,db,request,leaving=False):
    _,actor=require_team(db,team_id,user.id,admin=not leaving,lock=True)
    member=db.get(TeamMembership,(team_id,user_id))
    if not member:
        raise HTTPException(404,'团队成员不存在')
    if member.role=='owner':
        raise HTTPException(409,'所有者需先将团队转移给另一位已确认教师')
    if not leaving and actor.role!='owner' and member.role!='member':
        raise HTTPException(403,'只有团队所有者可以移除管理员')
    projects=db.scalars(select(Project).where(Project.team_id==team_id).order_by(Project.id).with_for_update()).all()
    for project in projects:
        membership=db.get(Membership,(project.id,user_id))
        if membership and membership.role=='admin' and not project.is_team_library:
            another=db.scalar(select(Membership.user_id).where(Membership.project_id==project.id,Membership.user_id!=user_id,Membership.role=='admin'))
            if not another:
                raise HTTPException(409,'该成员仍是某项目最后一位管理员，请先转交项目管理权限。')
    ids=[p.id for p in projects]
    db.execute(delete(Membership).where(Membership.project_id.in_(ids),Membership.user_id==user_id))
    db.delete(member)
    record_event(db,library(db,team_id).id,user.id,'team.member_left' if leaving else 'team.member_removed',user_id,request.state.trace_id)
    db.commit()


@router.delete('/teams/{team_id}/members/{user_id}',status_code=204)
def remove(team_id:UUID,user_id:UUID,user:CurrentUser,db:Db,request:Request):
    remove_member(team_id,user_id,user,db,request)


@router.post('/teams/{team_id}/leave',status_code=204)
def leave(team_id:UUID,user:CurrentUser,db:Db,request:Request):
    remove_member(team_id,user.id,user,db,request,leaving=True)


@router.post('/teams/{team_id}/transfer/{user_id}')
def transfer(team_id:UUID,user_id:UUID,user:CurrentUser,db:Db,request:Request):
    _,owner=require_team(db,team_id,user.id,owner=True,lock=True)
    target=db.get(TeamMembership,(team_id,user_id));teacher=db.get(User,user_id)
    if user_id==user.id or not target or not teacher.teacher_verified or teacher.academic_identity!='teacher' or not teacher.is_active:
        raise HTTPException(409,'请选择另一位已加入团队且身份已确认的教师')
    owner.role='admin';target.role='owner'
    public=library(db,team_id);db.get(Membership,(public.id,user_id)).role='admin'
    record_event(db,public.id,user.id,'team.owner_transferred',user_id,request.state.trace_id)
    db.commit()
    return {'owner_id':str(user_id)}
