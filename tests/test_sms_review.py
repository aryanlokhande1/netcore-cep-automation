import hashlib

import pytest

from app.trace import Trace
from modules.sms.executor import CAPABILITIES
from modules.sms.review import Lesson, ordered_evidence, save_candidate, validate_lessons
from modules.sms.skill import load_skill
from modules.whatsapp.contract import V2Stop


def test_teacher_meter_accepts_only_the_scoped_sms_observation_catalog(monkeypatch):
    from deep_agents.telemetry import MeteredChatOpenAI

    monkeypatch.setenv('OPENAI_API_KEY', 'fixture-only')
    model = MeteredChatOpenAI(model='fixture')
    model.allow_teacher_observations(['read_sms_review_evidence', 'view_sms_stage'])
    with pytest.raises(ValueError, match='Unexpected Teacher'):
        model.allow_teacher_observations(['read_sms_review_evidence', 'view_sms_stage', 'set_sms_field'])
    model._role = 'doer'
    with pytest.raises(ValueError, match='Unexpected Teacher'):
        model.allow_teacher_observations(['read_sms_review_evidence', 'view_sms_stage'])


def test_teacher_review_tool_keeps_predicates_and_references_without_repeating_full_evidence():
    from modules.sms.review import evidence_index

    trace = Trace()
    trace.enable_agent_trace()
    ident = trace.agent_event('sms_mcp_result', role='doer', tool='set_sms_field', code='BLOCKED')
    checks = {'initial_binding': True, 'popup_owned': False}
    source = ordered_evidence(trace, {'tag': {'binding_checks': checks, 'controls': 'long scoped observation'}})
    result = evidence_index(source)
    assert result['binding_checks'] == checks
    assert result['mcp_results'] == [{'id': ident, 'role': 'doer', 'tool': 'set_sms_field', 'code': 'BLOCKED'}]
    assert 'long scoped observation' not in repr(result)


def test_ordered_setup_evidence_explicitly_supersedes_initial_route_failure():
    trace = Trace()
    trace.enable_agent_trace()
    earlier = trace.agent_event('mcp_observer_failure', role='observer', operation='verify_sms_setup',
                               category='SMS_SETUP_PROTECTED_ROUTE_UNVERIFIED')
    observation = {'approved_route_verified': True, 'setup_view': True, 'campaign_name_count': 1}
    later = trace.agent_event('sms_setup_diagnostic_read', role='observer', **observation)
    evidence = ordered_evidence(trace, observation)
    assert [item['id'] for item in evidence['chronological_events']] == [earlier, later]
    assert evidence['current_setup_state'] == 'verified'
    assert evidence['recovered_checks'] == [{'earlier_failure': earlier, 'superseded_by_success': later}]


def test_teacher_trace_retains_scoped_nearby_tag_controls():
    trace = Trace()
    trace.enable_agent_trace()
    nearby = [{'tag': 'mat-form-field', 'controls': [{'tag': 'input', 'role': 'combobox'}]}]
    ref = trace.agent_event('sms_tag_investigation_readback', role='observer', nearby_controls=nearby)
    events = ordered_evidence(trace, {})['chronological_events']
    assert events[0]['id'] == ref
    assert events[0]['nearby_controls'] == nearby


def test_versioned_candidate_only_accepts_executed_action_and_later_matching_readback(tmp_path):
    trace = Trace()
    trace.enable_agent_trace()
    scope = {'stage': 'content', 'action': 'use_sms_template', 'field': 'template'}
    action = trace.agent_event('sms_task_action', role='doer', status='EXECUTED', **scope)
    readback = trace.agent_event('sms_task_readback', role='observer', status='VERIFIED', action_ref=action, **scope)
    events = ordered_evidence(trace, {})['chronological_events']
    valid = Lesson(action_ref=action, readback_ref=readback)
    steps = validate_lessons([valid], events, CAPABILITIES)
    with pytest.raises(ValueError, match='EXECUTION_EVIDENCE_INVALID'):
        validate_lessons([valid], events[::-1], CAPABILITIES)
    with pytest.raises(ValueError, match='EXECUTION_EVIDENCE_INVALID'):
        validate_lessons([valid], events, set())
    candidate = save_candidate(trace, 'd' * 64, steps, root=tmp_path)
    assert candidate['approved'] is False
    folder = tmp_path / candidate['version']
    assert hashlib.sha256((folder / 'SKILL.md').read_bytes()).hexdigest() == candidate['content_sha256']
    assert 'use_sms_template' in (folder / 'strategy.diff').read_text(encoding='utf-8')
    snapshot = load_skill(version=candidate['version'], expected_digest=candidate['content_sha256'],
                          task_digest='d' * 64, candidates=tmp_path)
    assert snapshot.metadata['source'] == 'TEACHER_CANDIDATE'
    assert save_candidate(trace, 'd' * 64, steps, root=tmp_path, skill=snapshot) is None
    with pytest.raises(V2Stop, match='SMS_SKILL_HASH_OR_PROVENANCE_UNVERIFIED'):
        load_skill(version=candidate['version'], expected_digest='e' * 64, task_digest='d' * 64, candidates=tmp_path)
