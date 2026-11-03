"""Real QA smoke check: exclusively a new project containing synthetic text."""
from contextlib import ExitStack
import json
from pathlib import Path
import time
import uuid

import httpx

ROOT = Path(__file__).resolve().parents[1]


def main():
    records = json.loads((ROOT / '.local/demo_accounts.json').read_text(encoding='utf-8'))
    accounts = {a['username']: a for a in records['accounts']}
    with httpx.Client(base_url='http://127.0.0.1:8000/api', timeout=100) as client, ExitStack() as cleanup:
        auth = {}
        for username in ['demo_admin', 'demo_member']:
            response = client.post('/auth/login', json={'username': username, 'password': accounts[username]['password']})
            response.raise_for_status()
            headers = {'Authorization': 'Bearer '+response.json()['access_token']}
            auth[username] = headers
            cleanup.callback(lambda h=headers: client.post('/auth/logout', headers=h))
        name = '引用问答验收（合成资料）-'+uuid.uuid4().hex[:6]
        team_response=client.get('/teams',headers=auth['demo_admin']);team_response.raise_for_status()
        assert team_response.json(),'Demo account needs approved team membership'
        response = client.post('/projects', headers=auth['demo_admin'], json={'name': name, 'description': '自动生成的纯合成资料，仅用于本机引用问答验收。','team_id':team_response.json()[0]['id']})
        response.raise_for_status()
        project_id = response.json()['id']
        documents = []
        for filename, text in [('成员管理规范.md', '# 成员管理\n\n项目管理员可以添加或撤销成员。只有当前项目成员可以访问项目资料。'), ('版本发布规范.md', '# 版本发布\n\n资料新版本必须在解析和向量构建成功后发布。历史版本不进入当前问答检索。')]:
            response = client.post('/documents', headers=auth['demo_admin'], data={'project_id': project_id, 'allow_external_processing': 'true'}, files={'file': (filename, text.encode('utf-8'))})
            response.raise_for_status()
            doc = response.json()
            documents.append(doc)
            for _ in range(120):
                task = client.get('/tasks/'+doc['task_id'], headers=auth['demo_admin'])
                task.raise_for_status()
                state = task.json()['status']
                if state in {'succeeded','failed','cancelled'}:
                    break
                time.sleep(.5)
            assert state == 'succeeded', 'Synthetic ingestion failed'
        results = []
        for question, expected in [('项目管理员能做哪些成员管理操作？', 'answered'), ('本项目年度预算多少钱？', 'insufficient_evidence')]:
            response = client.post('/questions', headers=auth['demo_admin'], json={'project_id': project_id, 'question': question, 'allow_external_processing': True})
            response.raise_for_status()
            data = response.json()
            assert data['status'] == expected, 'Unexpected QA status: '+data['status']
            assert {e['document_id'] for e in data['evidence']} <= {d['document_id'] for d in documents}
            if expected == 'answered':
                assert data['citations'] and data['citation_check'] == 'reference_and_quote_only'
                assert data['details']['retrieval']['rerank_status'] == 'applied'
                for citation in data['citations']:
                    evidence = next(e for e in data['evidence'] if e['evidence_id'] == citation['evidence_id'])
                    assert ' '.join(citation['quote'].split()) in ' '.join(evidence['text'].split())
                source = client.get(data['evidence'][0]['source_path'], headers=auth['demo_admin'])
                source.raise_for_status()
            assert client.get('/questions/'+data['id'], headers=auth['demo_member']).status_code == 404
            results.append({'id': data['id'], 'status': data['status'], 'citations': len(data['citations']), 'generation_ms': data['details']['generation_ms']})
            print('PASS real QA: '+expected)
        history = client.get('/questions', headers=auth['demo_admin'], params={'project_id': project_id})
        history.raise_for_status()
        assert len(history.json()) == 2
        report = {'status': 'passed', 'synthetic_only': True, 'project_id': project_id, 'project_name': name, 'documents': documents, 'results': results, 'checked_at': time.time()}
        (ROOT / '.local/qa-live-check.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print('PASS private history and source access; synthetic QA project retained.')


if __name__ == '__main__':
    main()
