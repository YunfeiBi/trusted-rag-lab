"""Standalone ingestion worker with leased claims and atomic publication."""
import argparse
from datetime import timedelta
import hashlib
import logging
import time
import uuid
from time import perf_counter

import jieba
from sqlalchemy import and_, create_engine, delete, or_, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.dialects.postgresql import insert

from backend.app.config import Settings
from backend.app.ingestion import IngestionError, parse_and_chunk
from backend.app.model_client import EmbeddingClient
from backend.app.models import Chunk, Document, DocumentVersion, IngestionTask, WorkerHeartbeat, utcnow
from backend.app.storage import storage_path
from backend.app.runtime import MAX_ATTEMPTS, LEASE_SECONDS

logger = logging.getLogger('trusted_rag.worker')


class LeaseLost(Exception):
    pass


class Worker:
    def __init__(self, session_factory, settings, model=None):
        self.sessions = session_factory
        self.settings = settings
        self.model = model or EmbeddingClient()
        self.worker_id = uuid.uuid4()

    def heartbeat(self, state, db=None):
        row = {'id': self.worker_id, 'state': state, 'last_seen': utcnow()}
        statement = insert(WorkerHeartbeat).values(**row).on_conflict_do_update(index_elements=[WorkerHeartbeat.id], set_={'state': state, 'last_seen': row['last_seen']})
        if db is not None:
            db.execute(statement)
        else:
            with self.sessions() as session, session.begin():
                session.execute(statement)

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
            self.heartbeat('busy', db)

    @staticmethod
    def check_lease(task, token):
        if not task or task.status != 'processing' or task.lease_token != token or task.lease_until <= utcnow():
            raise LeaseLost()

    def process(self, task_id, token):
        start = perf_counter()
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
            timer = perf_counter()
            parts = parse_and_chunk(data, path.suffix)
            parse_ms = (perf_counter()-timer)*1000
            timer = perf_counter()
            vectors = []
            for offset in range(0, len(parts), 8):
                self.renew(task_id, token, 'embedding')
                batch = self.model.embed([p.text for p in parts[offset:offset+8]])
                if len(batch) != len(parts[offset:offset+8]) or any(len(v) != 1024 for v in batch):
                    raise IngestionError('EMBEDDING_INVALID_RESPONSE')
                vectors.extend(batch)
            embedding_ms = (perf_counter()-timer)*1000
            self.renew(task_id, token, 'publishing')
            timer = perf_counter()
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
            logger.info('task=%s status=succeeded chunks=%s parse_ms=%.2f embedding_ms=%.2f publish_ms=%.2f total_ms=%.2f', task_id, len(parts), parse_ms, embedding_ms, (perf_counter()-timer)*1000, (perf_counter()-start)*1000)
        except LeaseLost:
            logger.info('task=%s status=lease_lost', task_id)
        except SQLAlchemyError:
            # Do not change task state on an uncertain database outcome. Reclaim
            # after lease expiry if publication did not commit successfully.
            raise
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
        self.heartbeat('idle')
        claimed = self.claim()
        if claimed is None:
            return False
        self.heartbeat('busy')
        self.process(*claimed)
        self.heartbeat('idle')
        return True


def run_loop(worker, *, once=False, sleep=time.sleep):
    failures = 0
    while True:
        try:
            worked = worker.run_once()
            if failures:
                logger.info('event=worker_database_recovered failures=%s', failures)
            failures = 0
        except SQLAlchemyError as exc:
            failures += 1
            delay = min(30, 2 ** min(failures-1, 5))
            # Exception text can contain connection credentials or SQL parameters.
            logger.warning('event=worker_database_unavailable type=%s retry_seconds=%s', type(exc).__name__, delay)
            if once:
                return 1
            sleep(delay)
            continue
        if once:
            return 0
        if not worked:
            sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--once', action='store_true')
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
    logging.getLogger('httpx').setLevel(logging.WARNING)
    settings = Settings.load()
    engine = create_engine(settings.database_url, pool_pre_ping=True, hide_parameters=True, connect_args={'connect_timeout': 5}, pool_timeout=5)
    worker = Worker(sessionmaker(engine, expire_on_commit=False), settings)
    try:
        return run_loop(worker, once=arguments.once)
    except KeyboardInterrupt:
        pass
    finally:
        try:
            worker.heartbeat('stopped')
        except SQLAlchemyError:
            pass
        engine.dispose()


if __name__ == '__main__':
    raise SystemExit(main())
