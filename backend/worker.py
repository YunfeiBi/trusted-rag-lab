"""Standalone ingestion worker with leased claims and atomic publication."""
import argparse
from datetime import timedelta
import hashlib
import logging
import time
import uuid

import jieba
from sqlalchemy import and_, create_engine, delete, or_, select
from sqlalchemy.orm import sessionmaker

from backend.app.config import Settings
from backend.app.ingestion import IngestionError, parse_and_chunk
from backend.app.model_client import EmbeddingClient
from backend.app.models import Chunk, Document, DocumentVersion, IngestionTask, utcnow
from backend.app.storage import storage_path

logger = logging.getLogger('trusted_rag.worker')
MAX_ATTEMPTS = 3
LEASE_SECONDS = 300


class LeaseLost(Exception):
    pass


class Worker:
    def __init__(self, session_factory, settings, model=None):
        self.sessions = session_factory
        self.settings = settings
        self.model = model or EmbeddingClient()

    def claim(self):
        now = utcnow()
        with self.sessions() as db, db.begin():
            exhausted = db.scalars(select(IngestionTask).where(IngestionTask.status == 'processing', IngestionTask.lease_until <= now, IngestionTask.attempts >= MAX_ATTEMPTS).with_for_update(skip_locked=True))
            for task in exhausted:
                task.status = task.stage = 'failed'
                task.error_code = 'LEASE_EXHAUSTED'
                task.lease_token = task.lease_until = None
                task.updated_at = now
                db.get(DocumentVersion, task.version_id).status = 'failed'
            task = db.scalar(select(IngestionTask).where(IngestionTask.attempts < MAX_ATTEMPTS, or_(
                and_(IngestionTask.status == 'queued', IngestionTask.available_at <= now),
                and_(IngestionTask.status == 'processing', IngestionTask.lease_until <= now)
            )).order_by(IngestionTask.created_at).with_for_update(skip_locked=True).limit(1))
            if not task:
                return None
            task.status = 'processing'
            task.stage = 'parsing'
            task.attempts += 1
            task.lease_token = uuid.uuid4()
            task.lease_until = now + timedelta(seconds=LEASE_SECONDS)
            task.updated_at = now
            task.error_code = None
            db.get(DocumentVersion, task.version_id).status = 'processing'
            return task.id, task.lease_token

    def renew(self, task_id, token, stage):
        with self.sessions() as db, db.begin():
            task = db.scalar(select(IngestionTask).where(IngestionTask.id == task_id).with_for_update())
            self.check_lease(task, token)
            task.stage = stage
            task.lease_until = utcnow() + timedelta(seconds=LEASE_SECONDS)
            task.updated_at = utcnow()

    @staticmethod
    def check_lease(task, token):
        if not task or task.status != 'processing' or task.lease_token != token or task.lease_until <= utcnow():
            raise LeaseLost()

    def process(self, task_id, token):
        try:
            with self.sessions() as db:
                task = db.get(IngestionTask, task_id)
                self.check_lease(task, token)
                version = db.get(DocumentVersion, task.version_id)
                document = db.get(Document, version.document_id)
                if not document.is_active:
                    raise IngestionError('DOCUMENT_WITHDRAWN')
                key, expected_hash = version.storage_key, version.file_hash
            path = storage_path(self.settings.storage_dir, key)
            if not path.is_file():
                raise IngestionError('FILE_NOT_FOUND')
            data = path.read_bytes()
            if len(data) > self.settings.max_upload_bytes:
                raise IngestionError('FILE_TOO_LARGE')
            if hashlib.sha256(data).hexdigest() != expected_hash:
                raise IngestionError('FILE_CHANGED')
            parts = parse_and_chunk(data, path.suffix)
            vectors = []
            for offset in range(0, len(parts), 8):
                self.renew(task_id, token, 'embedding')
                batch = self.model.embed([p.text for p in parts[offset:offset+8]])
                if len(batch) != len(parts[offset:offset+8]) or any(len(v) != 1024 for v in batch):
                    raise IngestionError('EMBEDDING_INVALID_RESPONSE')
                vectors.extend(batch)
            self.renew(task_id, token, 'publishing')
            keywords = [' '.join(jieba.cut(p.text)) for p in parts]
            with self.sessions() as db, db.begin():
                task_snapshot = db.get(IngestionTask, task_id)
                version = db.get(DocumentVersion, task_snapshot.version_id)
                document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update())
                task = db.scalar(select(IngestionTask).where(IngestionTask.id == task_id).with_for_update().execution_options(populate_existing=True))
                self.check_lease(task, token)
                if not document.is_active:
                    raise IngestionError('DOCUMENT_WITHDRAWN')
                db.execute(delete(Chunk).where(Chunk.version_id == version.id))
                for ordinal, (part, vector, keyword) in enumerate(zip(parts, vectors, keywords)):
                    db.add(Chunk(id=uuid.uuid5(version.id, str(ordinal)), version_id=version.id, ordinal=ordinal,
                        text=part.text, page=part.page, section=part.section, line_start=part.line_start, line_end=part.line_end,
                        keyword_text=keyword, embedding=vector))
                db.flush()
                version.chunk_count = len(parts)
                version.status = 'ready'
                version.embedding_model = self.model.model
                current = db.get(DocumentVersion, document.current_version_id) if document.current_version_id else None
                if current is None or version.number > current.number:
                    document.current_version_id = version.id
                task.status = 'succeeded'
                task.stage = 'done'
                task.error_code = task.lease_token = task.lease_until = None
                task.updated_at = utcnow()
            logger.info('task=%s status=succeeded chunks=%s', task_id, len(parts))
        except LeaseLost:
            logger.info('task=%s status=lease_lost', task_id)
        except Exception as exc:
            logger.warning('task=%s failure_type=%s', task_id, type(exc).__name__)
            failure = exc if isinstance(exc, IngestionError) else IngestionError('UNEXPECTED_PROCESSING_ERROR', retryable=True)
            self.fail(task_id, token, failure)

    def fail(self, task_id, token, failure):
        with self.sessions() as db, db.begin():
            snapshot = db.get(IngestionTask, task_id)
            version = db.get(DocumentVersion, snapshot.version_id)
            document = db.scalar(select(Document).where(Document.id == version.document_id).with_for_update())
            task = db.scalar(select(IngestionTask).where(IngestionTask.id == task_id).with_for_update().execution_options(populate_existing=True))
            try:
                self.check_lease(task, token)
            except LeaseLost:
                return
            if not document.is_active:
                task.status = version.status = 'cancelled'
            elif failure.retryable and task.attempts < MAX_ATTEMPTS:
                task.status = version.status = 'queued'
                task.available_at = utcnow() + timedelta(seconds=5 * 2**(task.attempts-1))
            else:
                task.status = version.status = 'failed'
            task.stage = task.status
            task.error_code = failure.code
            task.lease_token = task.lease_until = None
            task.updated_at = utcnow()
            logger.warning('task=%s status=%s code=%s', task_id, task.status, failure.code)

    def run_once(self):
        claimed = self.claim()
        if claimed is None:
            return False
        self.process(*claimed)
        return True


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    settings = Settings.load()
    engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True)
    worker = Worker(sessionmaker(engine, expire_on_commit=False), settings)
    try:
        while True:
            worked = worker.run_once()
            if arguments.once:
                break
            if not worked:
                time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
