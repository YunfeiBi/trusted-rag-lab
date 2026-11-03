from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.app.generation import AnswerDraft, CitationValidationError, validate_claims
from backend.app.ingestion import IngestionError
from backend.app.models import QuestionRecord
from tests.conftest import login
from tests.test_documents import FakeEmbedding
from tests.test_search import FakeRerank, ingest


class FakeChat:
    model = 'test-chat'

    def __init__(self):
        self.calls = []
        self.hook = None
        self.draft = None

    def generate(self, question, evidence):
        self.calls.append(evidence)
        if self.hook:
            self.hook()
        if isinstance(self.draft, Exception):
            raise self.draft
        return self.draft or {'answerable': True, 'claims': [{'text': 'Members control access.', 'supports': [{'evidence_id': 'E1', 'quote': evidence[0]['text']}]}]}


@pytest.fixture
def qa(environment):
    client, engine, ids = environment
    owner = login(client)
    client.app.state.embedding_client = FakeEmbedding()
    client.app.state.rerank_client = FakeRerank()
    client.app.state.chat_client = FakeChat()
    return client, engine, ids, owner


def ask(client, ids, headers, **changes):
    return client.post('/api/questions', headers=headers, json={'project_id': ids['project'], 'question': 'membership', 'retrieval_mode': 'keyword', 'allow_external_processing': True, **changes})


def test_question_auth_consent_and_empty_corpus(qa):
    client, _, ids, owner = qa
    assert ask(client, ids, {}).status_code == 401
    assert ask(client, ids, login(client, 'outsider')).status_code == 404
    assert ask(client, ids, owner, allow_external_processing=False).status_code == 422
    assert ask(client, ids, owner, question=' ').status_code == 422
    data = ask(client, ids, owner).json()
    assert data['status'] == 'insufficient_evidence'
    assert not data['citations'] and not client.app.state.chat_client.calls


def test_answer_and_private_history(qa):
    client, engine, ids, owner = qa
    ingest(client, engine, ids, owner, 'membership controls document access')
    response = ask(client, ids, owner)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data['status'] == 'answered' and '[E1]' in data['answer']
    assert data['citations'][0]['quote'] == data['evidence'][0]['text']
    assert data['citation_check'] == 'reference_and_quote_only'
    assert client.get('/api/questions', headers=owner, params={'project_id': ids['project']}).json()[0]['id'] == data['id']
    member = login(client, 'member')
    assert client.get('/api/questions', headers=member, params={'project_id': ids['project']}).json() == []
    assert client.get('/api/questions/'+data['id'], headers=member).status_code == 404


@pytest.mark.parametrize('draft', [
    {'answerable': True, 'claims': [{'text': 'SECRET_DRAFT', 'supports': [{'evidence_id': 'E99', 'quote': 'membership controls access'}]}]},
    {'answerable': True, 'claims': [{'text': 'SECRET_DRAFT', 'supports': [{'evidence_id': 'E1', 'quote': 'fabricated quote missing'}]}]},
    {'answerable': True, 'claims': [{'text': 'SECRET_DRAFT [E1]', 'supports': [{'evidence_id': 'E1', 'quote': 'membership controls access'}]}]},
    {'answerable': True, 'claims': []},
])
def test_rejected_draft_never_persisted(qa, draft):
    client, engine, ids, owner = qa
    ingest(client, engine, ids, owner, 'membership controls access')
    client.app.state.chat_client.draft = draft
    response = ask(client, ids, owner)
    assert response.json()['status'] == 'invalid_citations'
    assert 'SECRET_DRAFT' not in response.text
    with Session(engine) as db:
        row = db.scalar(select(QuestionRecord))
        assert 'SECRET_DRAFT' not in row.answer and row.claims == []


@pytest.mark.parametrize('failure', ['insufficient', 'GENERATION_HTTP_ERROR', 'GENERATION_INVALID_RESPONSE'])
def test_model_refusal_and_safe_failure(qa, failure):
    client, engine, ids, owner = qa
    ingest(client, engine, ids, owner, 'membership controls access')
    client.app.state.chat_client.draft = {'answerable': False, 'claims': []} if failure == 'insufficient' else IngestionError(failure)
    data = ask(client, ids, owner).json()
    expected = {'insufficient': 'insufficient_evidence', 'GENERATION_HTTP_ERROR': 'generation_unavailable', 'GENERATION_INVALID_RESPONSE': 'invalid_citations'}
    assert data['status'] == expected[failure] and data['claims'] == []


