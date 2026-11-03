import hashlib
from pathlib import Path
from typing import Annotated
import uuid

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select

from .dependencies import CurrentUser, Db, require_membership
from .models import Chunk, Document, DocumentVersion, IngestionTask, utcnow
from .storage import storage_path
from .runtime import MAX_ATTEMPTS

router = APIRouter(prefix='/api')


def require_document(db, document_id, user_id, *, admin=False, lock=False):
    document = db.get(Document, document_id)
    if not document:
        raise HTTPException(404, '资料不存在或不可访问')
    require_membership(db, document.project_id, user_id, admin=admin, lock=lock)
    if lock:
        document = db.scalar(select(Document).where(Document.id == document_id).with_for_update().execution_options(populate_existing=True))
    if not document.is_active:
        raise HTTPException(404, '资料已撤下或不可访问')
    return document


def version_output(db, version):
    task = db.scalar(select(IngestionTask).where(IngestionTask.version_id == version.id))
    return {
        'id': str(version.id), 'number': version.number, 'filename': version.filename,
        'status': version.status, 'file_size': version.file_size, 'chunk_count': version.chunk_count,
        'embedding_model': version.embedding_model, 'created_at': version.created_at,
        'task': task_output(task) if task else None,
    }


def task_output(task):
    return {'id': str(task.id), 'version_id': str(task.version_id), 'status': task.status,
            'stage': task.stage, 'attempts': task.attempts, 'error_code': task.error_code, 'updated_at': task.updated_at,
            'max_attempts': MAX_ATTEMPTS,
            'retry_at': task.available_at if task.status == 'queued' and task.attempts else None,
            'lease_until': task.lease_until if task.status == 'processing' else None}


def document_output(db, document):
    latest = db.scalar(select(DocumentVersion).where(DocumentVersion.document_id == document.id).order_by(DocumentVersion.number.desc()).limit(1))
    current = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
    return {'id': str(document.id), 'project_id': str(document.project_id), 'title': document.title,
            'is_active': document.is_active, 'current_version_id': str(document.current_version_id) if document.current_version_id else None,
            'current_version_number': current.number if current else None,
            'latest_version': version_output(db, latest) if latest else None}


def save_version(db, settings, user, document, file, consent):
    if not consent:
        raise HTTPException(422, '请确认有权上传，且允许将提取文本发送至已配置的向量服务')
    filename = Path((file.filename or '').replace('\\','/')).name
    if not filename or len(filename) > 200 or any(ord(char) < 32 for char in filename):
        raise HTTPException(422, '文件名不合法或过长')
    suffix = Path(filename).suffix.lower()
    if suffix not in {'.pdf', '.md'}:
        raise HTTPException(415, '仅支持文本型 PDF 和 UTF-8 Markdown')
    content = file.file.read(settings.max_upload_bytes+1)
    if not content:
        raise HTTPException(422, '文件不能为空')
    if len(content) > settings.max_upload_bytes:
        raise HTTPException(413, '文件不能超过20MB')
    if suffix == '.pdf' and not content.startswith(b'%PDF-'):
        raise HTTPException(422, '文件不是有效的PDF格式')
    version_id = uuid.uuid4()
    key = version_id.hex + suffix
    path = storage_path(settings.storage_dir, key)
    path.parent.mkdir(parents=True, exist_ok=True)
    document.latest_version += 1
    version = DocumentVersion(id=version_id, document_id=document.id, number=document.latest_version,
        filename=filename, storage_key=key, file_hash=hashlib.sha256(content).hexdigest(), file_size=len(content), created_by=user.id)
    task = IngestionTask(id=uuid.uuid4(), version_id=version_id)
    try:
        path.write_bytes(content)
        db.add(version)
        db.flush()
        db.add(task)
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return {'document_id': str(document.id), 'version_id': str(version.id), 'task_id': str(task.id)}


@router.post('/documents', status_code=202)
def upload_document(user: CurrentUser, db: Db, request: Request,
    project_id: Annotated[uuid.UUID, Form()], file: Annotated[UploadFile, File()],
    allow_external_processing: Annotated[bool, Form()] = False,
    title: Annotated[str | None, Form(max_length=200)] = None):
    require_membership(db, project_id, user.id, admin=True, lock=True)
    document = Document(id=uuid.uuid4(), project_id=project_id, title=(title or file.filename or '未命名资料').strip()[:200], latest_version=0)
    if not document.title:
        raise HTTPException(422, '资料标题不能为空')
    db.add(document)
    db.flush()
    return save_version(db, request.app.state.settings, user, document, file, allow_external_processing)


