from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import event, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from backend.app.dependencies import get_db
from backend.app.ingestion import IngestionError
from backend.app.models import Chunk, Document, DocumentVersion, IngestionTask, WorkerHeartbeat, utcnow
from backend.worker import logger, run_loop
from tests.conftest import login
from tests.test_documents import FakeEmbedding, upload, worker_for


def test_ingestion_diagnostics_scope_and_worker_freshness(environment):
    client, engine, ids = environment
    owner, member, outsider = login(client), login(client, 'member'), login(client, 'outsider')
    path = '/api/projects/'+ids['project']+'/ingestion-status'
    assert client.get(path).status_code == 401
    assert client.get(path, headers=outsider).status_code == 404
    assert client.get(path, headers=member).json()['worker_status'] == 'no_recent_heartbeat'
    worker = worker_for(client, engine)
    worker.run_once()
    assert client.get(path, headers=owner).json()['worker_status'] == 'recent_heartbeat'
    record = upload(client, ids, owner).json()
    private_project = client.post('/api/projects', headers=outsider, json={'name': 'private'}).json()
    upload(client, {'project': private_project['id']}, outsider)
    result = client.get(path, headers=member).json()
    assert result['tasks']['queued'] == 1 and result['oldest_pending_at']
    assert record['document_id'] not in str(result) and 'worker_id' not in result
    with Session(engine) as db, db.begin():
        db.get(WorkerHeartbeat, worker.worker_id).last_seen = utcnow()-timedelta(seconds=91)
    assert client.get(path, headers=owner).json()['worker_status'] == 'no_recent_heartbeat'
    worker.heartbeat('stopped')
    assert client.get(path, headers=owner).json()['worker_status'] == 'no_recent_heartbeat'
    client.delete('/api/projects/'+ids['project']+'/members/'+ids['member'], headers=owner)
    assert client.get(path, headers=member).status_code == 404


def test_concurrent_version_upload_and_publication(environment):
    client, engine, ids = environment
    owner = login(client)
    first = upload(client, ids, owner).json()
    worker_for(client, engine).run_once()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda n: upload(client, ids, owner, document=first['document_id'], text='Concurrent membership version '+str(n)), range(2)))
    assert all(r.status_code == 202 for r in responses)
    jobs = [worker_for(client, engine).claim() for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(lambda job: worker_for(client, engine).process(*job), jobs))
    with Session(engine) as db:
        doc = db.get(Document, UUID(first['document_id']))
        versions = db.scalars(select(DocumentVersion).where(DocumentVersion.document_id == doc.id).order_by(DocumentVersion.number)).all()
        assert [v.number for v in versions] == [1, 2, 3]
        assert all(v.status == 'ready' for v in versions)
        assert doc.current_version_id == versions[-1].id


def test_publication_database_fault_rolls_back_then_lease_recovers(environment):
    client, engine, ids = environment
    owner = login(client)
    first = upload(client, ids, owner).json()
    worker = worker_for(client, engine)
    worker.run_once()
    newer = upload(client, ids, owner, document=first['document_id'], text='membership new version').json()

    def fail_chunk_insert(conn, cursor, statement, parameters, context, executemany):
        if statement.lstrip().startswith('INSERT INTO chunks'):
            raise OperationalError('sensitive-sql', {}, Exception('test-secret-password'))

    event.listen(engine, 'before_cursor_execute', fail_chunk_insert)
    try:
        assert run_loop(worker, once=True) == 1
    finally:
        event.remove(engine, 'before_cursor_execute', fail_chunk_insert)
    with Session(engine) as db, db.begin():
        assert db.get(Document, UUID(first['document_id'])).current_version_id == UUID(first['version_id'])
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.version_id == UUID(newer['version_id']))) == 0
        task = db.get(IngestionTask, UUID(newer['task_id']))
        assert task.status == 'processing'
        task.lease_until = utcnow()-timedelta(seconds=1)
    assert worker.run_once()
    with Session(engine) as db:
        assert db.get(Document, UUID(first['document_id'])).current_version_id == UUID(newer['version_id'])
        assert db.get(IngestionTask, UUID(newer['task_id'])).attempts == 2
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.version_id == UUID(newer['version_id']))) == 1


def test_database_retry_backoff_and_safe_logs(monkeypatch, caplog):
    monkeypatch.setattr(logger, 'propagate', True)
    delays = []

    class IntermittentWorker:
        calls = 0

        def run_once(self):
            self.calls += 1
            if self.calls <= 7:
                raise OperationalError('sensitive-sql', {}, Exception('test-secret-password'))
            return False

    worker = IntermittentWorker()

    def sleep(delay):
        delays.append(delay)
        if worker.calls == 8:
            raise KeyboardInterrupt

    with caplog.at_level('INFO', logger='trusted_rag.worker'), pytest.raises(KeyboardInterrupt):
        run_loop(worker, sleep=sleep)
    assert delays == [1, 2, 4, 8, 16, 30, 30, 1]
    assert 'worker_database_recovered' in caplog.text
    assert 'test-secret-password' not in caplog.text and 'sensitive-sql' not in caplog.text


def test_retry_schedule_lease_details_and_partial_batch_no_publish(environment):
    client, engine, ids = environment
    owner = login(client)
    record = upload(client, ids, owner, text=('membership evidence\n\n'*20)*25).json()

    class PartialModel(FakeEmbedding):
        calls = 0

        def embed(self, texts):
            self.calls += 1
            if self.calls == 2:
                raise IngestionError('EMBEDDING_UNAVAILABLE', retryable=True)
            return [[1.0]+[0.0]*1023 for _ in texts]

    worker = worker_for(client, engine, PartialModel())
    worker.run_once()
    data = client.get('/api/tasks/'+record['task_id'], headers=owner).json()
    assert data['status'] == 'queued' and data['retry_at'] and data['max_attempts'] == 3
    assert worker.claim() is None  # Backoff cannot be bypassed by another Worker.
    with Session(engine) as db, db.begin():
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0
        db.get(IngestionTask, UUID(record['task_id'])).available_at = utcnow()-timedelta(seconds=1)
    job = worker.claim()
    data = client.get('/api/tasks/'+record['task_id'], headers=owner).json()
    assert data['status'] == 'processing' and data['lease_until'] and data['retry_at'] is None
    worker.process(*job)
    assert client.get('/api/tasks/'+record['task_id'], headers=owner).json()['status'] == 'succeeded'


def test_api_database_fault_has_safe_trace_and_recovers(environment):
    client, _, _ = environment

    def unavailable():
        raise OperationalError('sensitive-sql', {}, Exception('test-secret-password'))
        yield

    client.app.dependency_overrides[get_db] = unavailable
    try:
        response = client.get('/api/health/ready')
        assert response.status_code == 503
        assert response.json()['trace_id'] == response.headers['x-request-id']
        assert 'test-secret-password' not in response.text and 'sensitive-sql' not in response.text
        assert client.get('/api/health/live').status_code == 200
    finally:
        client.app.dependency_overrides.clear()
    assert client.get('/api/health/ready').status_code == 200
