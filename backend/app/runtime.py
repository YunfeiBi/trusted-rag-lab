"""Small, project-authorized ingestion diagnostics, without document contents."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import AwareDatetime
from sqlalchemy import func, select

from .dependencies import CurrentUser, Db, require_membership
from .models import AuditEvent, Document, DocumentVersion, IngestionTask, WorkerHeartbeat, User, utcnow

router = APIRouter(prefix='/api')
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
HEARTBEAT_FRESH_SECONDS = 90


@router.get('/file-limits')
def file_limits(user: CurrentUser, request: Request):
    settings = request.app.state.settings
    return {name:getattr(settings,name) for name in ['max_upload_bytes','max_pdf_pages','max_text_chars','max_chunks','parse_timeout_seconds','processing_timeout_seconds']}


@router.get('/projects/{project_id}/tasks')
def project_tasks(project_id: UUID,user:CurrentUser,db:Db,status:str|None=None,created_from:AwareDatetime|None=None,created_before:AwareDatetime|None=None,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    require_membership(db,project_id,user.id)
    from .documents import task_output
    query = select(IngestionTask,Document.title,Document.id,DocumentVersion.number).join(DocumentVersion,DocumentVersion.id==IngestionTask.version_id).join(Document,Document.id==DocumentVersion.document_id).where(Document.project_id==project_id,Document.is_active.is_(True))
    if status:
        query = query.where(IngestionTask.status==status)
    if created_from and created_before and created_from >= created_before:
        raise HTTPException(422, '开始日期不能晚于结束日期')
    if created_from:
        query = query.where(IngestionTask.created_at >= created_from)
    if created_before:
        query = query.where(IngestionTask.created_at < created_before)
    rows = db.execute(query.order_by(IngestionTask.created_at.desc()).offset(offset).limit(limit))
    return [{**task_output(task),'document_title':title,'document_id':str(doc_id),'version_number':number} for task,title,doc_id,number in rows]


@router.get('/projects/{project_id}/audit-events')
def audit_events(project_id:UUID,user:CurrentUser,db:Db,offset:int=Query(0,ge=0),limit:int=Query(30,ge=1,le=100)):
    require_membership(db,project_id,user.id,admin=True)
    rows = db.execute(select(AuditEvent,User.display_name).join(User,User.id==AuditEvent.actor_id).where(AuditEvent.project_id==project_id).order_by(AuditEvent.created_at.desc()).offset(offset).limit(limit))
    return [{'id':str(e.id),'actor':name,'action':e.action,'target_id':e.target_id,'details':e.details,'trace_id':e.trace_id,'created_at':e.created_at} for e,name in rows]


@router.get('/projects/{project_id}/ingestion-status')
def ingestion_status(project_id: UUID, user: CurrentUser, db: Db):
    require_membership(db, project_id, user.id)
    now = utcnow()
    fresh = select(WorkerHeartbeat).where(WorkerHeartbeat.state != 'stopped', WorkerHeartbeat.last_seen >= now-timedelta(seconds=HEARTBEAT_FRESH_SECONDS)).subquery()
    count = db.scalar(select(func.count()).select_from(fresh))
    latest = db.scalar(select(func.max(WorkerHeartbeat.last_seen)).where(WorkerHeartbeat.state != 'stopped'))
    scope = select(IngestionTask.status, func.count()).join(DocumentVersion, DocumentVersion.id == IngestionTask.version_id).join(Document, Document.id == DocumentVersion.document_id).where(Document.project_id == project_id, Document.is_active.is_(True)).group_by(IngestionTask.status)
    counts = dict(db.execute(scope).all())
    oldest = db.scalar(select(func.min(IngestionTask.created_at)).join(DocumentVersion, DocumentVersion.id == IngestionTask.version_id).join(Document, Document.id == DocumentVersion.document_id).where(Document.project_id == project_id, Document.is_active.is_(True), IngestionTask.status.in_(['queued','processing'])))
    return {'worker_status': 'recent_heartbeat' if count else 'no_recent_heartbeat',
            'last_heartbeat_at': latest, 'freshness_seconds': HEARTBEAT_FRESH_SECONDS,
            'tasks': {status: counts.get(status, 0) for status in ['queued','processing','succeeded','failed','cancelled']},
            'oldest_pending_at': oldest, 'checked_at': now}
