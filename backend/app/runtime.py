"""Small, project-authorized ingestion diagnostics, without document contents."""
from datetime import timedelta
from uuid import UUID

from fastapi import APIRouter
from sqlalchemy import func, select

from .dependencies import CurrentUser, Db, require_membership
from .models import Document, DocumentVersion, IngestionTask, WorkerHeartbeat, utcnow

router = APIRouter(prefix='/api')
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
HEARTBEAT_FRESH_SECONDS = 90


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
