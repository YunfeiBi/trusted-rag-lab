"""Offline scripted workflow demo. No model, database, credentials or user data.

Run: python -m scripts.demo_agent_workflow --output path/to/report.json
Metrics measure orchestration scenarios only, never LLM answer accuracy.
"""
import argparse
import json
from pathlib import Path

from backend.app.agent_workflow import run_agent_workflow
from backend.app.ingestion import IngestionError


class DemoAgents:
    def __init__(self, scenario):
        self.scenario = scenario
        self.reviews = 0

    def plan(self, question, feedback, **kwargs):
        return {'action': 'search', 'query': question if not feedback else 'project access permissions'}

    def write(self, question, evidence, feedback, **kwargs):
        if self.scenario == 'upstream_failure':
            raise IngestionError('GENERATION_UNAVAILABLE')
        return {'answerable': True, 'claims': [{'text': 'Members control document access.', 'supports': [
            {'evidence_id': 'E1', 'quote': 'an invented quote without support' if self.scenario == 'bad_quote'
             else evidence[0]['text']}]}]}

    def review(self, question, evidence, draft, **kwargs):
        self.reviews += 1
        action = self.scenario if self.reviews == 1 and self.scenario in ('rewrite', 'retrieve') else 'accept'
        if self.scenario == 'reject':
            action = 'refuse'
        return {'action': action, 'reason': {'accept': 'accepted', 'rewrite': 'unsupported_claim',
            'retrieve': 'missing_evidence', 'refuse': 'missing_evidence'}[action]}


def run_demo():
    expected = {'accept': 'answered', 'rewrite': 'answered', 'retrieve': 'answered',
        'reject': 'review_rejected', 'bad_quote': 'invalid_citations', 'empty': 'insufficient_evidence',
        'upstream_failure': 'generation_unavailable', 'call_budget': 'workflow_stopped'}
    cases = []
    for scenario, status in expected.items():
        def search(query):
            return {'results': [] if scenario == 'empty' else [{'evidence_id': 'E1',
                'text': 'Project members control document access.'}], 'requested_mode': 'keyword',
                'effective_mode': 'keyword', 'rerank_status': 'not_requested', 'candidates': {},
                'timings_ms': {}, 'warnings': []}
        result = run_agent_workflow('Who can access the project?', DemoAgents(scenario), search,
            lambda evidence: True, max_model_calls=1 if scenario == 'call_budget' else 6)
        cases.append({'scenario': scenario, 'expected_status': status, 'actual_status': result['status'],
            'passed': result['status'] == status, 'model_calls': result['details']['agent_workflow']['model_calls'],
            'trace': result['details']['agent_workflow']['trace']})
    return {'mode': 'scripted_offline_no_llm', 'scope': 'control_flow_and_failure_handling_only',
        'scenario_count': len(cases), 'passed': sum(case['passed'] for case in cases), 'cases': cases}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    report = run_demo()
    data = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(data, encoding='utf-8')
    print(f"Offline orchestration scenarios: {report['passed']}/{report['scenario_count']}; no real LLM quality measurement.")
    for case in report['cases']:
        print(f"{case['scenario']}: {case['actual_status']}, calls={case['model_calls']}")
    raise SystemExit(0 if report['passed'] == report['scenario_count'] else 1)
