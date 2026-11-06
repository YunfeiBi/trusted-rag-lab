from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.models import QuestionRecord
from tests.conftest import login
from tests.test_questions import ask
from tests.test_documents import FakeEmbedding
from tests.test_search import ingest, FakeRerank
from tests.test_agent_workflow import ScriptedAgents


@pytest.fixture
def agent_qa(environment):
    client, engine, ids = environment
    client.app.state.embedding_client = FakeEmbedding()
    client.app.state.rerank_client = FakeRerank()
    client.app.state.chat_client = ScriptedAgents()
    return client, engine, ids, login(client)


def test_multi_agent_api_success_trace_and_private_history(agent_qa):
    client, engine, ids, owner = agent_qa
    ingest(client, engine, ids, owner, 'membership controls document access')
    client.app.state.chat_client = ScriptedAgents()
    response = ask(client, ids, owner, workflow_mode='multi_agent')
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'answered' and data['details']['agent_workflow']['model_calls'] == 3
    assert data['details']['workflow_mode'] == 'multi_agent'
    assert client.get('/api/questions/'+data['id'], headers=login(client, 'member')).status_code == 404


def test_multi_agent_rejected_draft_absent_from_database(agent_qa):
    client, engine, ids, owner = agent_qa
    ingest(client, engine, ids, owner, 'membership controls document access')
    client.app.state.chat_client = ScriptedAgents([{'action': 'refuse', 'reason': 'missing_evidence'}])
    response = ask(client, ids, owner, workflow_mode='multi_agent')
    assert response.json()['status'] == 'review_rejected'
    assert 'DRAFT_NOT_FOR_TRACE' not in response.text
    with Session(engine) as db:
        row = db.scalar(select(QuestionRecord))
        assert 'DRAFT_NOT_FOR_TRACE' not in str(row.details) + row.answer
        assert not row.claims and not row.citations


@pytest.mark.parametrize('change', ['revoke', 'withdraw'])
def test_multi_agent_scope_changes_during_write(agent_qa, change):
    client, engine, ids, owner = agent_qa
    doc = ingest(client, engine, ids, owner, 'membership controls document access')
    member = login(client, 'member')
    def mutate():
        if change == 'revoke':
            client.delete('/api/projects/'+ids['project']+'/members/'+ids['member'], headers=owner)
        else:
            client.post('/api/documents/'+doc['document_id']+'/withdraw', headers=owner)
    client.app.state.chat_client = ScriptedAgents(hook=mutate)
    response = ask(client, ids, member, workflow_mode='multi_agent')
    if change == 'revoke':
        assert response.status_code == 404
        with Session(engine) as db:
            assert db.scalar(select(QuestionRecord).where(QuestionRecord.user_id == UUID(ids['member']))) is None
    else:
        assert response.json()['status'] == 'evidence_changed'
        assert not response.json()['evidence']
    assert 'DRAFT_NOT_FOR_TRACE' not in response.text


def test_multi_agent_consent_and_authorization_still_required(agent_qa):
    client, _, ids, owner = agent_qa
    assert ask(client, ids, owner, workflow_mode='multi_agent', allow_external_processing=False).status_code == 422
    assert ask(client, ids, login(client, 'outsider'), workflow_mode='multi_agent').status_code == 404
    assert ask(client, ids, {}, workflow_mode='multi_agent').status_code == 401


def test_budget_stop_is_saved_as_safe_terminal_status(agent_qa, monkeypatch):
    from backend.app import questions
    from backend.app.agent_workflow import run_agent_workflow
    client, engine, ids, owner = agent_qa
    ingest(client, engine, ids, owner, 'membership controls document access')
    monkeypatch.setattr(questions, 'run_agent_workflow', lambda *args:
        run_agent_workflow(*args, max_model_calls=1))
    response = ask(client, ids, owner, workflow_mode='multi_agent')
    assert response.status_code == 200, response.text
    assert response.json()['status'] == 'workflow_stopped'
    with Session(engine) as db:
        row = db.scalar(select(QuestionRecord))
        assert row.status == 'workflow_stopped' and not row.claims
