import json

import httpx
import pytest

from backend.app.agent_workflow import run_agent_workflow
from backend.app.generation import ChatClient, ReviewDecision
from backend.app.ingestion import IngestionError

EVIDENCE = [{'evidence_id': 'E1', 'text': 'membership controls document access'}]


class ScriptedAgents:
    model = 'scripted-offline'

    def __init__(self, reviews=None, *, invalid_quote=False, hook=None):
        self.reviews = list(reviews or [{'action': 'accept', 'reason': 'accepted'}])
        self.invalid_quote = invalid_quote
        self.calls = []
        self.hook = hook

    def plan(self, question, feedback, **kwargs):
        self.calls.append(('plan', feedback))
        return {'action': 'search', 'query': question + (' access' if feedback else '')}

    def write(self, question, evidence, feedback, **kwargs):
        self.calls.append(('write', feedback))
        if self.hook:
            self.hook()
        return {'answerable': True, 'claims': [{'text': 'DRAFT_NOT_FOR_TRACE', 'supports': [
            {'evidence_id': 'E1', 'quote': 'fabricated invalid quote' if self.invalid_quote else evidence[0]['text']}]}]}

    def review(self, question, evidence, draft, **kwargs):
        self.calls.append(('review', ''))
        return self.reviews.pop(0) if len(self.reviews) > 1 else self.reviews[0]


def search_result(evidence=None):
    return {'results': EVIDENCE if evidence is None else evidence, 'requested_mode': 'keyword',
            'effective_mode': 'keyword', 'rerank_status': 'not_requested', 'candidates': {},
            'timings_ms': {}, 'warnings': []}


def execute(client=None, **kwargs):
    return run_agent_workflow('membership', client or ScriptedAgents(),
        kwargs.pop('search', lambda query: search_result()), kwargs.pop('guard', lambda evidence: True), **kwargs)


def test_success_handoffs_and_safe_trace():
    result = execute()
    assert result['status'] == 'answered'
    trace = result['details']['agent_workflow']['trace']
    assert [e['role'] for e in trace if e['event'] == 'model_call'] == ['retriever', 'writer', 'reviewer']
    assert any(e['event'] == 'authorized_search' for e in trace)
    assert 'DRAFT_NOT_FOR_TRACE' not in json.dumps(result['details'])
    assert result['details']['agent_workflow']['model_calls'] == 3


@pytest.mark.parametrize('action,reason,expected_calls', [
    ('rewrite', 'unsupported_claim', 5), ('retrieve', 'missing_evidence', 6)])
def test_reviewer_feedback_routes_to_writer_or_retrieval(action, reason, expected_calls):
    client = ScriptedAgents([{'action': action, 'reason': reason}, {'action': 'accept', 'reason': 'accepted'}])
    result = execute(client)
    assert result['status'] == 'answered'
    assert result['details']['agent_workflow']['model_calls'] == expected_calls
    assert any(feedback == reason for _, feedback in client.calls)


def test_rejection_stops_and_never_returns_draft():
    result = execute(ScriptedAgents([{'action': 'rewrite', 'reason': 'unsupported_claim'}]))
    assert result['status'] == 'review_rejected'
    assert not result['claims'] and not result['citations'] and not result['answer']
    assert 'DRAFT_NOT_FOR_TRACE' not in json.dumps(result)


def test_quote_gate_prevents_reviewer_and_bounds_bad_drafts():
    client = ScriptedAgents(invalid_quote=True)
    result = execute(client)
    assert result['status'] == 'invalid_citations'
    assert len(client.calls) == 3 and not any(role == 'review' for role, _ in client.calls)
    assert 'DRAFT_NOT_FOR_TRACE' not in json.dumps(result)


def test_empty_evidence_replans_once_and_never_writes():
    client = ScriptedAgents()
    result = execute(client, search=lambda query: search_result([]))
    assert result['status'] == 'insufficient_evidence'
    assert client.calls == [('plan', ''), ('plan', 'missing_evidence')]


def test_permissions_or_versions_changed_before_review():
    current = [True]
    result = execute(ScriptedAgents(hook=lambda: current.__setitem__(0, False)), guard=lambda evidence: current[0])
    assert result['status'] == 'evidence_changed' and not result['evidence'] and not result['answer']


def test_call_budget_stops_before_next_model():
    result = execute(max_model_calls=1)
    assert result['status'] == 'workflow_stopped'
    assert result['details']['agent_workflow']['model_calls'] == 1
    assert result['details']['agent_workflow']['trace'][-1]['code'] == 'MODEL_CALL_BUDGET'


def test_deadline_checked_after_tool_before_next_model():
    now = [0]
    def slow_search(query):
        now[0] = 121
        return search_result()
    result = execute(search=slow_search, clock=lambda: now[0])
    assert result['status'] == 'workflow_stopped' and not result['claims']
    assert result['details']['agent_workflow']['trace'][-1]['code'] == 'WORKFLOW_DEADLINE'


def test_model_failure_is_terminal_without_upstream_text():
    client = ScriptedAgents()
    def fail(*args, **kwargs):
        raise IngestionError('GENERATION_UNAVAILABLE')
    client.review = fail
    result = execute(client)
    assert result['status'] == 'generation_unavailable'
    assert 'DRAFT_NOT_FOR_TRACE' not in json.dumps(result)


@pytest.mark.parametrize('schema', [
    {'action': 'accept', 'reason': 'unsupported_claim'},
    {'action': 'shell', 'reason': 'missing_evidence'},
    {'action': 'accept', 'reason': 'accepted', 'command': 'bad'},
])
def test_reviewer_rejects_inconsistent_or_extra_tool_fields(schema):
    with pytest.raises(ValueError):
        ReviewDecision.model_validate(schema)


def test_real_adapter_sends_separate_roles_and_feedback(monkeypatch):
    captured = []
    payloads = [ {'action': 'search', 'query': 'membership'},
        {'answerable': True, 'claims': [{'text': 'Access is controlled.', 'supports': [
            {'evidence_id': 'E1', 'quote': EVIDENCE[0]['text']}]}]},
        {'action': 'accept', 'reason': 'accepted'}]
    def respond(request):
        captured.append(json.loads(request.content))
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(payloads.pop(0))}}]})
    original = httpx.Client
    monkeypatch.setattr(httpx, 'Client', lambda **kwargs: original(transport=httpx.MockTransport(respond), **kwargs))
    client = ChatClient()
    client.base, client.key, client.model = 'https://model.invalid/v1', 'test-only', 'test'
    result = execute(client)
    assert result['status'] == 'answered'
    assert len({call['messages'][0]['content'] for call in captured}) == 3
    assert all(call['response_format']['type'] == 'json_object' for call in captured)


def test_request_tool_cannot_select_project_scope():
    from backend.app.generation import RetrievalPlan
    with pytest.raises(ValueError):
        RetrievalPlan.model_validate({'action': 'search', 'query': 'test', 'project_id': 'other'})