@router.post('/documents/{document_id}/versions', status_code=202)
def upload_version(document_id: uuid.UUID, user: CurrentUser, db: Db, request: Request,
    file: Annotated[UploadFile, File()], allow_external_processing: Annotated[bool, Form()] = False):
    document = require_document(db, document_id, user.id, admin=True, lock=True)
    return save_version(db, request.app.state.settings, user, document, file, allow_external_processing)


@router.get('/projects/{project_id}/documents')
def list_documents(project_id: uuid.UUID, user: CurrentUser, db: Db):
    _, membership = require_membership(db, project_id, user.id)
    query = select(Document).where(Document.project_id == project_id)
    if membership.role != 'admin':
        query = query.where(Document.is_active.is_(True))
    return [document_output(db, d) for d in db.scalars(query.order_by(Document.created_at.desc()).limit(100))]


@router.get('/documents/{document_id}/versions')
def versions(document_id: uuid.UUID, user: CurrentUser, db: Db):
    require_document(db, document_id, user.id)
    return [version_output(db, v) for v in db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == document_id).order_by(DocumentVersion.number.desc()))]


@router.get('/tasks/{task_id}')
def read_task(task_id: uuid.UUID, user: CurrentUser, db: Db):
    task = db.get(IngestionTask, task_id)
    if not task:
        raise HTTPException(404, '任务不存在或不可访问')
    version = db.get(DocumentVersion, task.version_id)
    require_document(db, version.document_id, user.id)
    return task_output(task)


@router.post('/tasks/{task_id}/retry')
def retry_task(task_id: uuid.UUID, user: CurrentUser, db: Db):
    task = db.get(IngestionTask, task_id)
    if not task:
        raise HTTPException(404, '任务不存在或不可访问')
    version = db.get(DocumentVersion, task.version_id)
    document = require_document(db, version.document_id, user.id, admin=True, lock=True)
    task = db.scalar(select(IngestionTask).where(IngestionTask.id == task_id).with_for_update().execution_options(populate_existing=True))
    if task.status != 'failed':
        raise HTTPException(409, '仅失败任务可重试')
    if version.number != document.latest_version:
        raise HTTPException(409, '已有更新版本，请重试最新版本或重新上传')
    task.status = version.status = 'queued'
    task.stage = 'queued'
    task.attempts = 0
    task.error_code = task.lease_token = task.lease_until = None
    task.available_at = task.updated_at = utcnow()
    db.commit()
    return task_output(task)


@router.post('/documents/{document_id}/withdraw')
def withdraw(document_id: uuid.UUID, user: CurrentUser, db: Db):
    document = require_document(db, document_id, user.id, admin=True, lock=True)
    document.is_active = False
    tasks = db.scalars(select(IngestionTask).join(DocumentVersion).where(DocumentVersion.document_id == document_id, IngestionTask.status.in_(['queued','processing'])).with_for_update(of=IngestionTask)).all()
    for task in tasks:
        task.status = task.stage = 'cancelled'
        task.lease_token = task.lease_until = None
        task.updated_at = utcnow()
        db.get(DocumentVersion, task.version_id).status = 'cancelled'
    db.commit()
    return {'id': str(document.id), 'is_active': False}


def authorized_version(db, document_id, version_id, user_id):
    document = require_document(db, document_id, user_id)
    target = version_id or document.current_version_id
    version = db.get(DocumentVersion, target) if target else None
    if not version or version.document_id != document_id:
        raise HTTPException(404, '版本不存在或尚未可用')
    return version


@router.get('/documents/{document_id}/source')
def source(document_id: uuid.UUID, user: CurrentUser, db: Db, request: Request, version_id: uuid.UUID | None = None):
    version = authorized_version(db, document_id, version_id, user.id)
    path = storage_path(request.app.state.settings.storage_dir, version.storage_key)
    if not path.is_file():
        raise HTTPException(404, '原文件不存在')
    media = 'application/pdf' if path.suffix == '.pdf' else 'text/plain'
    return FileResponse(path, media_type=media, filename=version.filename, content_disposition_type='inline')


@router.get('/documents/{document_id}/chunks')
def chunks(document_id: uuid.UUID, user: CurrentUser, db: Db,
           version_id: uuid.UUID | None = None, offset: int = Query(0, ge=0), limit: int = Query(30, ge=1, le=100)):
    version = authorized_version(db, document_id, version_id, user.id)
    if version.status != 'ready':
        raise HTTPException(409, '该版本尚未处理完成')
    rows = db.scalars(select(Chunk).where(Chunk.version_id == version.id).order_by(Chunk.ordinal).offset(offset).limit(limit))
    return {'version_id': str(version.id), 'total': version.chunk_count, 'items': [
        {'id': str(c.id), 'ordinal': c.ordinal, 'text': c.text, 'page': c.page, 'section': c.section, 'line_start': c.line_start, 'line_end': c.line_end} for c in rows]}
