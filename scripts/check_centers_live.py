"""Synthetic-only live checks: large/encrypted PDF, tasks, audit and eval draft."""
from io import BytesIO
import json
from pathlib import Path
import time
import uuid

import httpx
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject,DictionaryObject,NameObject

ROOT=Path(__file__).resolve().parents[1]


def pdf_bytes():
    writer=PdfWriter()
    page=writer.add_blank_page(width=612,height=792)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 700 Td (Project access requires valid membership.) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    result=BytesIO()
    writer.write(result)
    return result.getvalue()


def main():
    accounts=json.loads((ROOT/'.local/demo_accounts.json').read_text(encoding='utf-8'))['accounts']
    account=next(a for a in accounts if a['username']=='demo_admin')
    with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=100) as client:
        auth=client.post('/auth/login',json={'username':account['username'],'password':account['password']})
        auth.raise_for_status()
        headers={'Authorization':'Bearer '+auth.json()['access_token']}
        try:
            name='七板块与PDF验收（合成资料）-'+uuid.uuid4().hex[:6]
            team_response=client.get('/teams',headers=headers);team_response.raise_for_status()
            assert team_response.json(),'Demo account needs approved team membership'
            response=client.post('/projects',headers=headers,json={'name':name,'description':'纯合成测试；包含AES密码PDF和超过20MB附件PDF，不代表正式人工评测。','team_id':team_response.json()[0]['id']})
            response.raise_for_status()
            project=response.json()['id']
            local=ROOT/'.local/centers-check'
            local.mkdir(exist_ok=True)
            writer=PdfWriter(clone_from=BytesIO(pdf_bytes()))
            writer.encrypt('synthetic-file-only',algorithm='AES-256')
            encrypted=local/'synthetic-encrypted.pdf'
            writer.write(encrypted)
            with encrypted.open('rb') as file:
                wrong=client.post('/documents',headers=headers,data={'project_id':project,'allow_external_processing':'true','pdf_password':'deliberately-wrong'},files={'file':('加密合成样例.pdf',file,'application/pdf')})
            assert wrong.status_code==422 and '密码错误' in wrong.text
            writer=PdfWriter(clone_from=BytesIO(pdf_bytes()))
            writer.add_attachment('synthetic-padding.bin',b'x'*(21*1024*1024))
            large=local/'synthetic-large.pdf'
            writer.write(large)
            assert large.stat().st_size>20*1024*1024
            records=[]
            for path,password in [(encrypted,'synthetic-file-only'),(large,None)]:
                form={'project_id':project,'allow_external_processing':'true'}
                if password:
                    form['pdf_password']=password
                with path.open('rb') as file:
                    response=client.post('/documents',headers=headers,data=form,files={'file':(path.name,file,'application/pdf')})
                response.raise_for_status()
                record=response.json()
                assert record['decrypted']==bool(password)
                for _ in range(240):
                    task=client.get('/tasks/'+record['task_id'],headers=headers)
                    task.raise_for_status()
                    state=task.json()['status']
                    if state in {'succeeded','failed','cancelled'}:
                        break
                    time.sleep(.5)
                assert state=='succeeded','Synthetic PDF failed ingestion'
                records.append(record)
                print('PASS synthetic PDF upload/parse/real embedding: '+('AES-encrypted' if password else '>20MB'))
            chunks=client.get('/documents/'+records[0]['document_id']+'/chunks',headers=headers)
            chunks.raise_for_status()
            gold=chunks.json()['items'][0]['id']
            draft=client.post('/projects/'+project+'/evaluation-cases',headers=headers,json={'question':'What is required to access project documents?','gold_chunk_ids':[gold],'notes':'自动合成测试草稿，未经人工确认，不作为正式评测。','human_confirmed':False})
            draft.raise_for_status()
            tasks=client.get('/projects/'+project+'/tasks',headers=headers)
            tasks.raise_for_status()
            assert len(tasks.json())==2
            events=client.get('/projects/'+project+'/audit-events',headers=headers)
            events.raise_for_status()
            assert len(events.json())>=4
            assert 'synthetic-file-only' not in events.text
            report={'status':'passed','synthetic_only':True,'project_id':project,'project_name':name,'records':records,'draft_id':draft.json()['id'],'large_bytes':large.stat().st_size,'checked_at':time.time()}
            (ROOT/'.local/centers-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            print('PASS task/audit centers and unconfirmed evaluation draft; no human confirmation fabricated.')
        finally:
            client.post('/auth/logout',headers=headers)


if __name__=='__main__':
    main()
