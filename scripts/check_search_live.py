"""Real search smoke check restricted to a newly created synthetic-only project."""
from contextlib import ExitStack
import json
from pathlib import Path
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = json.loads((ROOT / '.local' / 'demo_accounts.json').read_text(encoding='utf-8'))
    accounts = {row['username']:row for row in records['accounts']}
    with httpx.Client(base_url='http://127.0.0.1:8000/api',timeout=50) as client, ExitStack() as cleanup:
        auth = {}
        for username in ['demo_admin','demo_member']:
            response = client.post('/auth/login',json={'username':username,'password':accounts[username]['password']})
            response.raise_for_status()
            headers = {'Authorization':'Bearer '+response.json()['access_token']}
            auth[username] = headers
            cleanup.callback(lambda h=headers:client.post('/auth/logout',headers=h))
        name = '检索链路验收（合成资料）-'+uuid.uuid4().hex[:6]
        response = client.post('/projects',headers=auth['demo_admin'],json={'name':name,'description':'自动生成的隔离合成资料，仅用于本机检索验收，不含真实科研内容。'})
        response.raise_for_status()
        project_id = response.json()['id']
        documents = []
        for filename,text in [
            ('访问权限规范.md','# 访问规范\n\n项目资料仅项目成员可访问。管理员可以添加或撤销成员。\n\n项目版本须在构建成功后发布。'),
            ('实验指标说明.md','# 模型评测\n\n分类模型报告准确率、召回率和F1分数。'),
        ]:
            response = client.post('/documents',headers=auth['demo_admin'],data={'project_id':project_id,'allow_external_processing':'true'},files={'file':(filename,text.encode('utf-8'))})
            response.raise_for_status()
            document = response.json()
            documents.append(document)
            for attempt in range(90):
                task = client.get('/tasks/'+document['task_id'],headers=auth['demo_admin'])
                task.raise_for_status()
                status = task.json()
                if status['status'] in {'succeeded','failed','cancelled'}:
                    break
                time.sleep(.5)
            assert status['status']=='succeeded','Synthetic task did not succeed'
        query = '项目资料通过什么方式控制访问权限？'
        summaries = []
        for mode in ['keyword','vector','hybrid']:
            response = client.post('/search',headers=auth['demo_admin'],json={'project_id':project_id,'query':query,'mode':mode,'top_k':5,'use_rerank':True,'allow_external_processing':mode!='keyword'})
            response.raise_for_status()
            data = response.json()
            assert data['results'],'No synthetic evidence found'
            assert {r['document_id'] for r in data['results']} <= {d['document_id'] for d in documents},'Search escaped synthetic scope'
            if mode=='hybrid':
                assert data['rerank_status']=='applied' and data['candidates']['keyword']>0 and data['candidates']['vector']>0
                valid = client.post('/search/validate',headers=auth['demo_admin'],json={'project_id':project_id,'chunk_ids':[r['chunk_id'] for r in data['results']]})
                valid.raise_for_status()
                assert set(valid.json()['valid_chunk_ids'])=={r['chunk_id'] for r in data['results']}
                original = client.get(data['results'][0]['source_path'],headers=auth['demo_admin'])
                original.raise_for_status()
            summaries.append({'mode':mode,'status':data['status'],'results':len(data['results']),'candidates':data['candidates'],'rerank_status':data['rerank_status'],'timings_ms':data['timings_ms']})
            print(f'PASS live search mode={mode} results={len(data["results"])} rerank={data["rerank_status"]}')
        denied = client.post('/search',headers=auth['demo_member'],json={'project_id':project_id,'query':'权限','allow_external_processing':True})
        assert denied.status_code==404
        report = {'status':'passed','synthetic_only':True,'project_id':project_id,'project_name':name,'documents':documents,'summaries':summaries,'checked_at':time.time()}
        (ROOT / '.local' / 'search-live-check.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
        print('PASS live evidence validation, source fetch and non-member denial. Synthetic-only project retained.')


if __name__ == '__main__':
    main()
