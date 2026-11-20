"""Offline archived-event regression; never opens browsers or invokes providers."""
import json
from pathlib import Path

import pytest

from deep_agents.evidence import compile_evidence, digest, source_events, tokens
from deep_agents.permissions import model_failure_details
from deep_agents.review_contract import EvidenceError
from modules.login.review import LoginReviewAdapter

RUN = '3dc548f8eb984c2eb4431fcc004d4199'


def saved_input():
    directory = Path('outputs/whatsapp-v2') / RUN
    events = json.loads((directory / (RUN + '.json')).read_text())
    summary = json.loads((directory / (RUN + '-summary.json')).read_text())
    # Retained fields only: the original in-memory Login packet was not persisted.
    result = dict(run_id=RUN, verification='UNEXPECTED_PAGE',
                  reason_code='POST_LOGIN_READINESS_SCOPE_INCOMPLETE',
                  step_facts=summary['step_facts'])
    return result, events


def test_actual_saved_events_fit_without_losing_findings_or_tool_history():
    result, events = saved_input()
    original = compile_evidence(result, events, LoginReviewAdapter(), max_tokens=20000)
    compact = compile_evidence(result, events, LoginReviewAdapter(), max_tokens=5000)
    assert tokens(compact.model_dump()) <= 5000
    substantive = lambda p: sorted((c.kind, digest(c.data), tuple(c.events))
                                  for c in p.evidence
                                  if c.kind not in {'source_digest', 'source_archive'})
    assert substantive(compact) == substantive(original)
    assert {i for c in compact.evidence for i in c.events} == {
        i for c in original.evidence for i in c.events}
    archive, = [c for c in compact.evidence if c.kind == 'source_archive']
    assert archive.data['sha256'] == digest(source_events(events))
    assert archive.data['source_event_count'] == len(source_events(events))
    assert compact.model_dump() == compile_evidence(
        result, source_events(events), LoginReviewAdapter(), max_tokens=5000).model_dump()
    scope = [c.data for c in compact.evidence if c.data.get('state') == 'LOGIN_SCOPE_UNAVAILABLE']
    assert scope and any(d['inaccessible_shadow_roots'] == 1 for d in scope)
    assert any(c.data.get('code') == 'MISSING_EVIDENCE_CONFIGURATION' for c in compact.evidence)
    assert len([c for c in compact.evidence if c.kind == 'tool_result']) == 5


def test_tiny_budget_still_fails_closed_with_exact_safe_counts():
    result, events = saved_input()
    with pytest.raises(EvidenceError) as caught:
        compile_evidence(result, events, LoginReviewAdapter(), max_tokens=10)
    diagnostic = model_failure_details(caught.value)
    assert diagnostic['code'] == 'EVIDENCE_CONTEXT_BUDGET_EXCEEDED'
    assert diagnostic['evidence_tokens'] > diagnostic['evidence_token_limit'] == 10


def test_wrapped_compiler_error_retains_code_without_exporting_exception_text():
    root = EvidenceError('INVALID_FACT_REFERENCE')
    wrapper = RuntimeError('private credential value must never be exported')
    wrapper.__cause__ = root
    diagnostic = model_failure_details(wrapper)
    assert diagnostic['code'] == 'INVALID_FACT_REFERENCE'
    assert 'private credential' not in json.dumps(diagnostic)
    assert model_failure_details(EvidenceError('private arbitrary text'))['code'] == 'MODEL_OR_CONTROLLER_EXCEPTION'
