from datetime import datetime
from hashlib import sha256
import json
import os

import pytest

from modules.sms.preflight import SETUP_OBSERVATION, setup_status, verify_future_schedule
from modules.whatsapp.contract import V2Stop


def test_existing_loader_checks_sms_payload_digest_and_leaves_unrequested_fields_absent(tmp_path):
    from modules.sms.contract import CREATE_URL, load_sms_payload

    path = tmp_path / 'payload.json'
    raw = json.dumps({
        'tenant': 'bajajfinservmarket', 'create_url': CREATE_URL, 'campaign_name': '24nov_testing', 'tag': 'test',
        'audience_type': 'Segments/Lists', 'include_audience': 'agent_test', 'channel': 'bajajfinservmarket_bajajfinservdir',
        'sender_id': 'BFDLPS', 'custom_domain': 'http://m.bmkts.in/', 'template': 'LP_SEC_HLF_SMS_RTCS_SEPT_24NOV',
        'template_id': 16683, 'send_type': 'Send later', 'schedule_datetime': '2026-11-25T16:00:00+05:30',
        'timezone': 'Asia/Kolkata', 'final_action': 'Preview',
    }).encode()
    path.write_bytes(raw)
    digest = sha256(raw).hexdigest()
    payload, verified = load_sms_payload(path, approved_digest=digest)
    assert verified == digest
    assert payload.template_id == 16683 and payload.final_action == 'Preview'
    for field in ('ga_tracking', 'exclude_audience', 'frequency_cap_enabled'):
        assert field not in payload.model_dump()
    changed = json.dumps({**payload.model_dump(), 'ga_tracking': False}).encode()
    path.write_bytes(changed)
    with pytest.raises(V2Stop, match='PAYLOAD_APPROVAL_DIGEST_MISMATCH'):
        load_sms_payload(path, approved_digest=digest)
    with pytest.raises(V2Stop, match='INVALID_V2_PAYLOAD'):
        load_sms_payload(path, approved_digest=sha256(changed).hexdigest())


def test_schedule_must_be_in_future_at_approved_instant():
    assert verify_future_schedule(datetime.fromisoformat('2026-11-25T10:29:59+00:00')).isoformat() == (
        '2026-11-25T16:00:00+05:30')
    with pytest.raises(V2Stop, match='SMS_SCHEDULE_NOT_IN_FUTURE'):
        verify_future_schedule(datetime.fromisoformat('2026-11-25T10:30:00+00:00'))


@pytest.mark.parametrize(('name_count', 'status'), [
    (0, 'SMS_SETUP_CAMPAIGN_NAME_NOT_UNIQUE'),
    (2, 'SMS_SETUP_CAMPAIGN_NAME_NOT_UNIQUE'),
    (1, 'SMS_SETUP_VERIFIED'),
])
def test_setup_does_not_require_schedule_timezone(name_count, status):
    assert setup_status({'campaign_name_count': name_count, 'setup_view': True}) == status


def test_campaign_name_without_setup_view_does_not_pass():
    assert setup_status({'campaign_name_count': 1, 'setup_view': False}) == (
        'SMS_SETUP_VIEW_UNVERIFIED')


@pytest.mark.asyncio
async def test_sms_launcher_prints_one_blocker_and_keeps_details_in_artifact(tmp_path, monkeypatch, capsys):
    from unittest.mock import AsyncMock
    from app.cli import build_parser, run
    from modules.whatsapp import workflow

    evidence = {'status': 'SMS_EXACT_NAME_CHECK_BLOCKED',
                'message': "Campaigns search could not verify exact name '24nov_testing'.",
                'name_check': {'status': 'CAMPAIGN_LIST_READINESS_FAILED', 'source': 'AUTOMATED_CAMPAIGNS_UI'}}
    monkeypatch.setattr(workflow, 'run_human_assisted', AsyncMock(return_value={
        'run_id': 'local', 'status': evidence['status'], 'artifact_directory': str(tmp_path),
        'dashboard_handoff_evidence': evidence, 'cleanup': 'CLOSED_AND_PROFILE_REMOVED'}))
    args = build_parser().parse_args(['sms-preflight', '--authorized-live', '--authorized-inference'])
    with pytest.raises(SystemExit) as stop:
        await run(args)
    assert stop.value.code == 1
    assert capsys.readouterr().out.splitlines() == [evidence['message']]
    assert json.loads((tmp_path / 'sms-result.json').read_text(encoding='utf-8'))['dashboard_handoff_evidence'] == evidence


