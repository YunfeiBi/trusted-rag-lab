from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from dataclasses import replace
from uuid import UUID

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
import pytest
from sqlalchemy import Text, func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.app.ingestion import IngestionError, parse_and_chunk
from backend.app.models import Chunk, Document, DocumentVersion, IngestionTask, utcnow
from backend.worker import Worker
from backend.app.storage import storage_path
from tests.conftest import login


class FakeEmbedding:
    model = 'test-embedding-1024'
    calls = 0

    def embed(self, texts):
        self.calls += 1
        return [[1.0]+[0.0]*1023 for _ in texts]


def worker_for(client, engine, model=None):
    return Worker(sessionmaker(engine, expire_on_commit=False), client.app.state.settings, model or FakeEmbedding())


def upload(client, ids, headers, text='# Rules\n\nProject documents require membership.', document=None, name='rules.md', consent='true'):
    path = '/api/documents' if not document else f'/api/documents/{document}/versions'
    data = {'allow_external_processing': consent}
    if not document:
        data['project_id'] = ids['project']
    content = text.encode() if isinstance(text,str) else text
    return client.post(path, headers=headers, data=data, files={'file': (name, content)})


def pdf_bytes(encrypted=False):
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    font = DictionaryObject({NameObject('/Type'):NameObject('/Font'), NameObject('/Subtype'):NameObject('/Type1'), NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')] = DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream = DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 700 Td (Project access requires valid membership.) Tj ET')
    page[NameObject('/Contents')] = writer._add_object(stream)
    if encrypted:
        writer.encrypt('test-pdf-password')
    output = BytesIO()
    writer.write(output)
    return output.getvalue()


def test_upload_permissions_validation_and_no_file_leak(environment):
    client, engine, ids = environment
    owner, member, outsider = login(client), login(client,'member'), login(client,'outsider')
    assert upload(client,ids,member).status_code == 403
    assert upload(client,ids,outsider).status_code == 404
    assert upload(client,ids,owner,consent='false').status_code == 422
    assert upload(client,ids,owner,name='bad.html').status_code == 415
    assert upload(client,ids,owner,text=b'bad-pdf',name='bad.pdf').status_code == 422
    assert upload(client,ids,owner,text=b'').status_code == 422
    assert not list(client.app.state.settings.storage_dir.glob('*'))
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Document)) == 0


def test_upload_size_and_cross_document_source(environment):
    client, _, ids = environment
    owner = login(client)
    initial = client.app.state.settings
    client.app.state.settings = replace(initial,max_upload_bytes=8)
    assert upload(client,ids,owner,text='x'*9).status_code == 413
    client.app.state.settings = initial
    one,two = upload(client,ids,owner).json(),upload(client,ids,owner).json()
    assert client.get('/api/documents/'+one['document_id']+'/source?version_id='+two['version_id'],headers=owner).status_code == 404


def test_markdown_ingestion_and_source_permissions(environment):
    client, engine, ids = environment
    owner, member, outsider = login(client),login(client,'member'),login(client,'outsider')
    response = upload(client,ids,owner)
    assert response.status_code == 202
    result = response.json()
    task = '/api/tasks/'+result['task_id']
    assert client.get(task, headers=owner).json()['status'] == 'queued'
    assert client.get(task, headers=outsider).status_code == 404
    assert worker_for(client,engine).run_once()
    assert client.get(task, headers=member).json()['status'] == 'succeeded'
    docs = client.get('/api/projects/'+ids['project']+'/documents',headers=member).json()
    assert docs[0]['current_version_id'] == result['version_id']
    assert 'storage_key' not in str(docs)
    preview = client.get('/api/documents/'+result['document_id']+'/chunks',headers=member).json()
    assert preview['items'][0]['line_start'] == 1
    assert preview['items'][0]['section'] == 'Rules'
    assert 'embedding' not in preview['items'][0]
    source = '/api/documents/'+result['document_id']+'/source'
    assert 'membership' in client.get(source,headers=member).text
    assert client.get(source,headers=outsider).status_code == 404
    client.delete('/api/projects/'+ids['project']+'/members/'+ids['member'],headers=owner)
    assert client.get(source,headers=member).status_code == 404
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Chunk)) == preview['total']
        assert db.scalar(select(func.length(Chunk.search_vector.cast(Text)))) > 0


def test_pdf_parsing_preserves_page_and_rejects_encrypted():
    parts = parse_and_chunk(pdf_bytes(),'.pdf')
    assert parts[0].page == 1 and 'membership' in parts[0].text
    with pytest.raises(IngestionError,match='ENCRYPTED_PDF'):
        parse_and_chunk(pdf_bytes(True),'.pdf')
    with pytest.raises(IngestionError,match='INVALID_UTF8'):
        parse_and_chunk(b'\xff','.md')


def test_failure_keeps_old_version_and_manual_retry(environment):
    client, engine, ids = environment
    owner = login(client)
    first = upload(client,ids,owner).json()
    worker = worker_for(client,engine)
    worker.run_once()
    bad = upload(client,ids,owner,text=b'\xff',document=first['document_id']).json()
    worker.run_once()
    task = client.get('/api/tasks/'+bad['task_id'],headers=owner).json()
    assert task['status'] == 'failed' and task['error_code'] == 'INVALID_UTF8'
    docs = client.get('/api/projects/'+ids['project']+'/documents',headers=owner).json()
    assert docs[0]['current_version_id'] == first['version_id']
    assert client.post('/api/tasks/'+bad['task_id']+'/retry',headers=owner).status_code == 200
    assert client.get('/api/tasks/'+bad['task_id'],headers=owner).json()['attempts'] == 0


