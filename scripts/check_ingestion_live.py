"""Real local HTTP + independent Worker smoke check with synthetic Markdown only."""
import json
from pathlib import Path
import time
from contextlib import ExitStack

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = json.loads((ROOT / '.local' / 'demo_accounts.json').read_text(encoding='utf-8'))
    accounts = {row['username']: row for row in records['accounts']}
    headers = {}
    with httpx.Client(base_url='http://127.0.0.1:8000/api', timeout=10) as client, ExitStack() as cleanup:
        for name in ['demo_admin','demo_member','demo_other']:
            response = client.post('/auth/login', json={'username': name, 'password': accounts[name]['password']})
            response.raise_for_status()
            headers[name] = {'Authorization': 'Bearer '+response.json()['access_token']}
            cleanup.callback(lambda h=headers[name]: client.post('/auth/logout',headers=h))
        project_id = records['projects']['a']
        document_id = None
        versions = []
        for number in [1,2]:
            text = f'# 合成验收资料 v{number}\n\n这是系统自动生成的演示文本，不是真实科研资料。\n\n项目资料通过成员关系控制访问。当前版本号为 {number}。'
            path = '/documents' if document_id is None else f'/documents/{document_id}/versions'
            fields = {'allow_external_processing':'true'}
            if document_id is None:
                fields['project_id'] = project_id
                fields['title'] = '入库链路验收样例（合成资料）'
            response = client.post(path, headers=headers['demo_admin'], data=fields, files={'file':('synthetic-ingestion.md',text.encode('utf-8'))})
            response.raise_for_status()
            ids = response.json()
            document_id = ids['document_id']
            versions.append(ids['version_id'])
            for _ in range(90):
                task_response = client.get('/tasks/'+ids['task_id'],headers=headers['demo_admin'])
                task_response.raise_for_status()
                task = task_response.json()
                if task['status'] in {'succeeded','failed','cancelled'}:
                    break
                time.sleep(.5)
            assert task['status']=='succeeded', 'Worker task did not succeed: '+str(task.get('error_code'))
            chunks = client.get(f'/documents/{document_id}/chunks',headers=headers['demo_member'])
            chunks.raise_for_status()
            assert chunks.json()['version_id'] == ids['version_id']
            assert chunks.json()['total'] > 0
            print(f'PASS real Worker: version={number}, chunks={chunks.json()["total"]}')
        original = client.get(f'/documents/{document_id}/source',params={'version_id':versions[0]},headers=headers['demo_member'])
        original.raise_for_status()
        assert 'v1' in original.text
        other_projects = client.get('/projects',headers=headers['demo_other'])
        other_projects.raise_for_status()
        other_has_access = any(p['id']==project_id for p in other_projects.json())
        assert client.get(f'/documents/{document_id}/source',headers=headers['demo_other']).status_code == (200 if other_has_access else 404)
        print('PASS live source access matches current membership (existing demo memberships preserved)')
        output = {'status':'passed','document_id':document_id,'versions':versions,'project_id':project_id,'synthetic_only':True,'checked_at':time.time()}
        (ROOT / '.local' / 'ingestion-live-check.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    print('PASS source authorization, historical source and version switching. Synthetic sample retained for local demo.')


if __name__ == '__main__':
    main()