@pytest.mark.asyncio
async def test_automated_exact_match_cannot_be_overridden_by_operator_flag(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.trace import Trace
    from modules.sms import preflight

    browser = SimpleNamespace(trace=Trace(), navigate=AsyncMock())
    automated = {'status': 'CAMPAIGN_NAME_ALREADY_USED'}
    run = SimpleNamespace(payload=SimpleNamespace(campaign_name='24nov_testing'),
                          operator_confirmed_unused_name='24nov_testing')
    execute = AsyncMock()
    monkeypatch.setattr(preflight, 'reconcile_campaign', AsyncMock(return_value=automated))
    result = await preflight.preflight(browser, object(), tmp_path, run, execute=execute)
    assert result['status'] == 'CAMPAIGN_NAME_ALREADY_USED'
    assert result['name_check'] == {**automated, 'source': 'AUTOMATED_CAMPAIGNS_UI'}
    assert 'operator_name_check' not in result
    execute.assert_not_awaited()
    browser.navigate.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(('automated_status', 'attest', 'payload_reply'), [
    ('CAMPAIGN_NAME_UNUSED', False, 'y'),
    ('CAMPAIGN_LIST_READINESS_FAILED', True, 'y'),
    ('CAMPAIGN_LIST_READINESS_FAILED', True, 'n'),
    ('CAMPAIGN_LIST_READINESS_FAILED', False, 'y'),
    ('CAMPAIGN_LIST_READINESS_FAILED', False, 'n'),
    ('CAMPAIGN_SEARCH_SCOPE_UNVERIFIED', False, None),
])
async def test_headed_sms_confirmation_has_only_two_yes_no_prompts(tmp_path, monkeypatch, automated_status, attest, payload_reply):
    from io import StringIO
    from typing import get_args
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from app.trace import Trace
    from browser.operator_console import OperatorConsole
    from modules.sms import preflight
    from modules.sms.contract import SMSPreviewPayload

    payload = SMSPreviewPayload.model_validate({name: get_args(field.annotation)[0]
                                                for name, field in SMSPreviewPayload.model_fields.items()})
    trace = Trace()
    trace.enable_agent_trace()
    browser = SimpleNamespace(trace=trace, navigate=AsyncMock())
    binding = SimpleNamespace(require_current=AsyncMock())
    output = StringIO()
    keys = iter('y\r' + (payload_reply + '\r' if payload_reply else ''))
    console = OperatorConsole(read_key=lambda: next(keys), output=output)
    run = preflight.Preflight(30, payload=payload, preview=True, digest='d' * 64,
                              operator_confirmed_unused_name=payload.campaign_name if attest else None)
    monkeypatch.setattr(run, 'console', lambda _: console)
    monkeypatch.setattr(run, 'operator_seconds', lambda _: 30)
    monkeypatch.setattr(run, 'save_campaigns_evidence', AsyncMock(return_value='diagnostic.json'))
    monkeypatch.setattr(preflight, 'verify_future_schedule', lambda: datetime.fromisoformat(payload.schedule_datetime))
    automated = AsyncMock(return_value={'status': automated_status})
    monkeypatch.setattr(preflight, 'reconcile_campaign', automated)
    monkeypatch.setattr(preflight, 'require_setup_document', AsyncMock(return_value=('target', payload.create_url)))
    monkeypatch.setattr(preflight, 'reject_security_negatives', AsyncMock())
    monkeypatch.setattr(preflight, 'observe_setup', AsyncMock(return_value=('SMS_SETUP_VERIFIED', {'setup_view': True})))

    async def finish(observer, evidence):
        await run.approve_payload(observer, evidence)
        return evidence

    assert await console.confirm_sms_tenant(payload.tenant, 30) == 'yes'
    if payload_reply == 'n':
        with pytest.raises(V2Stop, match='SMS_PAYLOAD_DECLINED'):
            await preflight.preflight(browser, binding, tmp_path, run, execute=finish)
        assert not any(row.get('agent_execution', {}).get('kind') == 'sms_exact_name_operator_result' for row in trace.events)
    else:
        result = await preflight.preflight(browser, binding, tmp_path, run, execute=finish)
        assert result['name_check'] == {'status': automated_status, 'source': 'AUTOMATED_CAMPAIGNS_UI'}
        if payload_reply:
            approval = result['payload_approval']
            assert approval['accepted'] and approval['payload_sha256'] == 'd' * 64
            assert approval['scope']['next_step_may_persist'] and approval['scope']['preview_max_clicks'] == 1
            assert approval['scope']['excluded'] == ['finish_later', 'save_and_publish', 'activate', 'send']
            assert result.get('operator_name_check', {}).get('source') == ('OPERATOR_ATTESTATION' if attest else None)
            uncertainty = automated_status == 'CAMPAIGN_LIST_READINESS_FAILED' and not attest
            assert approval['duplicate_uncertainty_accepted'] is uncertainty
            if uncertainty:
                assert result['name_status'] == 'CAMPAIGN_LIST_READINESS_FAILED'
                assert result['name_check_resolution'] == 'OPERATOR_APPROVED_UNCERTAINTY'
        else:
            assert result['status'] == 'SMS_EXACT_NAME_CHECK_BLOCKED'
            browser.navigate.assert_not_awaited()
    prompts = [event['prompt_id'] for event in console.events if event['event'] == 'prompt_open']
    assert prompts == ['sms_tenant_confirmation'] + (['sms_payload_confirmation'] if payload_reply else [])
    assert 'Confirm Netcore tenant bajajfinservmarket? [y/N]' in output.getvalue()
    assert ('Apply this SMS payload through Preview? [y/N]' in output.getvalue()) == bool(payload_reply)
    assert ('Duplicate check unavailable' in output.getvalue()) == (automated_status == 'CAMPAIGN_LIST_READINESS_FAILED' and not attest)
    assert automated.await_args.kwargs['check_unused'] and not automated.await_args.kwargs['announce']


@pytest.mark.asyncio
@pytest.mark.parametrize(('status', 'strict', 'expected'), [
    ('CAMPAIGN_LIST_READINESS_FAILED', True, 'diagnostic'),
    ('CAMPAIGN_NAME_ALREADY_USED', True, 'diagnostic'),
    ('CAMPAIGN_NAME_UNUSED', True, 'write'),
    ('CAMPAIGN_NAME_UNUSED', False, 'diagnostic'),
])
async def test_tag_investigation_only_offers_writes_after_verified_unused_name(monkeypatch, tmp_path,
                                                                              status, strict, expected):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from modules.sms import preflight, workflow

    diagnostic = AsyncMock(return_value='diagnostic')
    writable = AsyncMock(return_value='write')
    monkeypatch.setattr(workflow, 'investigate_tag_readonly', diagnostic)
    monkeypatch.setattr(workflow, 'finish_preview_workflow', writable)

    async def route(browser, binding, directory, run, *, review, execute):
        return await execute(None, {'name_status': status})

    monkeypatch.setattr(preflight, 'preflight', route)
    run = preflight.Preflight(30, preview=True, tag_investigation=True, reconcile_before_write=strict)
    result = await run(SimpleNamespace(), None, tmp_path, settings=None, budget=None)
    assert result == expected
    assert diagnostic.await_count == int(expected == 'diagnostic')
    assert writable.await_count == int(expected == 'write')


@pytest.mark.asyncio
async def test_failed_mcp_read_takes_one_diagnostic_read_and_holds_healthy_page(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.trace import Trace
    from modules.sms import preflight

    browser = SimpleNamespace(trace=Trace(), navigate=AsyncMock())
    binding = SimpleNamespace(require_current=AsyncMock())
    run = SimpleNamespace(save_campaigns_evidence=AsyncMock(return_value='diagnostic.json'),
                          hold=AsyncMock(return_value='RELEASED'))
    stop = V2Stop('MCP_BROWSER_DISPATCH_FAILED', owner='tool', unsafe=True)
    stop.diagnostic = {'category': 'SMS_SETUP_DOCUMENT_CHANGED', 'operation': 'verify_sms_setup', 'phase': 'invoke'}
    read = AsyncMock(side_effect=[stop, ('SMS_SETUP_VIEW_UNVERIFIED', {'campaign_name_count': 1, 'setup_view': False})])
    monkeypatch.setattr(preflight, 'reconcile_campaign', AsyncMock(return_value={'status': 'CAMPAIGN_NAME_UNUSED'}))
    monkeypatch.setattr(preflight, 'require_setup_document', AsyncMock(return_value=('target', preflight.CREATE_URL)))
    monkeypatch.setattr(preflight, 'reject_security_negatives', AsyncMock())
    monkeypatch.setattr(preflight, 'observe_setup', read)
    result = await preflight.preflight(browser, binding, tmp_path, run)
    assert [call.kwargs['attempts'] for call in read.await_args_list] == [8, 1]
    assert result['setup_read_failure'] == stop.diagnostic
    assert result['status'] == 'SMS_SETUP_VIEW_UNVERIFIED'
    assert result['last_verified_step'] == 'SMS_APPROVED_ROUTE_VERIFIED'
    assert result['review_status'] == 'RELEASED'
    run.hold.assert_awaited_once()
    browser.navigate.assert_awaited_once_with(preflight.CREATE_URL)


@pytest.mark.asyncio
@pytest.mark.skipif(os.getenv('RUN_SMS_DOM_FIXTURE') != '1', reason='Explicit loopback Chromium test')
async def test_chromium_sms_setup_filters_hidden_controls_without_reading_timezone(monkeypatch):
    from app.browser_mcp_client import StepTools, open_step
    from app.config import Config, OriginPolicy
    from app.trace import Trace
    from browser.protected_tabs import bind_confirmed_primary_tab
    from browser.stagehand_client import StagehandClient
    from modules.sms import preflight
    from modules.whatsapp.mcp_steps import login_document
    from test_whatsapp_navigation_repair import navigation_server

    with navigation_server('valid') as server:
        browser = StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=False,
            login_url='about:blank', expected_host='127.0.0.1', max_actions=30, timeout_seconds=45),
            OriginPolicy({server.origin}), Trace(), fixture=True)
        try:
            await browser.start()
            await browser.page.goto(server.dashboard)
            monkeypatch.setattr(preflight, 'CREATE_URL', server.dashboard)
            html = ('<main><h1>SMS Setup</h1><input type="text" aria-label="Campaign name" hidden>'
                    '<label for="campaign">Campaign name *</label><input id="campaign" value="private">'
                    '<label for="zone">Time zone</label><select id="zone">'
                    '<option>Asia/Kolkata</option><option selected>Asia/Colombo</option></select></main>')
            await browser.page.evaluate('document.body.innerHTML = ' + json.dumps(html))
            binding = bind_confirmed_primary_tab(browser)
            observer = preflight.SetupObserver(browser, binding, await preflight.require_setup_document(browser, binding))
            step = StepTools('observer', 'sms_setup', frozenset({'verify_sms_setup', 'type'}))
            async with open_step(observer, step, document_identity=login_document, independent_observer=True) as client:
                assert set(client.tools) == {'verify_sms_setup'}
                facts = await client.read('verify_sms_setup')
                assert facts.pop('approved_route_verified') is True
                assert facts.pop('setup_fragment') is False
                with pytest.raises(V2Stop, match='MCP_BROWSER_DISPATCH_FAILED'):
                    await client.read('verify_sms_setup', {'value': 'unused'})
            with pytest.raises(V2Stop, match='MCP_STEP_SCOPE_REVOKED'):
                await client.read('verify_sms_setup')
            assert facts == {'campaign_name_count': 1, 'setup_view': True, 'setup_marker_visible': True,
                             'setup_form_signature': False, 'later_step_active': False}
            assert setup_status(facts) == 'SMS_SETUP_VERIFIED'
            await browser.page.evaluate('document.getElementById("zone").selectedIndex = 0')
            assert setup_status(await browser.page.evaluate(SETUP_OBSERVATION)) == 'SMS_SETUP_VERIFIED'
        finally:
            await browser.close()