def test_out_of_order_publication_and_replay_idempotence(environment):
    client, engine, ids = environment
    owner = login(client)
    first = upload(client,ids,owner).json()
    second = upload(client,ids,owner,text='Newer project rules.',document=first['document_id']).json()
    worker = worker_for(client,engine)
    one,two = worker.claim(),worker.claim()
    assert one and two
    worker.process(*two)
    worker.process(*one)
    worker.process(*two)
    with Session(engine) as db:
        assert db.get(Document,UUID(first['document_id'])).current_version_id == UUID(second['version_id'])
        assert db.get(DocumentVersion,UUID(second['version_id'])).status == 'ready'
        assert db.scalar(select(func.count()).select_from(Chunk).where(Chunk.version_id == UUID(second['version_id']))) == 1


def test_lease_reclaim_rejects_stale_worker(environment):
    client, engine, ids = environment
    owner = login(client)
    record = upload(client,ids,owner).json()
    worker = worker_for(client,engine)
    old = worker.claim()
    assert worker.claim() is None
    with Session(engine) as db,db.begin():
        db.get(IngestionTask,old[0]).lease_until = utcnow()-timedelta(seconds=1)
    new = worker.claim()
    assert new[0] == old[0] and new[1] != old[1]
    worker.process(*old)
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0
    worker.process(*new)
    assert client.get('/api/tasks/'+record['task_id'],headers=owner).json()['status'] == 'succeeded'


def test_withdraw_prevents_inflight_publication(environment):
    client, engine, ids = environment
    owner = login(client)
    record = upload(client,ids,owner).json()
    worker = worker_for(client,engine)
    job = worker.claim()
    path = '/api/documents/'+record['document_id']
    assert client.post(path+'/withdraw',headers=owner).status_code == 200
    worker.process(*job)
    assert client.get(path+'/source?version_id='+record['version_id'],headers=owner).status_code == 404
    assert client.get(path+'/chunks',headers=owner).status_code == 404
    with Session(engine) as db:
        assert db.get(IngestionTask,UUID(record['task_id'])).status == 'cancelled'
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_withdraw_during_embedding_cannot_publish(environment):
    client,engine,ids = environment
    owner = login(client)
    record = upload(client,ids,owner).json()
    class WithdrawModel(FakeEmbedding):
        def embed(self,texts):
            assert client.post('/api/documents/'+record['document_id']+'/withdraw',headers=owner).status_code == 200
            return super().embed(texts)
    worker_for(client,engine,WithdrawModel()).run_once()
    with Session(engine) as db:
        assert db.get(IngestionTask,UUID(record['task_id'])).status == 'cancelled'
        assert db.scalar(select(func.count()).select_from(Chunk)) == 0


def test_pdf_end_to_end_and_file_integrity(environment):
    client,engine,ids = environment
    owner = login(client)
    good = upload(client,ids,owner,text=pdf_bytes(),name='sample.pdf').json()
    worker_for(client,engine).run_once()
    parts = client.get('/api/documents/'+good['document_id']+'/chunks',headers=owner).json()
    assert parts['items'][0]['page'] == 1 and 'membership' in parts['items'][0]['text']
    changed = upload(client,ids,owner).json()
    with Session(engine) as db:
        version = db.get(DocumentVersion,UUID(changed['version_id']))
        storage_path(client.app.state.settings.storage_dir,version.storage_key).write_bytes(b'changed')
    worker_for(client,engine).run_once()
    result = client.get('/api/tasks/'+changed['task_id'],headers=owner).json()
    assert result['status']=='failed' and result['error_code']=='FILE_CHANGED'


def test_exhausted_lease_becomes_failed(environment):
    client,engine,ids = environment
    owner = login(client)
    record = upload(client,ids,owner).json()
    worker = worker_for(client,engine)
    job = worker.claim()
    with Session(engine) as db,db.begin():
        task = db.get(IngestionTask,job[0])
        task.attempts = 3
        task.lease_until = utcnow()-timedelta(seconds=1)
    assert worker.claim() is None
    result = client.get('/api/tasks/'+record['task_id'],headers=owner).json()
    assert result['status']=='failed' and result['error_code']=='LEASE_EXHAUSTED'


def test_transient_failure_requeues_then_exhausts(environment):
    client, engine, ids = environment
    owner = login(client)
    record = upload(client,ids,owner).json()
    class DownModel:
        model = 'unavailable'
        def embed(self,texts):
            raise IngestionError('EMBEDDING_UNAVAILABLE',retryable=True)
    worker = worker_for(client,engine,DownModel())
    for attempt in range(3):
        assert worker.run_once()
        with Session(engine) as db,db.begin():
            task = db.get(IngestionTask,UUID(record['task_id']))
            assert task.status == ('failed' if attempt==2 else 'queued')
            task.available_at = utcnow()-timedelta(seconds=1)
    assert worker.claim() is None


def test_two_workers_claim_distinct_tasks(environment):
    client, engine, ids = environment
    owner = login(client)
    for _ in range(2):
        assert upload(client,ids,owner).status_code == 202
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = list(pool.map(lambda _: worker_for(client,engine).claim(),range(2)))
    assert jobs[0][0] != jobs[1][0]
