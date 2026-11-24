import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from modules.whatsapp.contract import V2Stop


def test_launch_grant_and_single_save_claim(tmp_path):
    from modules.whatsapp.unattended import DraftLaunch, claim_save

    path = tmp_path / 'approval.json'
    data = {'origin': 'https://tprappin2r.netcoresmartech.com', 'tenant': 'bajajfinservmarket',
            'action': 'SAVE_AND_PREVIEW', 'payload_sha256': 'a' * 64}
    path.write_text(json.dumps(data), encoding='utf-8')
    approved = DraftLaunch.load(path)
    assert approved.payload_sha256 == 'a' * 64
    data['tenant'] += 'other'
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(V2Stop, match='UNATTENDED_TENANT_SCOPE_MISMATCH'):
        DraftLaunch.load(path)
    payload = SimpleNamespace(campaign_name='one', schedule_datetime='2030-01-01T18:00:00+05:30')
    claim_save(tmp_path, approved, payload, 'run1')
    with pytest.raises(V2Stop, match='PRIOR_DRAFT_SAVE_ATTEMPT_REQUIRES_READ_ONLY_RECONCILIATION'):
        claim_save(tmp_path, approved, payload, 'run2')


def test_unattended_preflight_refuses_missing_reviewed_login_evidence(monkeypatch, tmp_path):
    from app.cli import build_parser
    from app.config import Config
    from modules.whatsapp.workflow import run_human_assisted
    import asyncio

    grant = tmp_path / 'approval.json'
    grant.write_text(json.dumps({'origin': 'https://tprappin2r.netcoresmartech.com', 'tenant': 'bajajfinservmarket',
        'action': 'READ_DRAFT', 'payload_sha256': 'a' * 64, 'campaign_id': '18023'}), encoding='utf-8')
    monkeypatch.setattr(Config, 'load', lambda: Config(executable='unused'))
    args = build_parser().parse_args(['whatsapp-human-assisted', '--unattended-config', str(grant),
        '--unattended-payload', str(tmp_path / 'payload.json'), '--login-evidence-file', str(tmp_path / 'missing.json'),
        '--authorized-live', '--authorized-inference'])
    with pytest.raises(V2Stop, match='MISSING_EVIDENCE_CONFIGURATION'):
        asyncio.run(run_human_assisted(args))


@pytest.mark.asyncio
@pytest.mark.parametrize('require_configuration, senders, templates, expected_status', [
    (False, [], [], 'PERSISTED_SCHEDULE_MATCH'),
    (True, ['fixture sender'], ['fixture template'], 'PERSISTED_DRAFT_MATCH'),
    (True, ['other sender'], ['fixture template'], 'PERSISTED_SENDER_OR_TEMPLATE_MISMATCH'),
    (True, ['fixture sender', 'other sender'], ['fixture template'], 'PERSISTED_SENDER_OR_TEMPLATE_UNRESOLVED'),
])
async def test_exact_existing_id_reads_draft_without_search(monkeypatch, require_configuration, senders, templates,
                                                            expected_status):
    from modules.whatsapp import reconciliation

    root = 'https://tprappin2r.netcoresmartech.com/bajajfinservmarket/admin/index.php/campaign/'
    listing, preview = root + 'unified', root + 'whatsapp_campaign/preview?id=18023'
    current = listing
    row = {'id': '18023', 'status': 'DRAFT', 'created': None, 'updated': None, 'channel': 'WhatsApp',
           'whatsapp_channel': True, 'links': [preview], 'controls': []}
    state = {'table_count': 1, 'visible_rows': 1, 'empty_state_visible': False, 'no_results': False,
             'search_count': 0, 'search_trigger_count': 0, 'campaigns_link_count': 0, 'busy': False,
             'shape': {'ready_state': 'complete'}, 'matches': [row], 'further_pages': False, 'time_column_zone': None}

    async def url():
        return current

    async def navigate(next_url):
        nonlocal current
        assert next_url == preview
        current = next_url

    async def frame_tree(*args, **kwargs):
        return {'frameTree': {'frame': {'url': current}}}

    page = SimpleNamespace(url=url, evaluate=AsyncMock(side_effect=[state, state, {
        'campaign_name_visible': True, 'ids': ['18023'], 'statuses': ['Draft'],
        'dates': ['Nov 25, 2026 06:00 PM'], 'zones': ['Asia/Calcutta'], 'send_later_label_visible': True,
        'senders': senders, 'templates': templates}]))
    guard = SimpleNamespace(send=frame_tree, session_states={'owned': SimpleNamespace(document_generation=1)})
    browser = SimpleNamespace(page=page, guard=guard, policy=SimpleNamespace(permits=lambda url: url.startswith(root)),
                              navigate=navigate, budget=lambda: None,
                              trace=SimpleNamespace(agent_event=lambda *args, **fields: None))
    binding = SimpleNamespace(session='owned', require_current=AsyncMock())
    payload = SimpleNamespace(campaign_name='exact-draft', schedule_datetime='2026-11-25T18:00:00+05:30',
                              timezone='Asia/Kolkata', business_number='fixture sender', template='fixture template')
    monkeypatch.setattr(reconciliation, 'reject_security_negatives', AsyncMock())

    result = await reconciliation.reconcile_campaign(browser, binding, payload, 'read-run', None, campaign_id='18023',
                                                      require_configuration=require_configuration)
    assert result['status'] == expected_status
    assert (result['campaign_id'], result['netcore_status'], result['date'], result['time'], result['timezone']) == (
        '18023', 'Draft', '2026-11-25', '18:00', 'Asia/Calcutta')
    if expected_status == 'PERSISTED_DRAFT_MATCH':
        assert (result['sender'], result['template'], result['configuration_source']) == (
            'fixture sender', 'fixture template', 'SAVED_RECORD')


