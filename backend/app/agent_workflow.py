"""Bounded retrieval/writer/reviewer collaboration, with no rejected-draft persistence.

LangGraph supplies the explicit shared state and conditional handoffs. Only the
retrieval role has a server-bound search tool. This is a controlled agent workflow,
not an autonomous team or a durable/resumable background job.
"""
from time import perf_counter
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from pydantic import ValidationError

from .generation import AnswerDraft, CitationValidationError, RetrievalPlan, ReviewDecision, validate_claims
from .ingestion import IngestionError


class State(TypedDict, total=False):
    round: int
    feedback: str
    route: str


class WorkflowStop(Exception):
    def __init__(self, code):
        self.code = code


class EvidenceChanged(Exception):
    pass


def run_agent_workflow(question, client, search, guard, *, max_rounds=2,
                       max_model_calls=6, max_seconds=120, clock=perf_counter):
    """Callbacks enforce caller identity/project; no model-specified permissions.

    guard checks session + evidence and must release its DB transaction before
    returning. Deadline is checked around every stage, not a process hard kill.
    Each model call also receives the remaining deadline as an HTTP timeout.
    """
    if not 1 <= max_rounds <= 2 or not 1 <= max_model_calls <= 6 or max_seconds <= 0:
        raise ValueError('Invalid workflow limits')
    started = clock()
    evidence, trace, warnings = [], [], []
    result = {'status': 'insufficient_evidence', 'answer': '', 'claims': [], 'citations': []}
    retrieval = {}
    model_calls = 0
    last_failure = 'insufficient_evidence'
    draft = None

    def check():
        if clock() - started >= max_seconds:
            raise WorkflowStop('WORKFLOW_DEADLINE')
        if not guard(evidence):
            raise EvidenceChanged()

    def call(role, fn):
        nonlocal model_calls
        check()
        if model_calls >= max_model_calls:
            raise WorkflowStop('MODEL_CALL_BUDGET')
        model_calls += 1
        timer = clock()
        trace.append({'role': role, 'event': 'model_call', 'call': model_calls})
        value = fn(min(45, max(.01, max_seconds - (clock() - started))))
        check()
        trace[-1]['elapsed_ms'] = round((clock() - timer) * 1000, 2)
        return value

    def retrieve_node(state):
        nonlocal evidence, retrieval
        round_number = state.get('round', 0) + 1
        plan = RetrievalPlan.model_validate(call('retriever', lambda timeout:
            client.plan(question, state.get('feedback', ''), timeout=timeout)))
        if plan.action == 'abstain':
            return {'round': round_number, 'route': 'stop'}
        check()
        retrieved = search(plan.query)
        check()
        evidence = retrieved['results']
        retrieval = {k: retrieved[k] for k in (
            'requested_mode', 'effective_mode', 'rerank_status', 'candidates', 'timings_ms')}
        warnings.extend(retrieved['warnings'])
        trace.append({'role': 'retriever', 'event': 'authorized_search',
                      'round': round_number, 'evidence_count': len(evidence)})
        check()
        if evidence:
            return {'round': round_number, 'route': 'write', 'feedback': ''}
        return {'round': round_number, 'route': 'retrieve' if round_number < max_rounds else 'stop',
                'feedback': 'missing_evidence'}

    def write_node(state):
        nonlocal draft, last_failure
        draft = None
        try:
            draft = AnswerDraft.model_validate(call('writer', lambda timeout:
                client.write(question, evidence, state.get('feedback', ''), timeout=timeout)))
            if not draft.answerable:
                last_failure = 'insufficient_evidence'
                return {'route': 'stop'}
            validate_claims(draft, evidence)
            return {'route': 'review'}
        except (CitationValidationError, ValidationError) as exc:
            code = exc.code if isinstance(exc, CitationValidationError) else 'GENERATION_INVALID_RESPONSE'
        except IngestionError as exc:
            if exc.code != 'GENERATION_INVALID_RESPONSE':
                raise
            code = exc.code
        draft = None
        last_failure = 'invalid_citations'
        trace.append({'role': 'validator', 'event': 'rejected', 'code': code})
        # Rewriting consumes a round too; no unbounded loop on malformed output.
        if state['round'] < max_rounds:
            return {'round': state['round'] + 1, 'route': 'write', 'feedback': code}
        return {'route': 'stop'}

    def review_node(state):
        nonlocal last_failure
        review = ReviewDecision.model_validate(call('reviewer', lambda timeout:
            client.review(question, evidence, draft, timeout=timeout)))
        trace.append({'role': 'reviewer', 'event': review.action, 'reason': review.reason})
        if review.action == 'accept':
            check()
            _, answer, claims, citations = validate_claims(draft, evidence)
            result.update(status='answered', answer=answer, claims=claims, citations=citations)
            return {'route': 'stop'}
        last_failure = 'review_rejected'
        if review.action == 'refuse' or state['round'] >= max_rounds:
            return {'route': 'stop'}
        # Reviewer can request rewriting or a new retrieval plan, not run tools.
        if review.action == 'rewrite':
            return {'round': state['round'] + 1, 'route': 'write', 'feedback': review.reason}
        return {'route': 'retrieve', 'feedback': review.reason}

    graph = StateGraph(State)
    graph.add_node('retrieve', retrieve_node)
    graph.add_node('write', write_node)
    graph.add_node('review', review_node)
    graph.add_edge(START, 'retrieve')
    for name in ('retrieve', 'write', 'review'):
        graph.add_conditional_edges(name, lambda state: state['route'],
            {'retrieve': 'retrieve', 'write': 'write', 'review': 'review', 'stop': END})
    try:
        graph.compile().invoke({'round': 0, 'feedback': ''}, {'recursion_limit': 16})
        if result['status'] != 'answered':
            result['status'] = last_failure
    except EvidenceChanged:
        result['status'] = 'evidence_changed'
        evidence = []
    except WorkflowStop as exc:
        result['status'] = 'workflow_stopped'
        trace.append({'role': 'supervisor', 'event': 'stopped', 'code': exc.code})
    except (IngestionError, ValidationError) as exc:
        result['status'] = 'generation_unavailable'
        trace.append({'role': 'supervisor', 'event': 'failed',
                      'code': exc.code if isinstance(exc, IngestionError) else 'AGENT_INVALID_RESPONSE'})
    # Trace contains only role names, codes, counters, timings; never drafts/prompts.
    result.update(evidence=evidence, details={'retrieval': retrieval, 'warnings': warnings,
        'agent_workflow': {'version': 'langgraph-reviewed-v1', 'model_calls': model_calls,
            'max_rounds': max_rounds, 'max_model_calls': max_model_calls,
            'elapsed_ms': round((clock() - started) * 1000, 2),
            'review_check': 'model_judgment_not_ground_truth', 'trace': trace}})
    return result