@pytest.mark.parametrize('change', ['withdraw', 'version'])
def test_evidence_changes_during_generation_and_history(qa, change):
    client, engine, ids, owner = qa
    doc = ingest(client, engine, ids, owner, 'membership controls access OLD_SECRET')
    first = ask(client, ids, owner).json()

    def mutate():
        if change == 'withdraw':
            assert client.post('/api/documents/'+doc['document_id']+'/withdraw', headers=owner).status_code == 200
        else:
            ingest(client, engine, ids, owner, 'membership NEW current version', document=doc['document_id'])

    client.app.state.chat_client.hook = mutate
    data = ask(client, ids, owner).json()
    assert data['status'] == 'evidence_changed' and not data['evidence']
    assert 'OLD_SECRET' not in str(data)
    history = client.get('/api/questions/'+first['id'], headers=owner).json()
    assert history['status'] == 'evidence_changed' and not history['citations']
    assert 'OLD_SECRET' not in str(history)


@pytest.mark.parametrize('logout', [False, True])
def test_revocation_during_generation_prevents_save(qa, logout):
    client, engine, ids, owner = qa
    ingest(client, engine, ids, owner, 'membership controls access SECRET')
    member = login(client, 'member')

    def mutate():
        if logout:
            assert client.post('/api/auth/logout', headers=member).status_code == 204
        else:
            assert client.delete('/api/projects/'+ids['project']+'/members/'+ids['member'], headers=owner).status_code == 204

    client.app.state.chat_client.hook = mutate
    response = ask(client, ids, member)
    assert response.status_code == 404 and 'SECRET' not in response.text
    with Session(engine) as db:
        assert db.scalar(select(QuestionRecord).where(QuestionRecord.user_id == UUID(ids['member']))) is None


def test_quote_validation_is_not_entailment():
    evidence = [{'evidence_id': 'E1', 'text': 'Membership controls access.'}]
    draft = AnswerDraft.model_validate({'answerable': True, 'claims': [{'text': 'A claim still requires human semantic review.', 'supports': [{'evidence_id': 'E1', 'quote': evidence[0]['text']}]}]})
    assert validate_claims(draft, evidence)[0]


def test_pdf_layout_whitespace_preserves_exact_source_quote():
    source = '申请方式：家庭经济困\n难学生可以申请国家助学贷款，\u3000并向学院申请临时困难补助。'
    quote = '家庭经济困难学生可以申请国家助学贷款，并向学院申请临时困难补助'
    draft = {'answerable': True, 'claims': [{'text': '可以申请国家助学贷款和临时困难补助。',
        'supports': [{'evidence_id': 'E1', 'quote': quote}]}]}
    answerable, answer, claims, citations = validate_claims(draft, [{'evidence_id': 'E1', 'text': source}])
    assert answerable and '可以申请' in answer and claims[0]['evidence_ids'] == ['E1']
    assert citations[0]['quote'] in source
    assert '\n' in citations[0]['quote'] and '\u3000' in citations[0]['quote']


@pytest.mark.parametrize('quote,code', [
    ('家庭经济困难学生可以申请国家奖学金', 'QUOTE_NOT_IN_EVIDENCE'),
    ('家庭经济困难学生……可以申请国家助学贷款', 'QUOTE_NOT_IN_EVIDENCE'),
    ('家庭经济困难学生，可以申请国家助学贷款', 'QUOTE_NOT_IN_EVIDENCE'),
    ('家 庭 经 济 困 难', 'QUOTE_TOO_SHORT'),
])
def test_whitespace_matching_still_rejects_changed_or_short_quotes(quote, code):
    draft = {'answerable': True, 'claims': [{'text': 'Rejected draft.',
        'supports': [{'evidence_id': 'E1', 'quote': quote}]}]}
    with pytest.raises(CitationValidationError) as failure:
        validate_claims(draft, [{'evidence_id': 'E1', 'text': '家庭经济困\n难学生可以申请国家助学贷款'}])
    assert failure.value.code == code


def test_quote_failure_returns_safe_reason_without_draft(qa):
    client, engine, ids, owner = qa
    ingest(client, engine, ids, owner, 'membership controls access')
    client.app.state.chat_client.draft = {'answerable': True, 'claims': [{'text': 'SECRET_DRAFT',
        'supports': [{'evidence_id': 'E1', 'quote': 'fabricated quote missing'}]}]}
    response = ask(client, ids, owner)
    data = response.json()
    assert data['status'] == 'invalid_citations'
    assert data['details']['warnings'][-1]['code'] == 'QUOTE_NOT_IN_EVIDENCE'
    assert 'SECRET_DRAFT' not in response.text
    with Session(engine) as db:
        row = db.scalar(select(QuestionRecord))
        assert row.claims == [] and row.citations == [] and 'SECRET_DRAFT' not in row.answer
