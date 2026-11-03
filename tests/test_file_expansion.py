from dataclasses import replace
from io import BytesIO
from uuid import UUID

import pytest
from pypdf import PdfReader, PdfWriter
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.app.file_processing import isolated
from backend.app.ingestion import IngestionError, parse_and_chunk
from backend.app.models import AuditEvent, Document, DocumentVersion
from tests.conftest import login
from tests.test_documents import pdf_bytes, worker_for
from fastapi.testclient import TestClient
from backend.app.main import create_app


def encrypted_pdf():
    reader=PdfReader(BytesIO(pdf_bytes()))
    writer=PdfWriter(clone_from=reader)
    writer.encrypt('test-file-password',algorithm='AES-256')
    output=BytesIO()
    writer.write(output)
    return output.getvalue()


def send(client,ids,owner,content,password=None):
    data={'project_id':ids['project'],'allow_external_processing':'true'}
    if password is not None:
        data['pdf_password']=password
    return client.post('/api/documents',headers=owner,data=data,files={'file':('encrypted.pdf',content)})


def test_encrypted_pdf_password_errors_cleanup_and_decrypted_source(environment):
    client,engine,ids=environment
    owner=login(client)
    encrypted=encrypted_pdf()
    for password,message in [(None,'需要密码'),('wrong-password','密码错误')]:
        result=send(client,ids,owner,encrypted,password)
        assert result.status_code==422 and message in result.text
        assert 'wrong-password' not in result.text
        assert not list(client.app.state.settings.storage_dir.glob('*'))
    result=send(client,ids,owner,encrypted,'test-file-password')
    assert result.status_code==202, result.text
    record=result.json()
    assert record['decrypted'] is True
    worker_for(client,engine).run_once()
    assert client.get('/api/tasks/'+record['task_id'],headers=owner).json()['status']=='succeeded'
    source=client.get('/api/documents/'+record['document_id']+'/source',headers=owner)
    assert not PdfReader(BytesIO(source.content)).is_encrypted
    assert 'membership' in PdfReader(BytesIO(source.content)).pages[0].extract_text()
    with Session(engine) as db:
        event=db.scalar(select(AuditEvent))
        assert event.details=={'version':1,'decrypted':True}
        assert 'test-file-password' not in str(event.details)
        assert not any('password' in column.name for column in DocumentVersion.__table__.columns)


def test_upload_over_twenty_mb_and_configurable_limit(environment):
    client,engine,ids=environment
    owner=login(client)
    payload=b'x'*(21*1024*1024)
    response=client.post('/api/documents',headers=owner,data={'project_id':ids['project'],'allow_external_processing':'true'},files={'file':('large.md',payload)})
    assert response.status_code==202
    limits=client.get('/api/file-limits',headers=owner).json()
    assert limits['max_upload_bytes']==100*1024*1024
    worker_for(client,engine).run_once()
    assert client.get('/api/tasks/'+response.json()['task_id'],headers=owner).json()['error_code']=='TEXT_TOO_LARGE'
    client.app.state.settings=replace(client.app.state.settings,max_upload_bytes=1024)
    response=client.post('/api/documents',headers=owner,data={'project_id':ids['project'],'allow_external_processing':'true'},files={'file':('too-big.md',b'x'*1025)})
    assert response.status_code==413
    with Session(engine) as db:
        assert db.scalar(select(func.count()).select_from(Document))==1


def test_parser_resource_limits_and_process_deadline(environment,tmp_path):
    client,_,_=environment
    with pytest.raises(IngestionError,match='TEXT_TOO_LARGE'):
        parse_and_chunk(b'x'*101,'.md',max_chars=100)
    with pytest.raises(IngestionError,match='TOO_MANY_CHUNKS'):
        parse_and_chunk(b'x'*2000,'.md',max_chunks=1)
    source=tmp_path/'sample.pdf'
    source.write_bytes(pdf_bytes())
    with pytest.raises(IngestionError,match='PROCESSING_TIMEOUT'):
        isolated('parse',source,client.app.state.settings,timeout=.00001)
    with pytest.raises(IngestionError,match='TOO_MANY_PAGES'):
        parse_and_chunk(pdf_bytes(),'.pdf',max_pages=0)


def test_failed_preparation_never_changes_current_version(environment):
    client,engine,ids=environment
    owner=login(client)
    first=send(client,ids,owner,pdf_bytes()).json()
    worker_for(client,engine).run_once()
    failed=client.post('/api/documents/'+first['document_id']+'/versions',headers=owner,data={'allow_external_processing':'true','pdf_password':'wrong-password'},files={'file':('encrypted.pdf',encrypted_pdf())})
    assert failed.status_code==422
    with Session(engine) as db:
        doc=db.get(Document,UUID(first['document_id']))
        assert doc.current_version_id==UUID(first['version_id']) and doc.latest_version==1


def test_multipart_body_limit_before_spooling(environment):
    client,engine,_=environment
    app=create_app(replace(client.app.state.settings,max_upload_bytes=1024),engine)
    with TestClient(app) as limited:
        body=b'x'*(1024*1024+1025)
        response=limited.post('/api/documents',content=body,headers={'Content-Type':'application/octet-stream'})
        assert response.status_code==413 and response.headers['x-request-id']
        # Missing Content-Length must not bypass the receive-side counter.
        boundary='synthetic-boundary'
        chunks=[b'--'+boundary.encode()+b'\r\nContent-Disposition: form-data; name="file"; filename="x.md"\r\n\r\n',body,b'\r\n--'+boundary.encode()+b'--\r\n']
        response=limited.post('/api/documents',content=iter(chunks),headers={'Content-Type':'multipart/form-data; boundary='+boundary})
        assert response.status_code==413