def test_doer_instruction_capture_requires_dispatched_text_and_excludes_observations(tmp_path):
    from app.trace import Trace
    from modules.whatsapp.diagnostic_artifacts import capture_doer_instructions

    trace = Trace()
    trace.enable_agent_trace(private_values=['fixture-secret'])
    capture = capture_doer_instructions(tmp_path, trace, 'a' * 64, 'system\n', 'skill fixture-secret\n', 'task')
    capture('', [{'content': [{'type': 'input_text', 'text': 'system\nskill fixture-secret\n'}]}],
            [{'content': 'task Current evidence: observation-secret'}], 'b' * 64)
    capture('', [], [{'content': 'different task'}], 'c' * 64)
    lines = (tmp_path / 'whatsapp-doer-instructions.jsonl').read_text(encoding='utf-8')
    matched, missed = map(json.loads, lines.splitlines())
    assert matched['system_instructions'] == 'system\n'
    assert matched['skill_instructions'] == 'skill [private]\n'
    assert matched['task_instructions'] == 'task'
    assert 'observation-secret' not in lines and 'fixture-secret' not in lines
    assert missed['system_excerpt_matched'] is False and missed['task_excerpt_matched'] is False
    assert 'system_instructions' not in missed and 'task_instructions' not in missed


@pytest.mark.asyncio
@pytest.mark.parametrize('status, missing, passes', [
    ('DRAFT_RECORD_VERIFIED', None, True),
    ('DRAFT_RECORD_VERIFIED', 'sender', False),
    ('DRAFT_RECORD_VERIFIED', 'template', False),
    ('DRAFT_PREVIEW_VERIFIED', None, False),
])
async def test_unattended_write_cli_requires_saved_record(status, missing, passes, monkeypatch):
    from app.cli import run
    from modules.whatsapp import workflow

    record = dict(status='PERSISTED_DRAFT_MATCH', configuration_source='SAVED_RECORD', campaign_id='123',
                  campaign_name='fixture', netcore_status='Draft', date='2026-11-25', time='18:00',
                  timezone='Asia/Kolkata', sender='fixture sender', template='fixture template')
    if missing:
        record.pop(missing)
    monkeypatch.setattr(workflow, 'run_human_assisted', AsyncMock(return_value={
        'unattended_action': 'SAVE_AND_PREVIEW', 'status': status, 'authentication_verified': True,
        'cleanup': 'CLOSED_AND_PROFILE_REMOVED', 'schedule_draft_preview': record}))
    args = SimpleNamespace(command='whatsapp-human-assisted', unattended_config='fixture')
    if passes:
        await run(args)
    else:
        with pytest.raises(SystemExit) as error:
            await run(args)
        assert error.value.code == 1
