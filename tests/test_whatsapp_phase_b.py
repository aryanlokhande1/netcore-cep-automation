"""Single-run Page 1 regression; protocol/model doubles, never live inference."""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.config import ROOT, ConfigurationError
from app.trace import Trace
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from modules.whatsapp.contract import VALUES, ORDER, FIELD_FACTS, SetupStop
from modules.whatsapp.agent import create_setup_doer
from test_whatsapp_phase1 import bound_setup_tools as legacy_bound_setup_tools, protocol_metadata_transport


async def bound_setup_tools():
    browser, tools = await legacy_bound_setup_tools()
    send = browser.guard.send
    async def with_scroll(method, params=None, session=None):
        if method == 'DOM.scrollIntoViewIfNeeded': return {}
        return await send(method, params, session)
    browser.guard.send = with_scroll
    return browser, tools


class Page1Model(SyntheticChatModel):
    cursor: int = 0
    stage: int = 0
    opened: bool = False

    def _generate(self, messages, **kwargs):
        self.calls += 1
        last = next((m for m in reversed(messages) if isinstance(m, ToolMessage)), None)
        if last is None:
            name, args = 'read_module_skill', {}
        elif last.name == 'read_module_skill':
            name, args = 'observe_page', {}
        elif self.cursor == 4:
            name, args = 'SetupReport', {'status': 'SETUP_FILLED_UNSAVED', 'reason': 'fields_verified'}
        else:
            data = json.loads(last.content)
            field = ORDER[self.cursor]
            target = next(f for f in data['fields'] if f['field'] == field)
            if self.stage == 2 and target['kind'] == 'OWNED_COMBOBOX' and not target['matches_required_value']:
                if not self.opened:
                    self.opened = True
                    return self.reply('click_observed_control', {'reference': target['reference']})
                if last.name == 'click_observed_control':
                    return self.reply('inspect_control', {'reference': target['reference']})
            name = ['inspect_control', 'scroll_to_control',
                'fill_text' if field == 'campaign_name' else 'select_observed_option', 'verify_field'][self.stage]
            args = {'reference': target['reference']}
            if name == 'select_observed_option': args['option_reference'] = target['options'][0]['reference']
            self.stage += 1
            if self.stage == 4: self.stage = 0; self.cursor += 1; self.opened = False
        return self.reply(name, args)

    def reply(self, name, args):
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[
            {'name': name, 'args': args, 'id': uuid4().hex}]))])


async def test_native_skill_read_and_successful_four_field_progression():
    browser, tools = await bound_setup_tools()
    model = Page1Model()
    agent, permissions = create_setup_doer(model, tools, browser.trace, InferenceBudget(24),
        offline=True, native_skill=True)
    config = {'configurable': {'thread_id': browser.trace.run_id}, 'recursion_limit': 100}
    result = await agent.ainvoke({'messages': [{'role': 'user', 'content': 'Execute Page 1 only.'}]}, config)
    assert result['structured_response'].status == 'SETUP_FILLED_UNSAVED'
    assert (await tools.verify())['ok'] and browser.page.values == VALUES
    assert agent.get_state(config).values['skills_metadata']
    fact = next(f for f in browser.trace.step_facts if f['kind'] == 'SKILL_READ')
    read = browser.trace.events[fact['event_index']]
    assert read['skill_path'] == 'skills/whatsapp/SKILL.md'
    assert read['skill_version'] == 1 and read['content_sha256'] == tools.skill_provenance['content_sha256']
    assert read['approved'] is False and read['skill_status'] == 'UNVERIFIED_BASELINE'
    assert 'read_module_skill' in permissions.allowed
    assert set(tools.execution_summary()['field_facts'].values()) == set(FIELD_FACTS.values())
    assert any(op['field']=='campaign_name' and op['operation']=='fill' and op['verified']
        for op in tools.execution_summary()['browser_operations'])
    assert all(tools.execution_summary()['per_field_seconds'][f] >= 0 for f in ORDER)
    assert browser.trace.openai_api_calls == 0


async def test_skill_reader_denies_other_paths_and_pre_read_browser_actions():
    browser, tools = await bound_setup_tools()
    _, permissions = create_setup_doer(Page1Model(), tools, browser.trace, InferenceBudget(3),
        offline=True, native_skill=True)
    gate = permissions.skill_gate
    assert gate.read('skills/login/SKILL.md')['error'] == 'SKILL_PATH_FORBIDDEN'
    assert tools.skill_provenance is None
    handler = AsyncMock()
    request = SimpleNamespace(tool_call={'name': 'fill_text', 'id': 'blocked'})
    response = await gate.awrap_tool_call(request, handler)
    assert 'SKILL_READ_REQUIRED' in response.content
    handler.assert_not_awaited()
    assert not browser.page.actions


async def test_wrong_option_and_missing_dependency_never_select():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect()
    waba = data['fields'][1]; business = data['fields'][2]
    result = await tools.select_observed_option(business['reference'], waba['options'][0]['reference'])
    assert result['error'] == 'EXACT_EXISTING_OPTION_UNAVAILABLE'
    assert not tools.failed_approaches['business_number'], 'Invalid option references are not valid recovery approaches'
    assert not browser.page.actions
    browser, tools = await bound_setup_tools()
    data = await tools.inspect(); business = data['fields'][2]
    result = await tools.select_observed_option(business['reference'], business['options'][0]['reference'])
    assert result['error'] == 'SETUP_FIELD_ORDER_REQUIRED'
    assert not browser.page.actions


async def test_prewrite_recovery_requires_reinspection_and_rejects_same_approach():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect(); target = data['fields'][0]
    # A proven offscreen precondition failure performed no write.
    original = tools._authorize
    tools._authorize = AsyncMock(side_effect=SetupStop('SETUP_CONTROL_OBSCURED_OR_OFFSCREEN'))
    failed = await tools.fill_text(target['reference'])
    assert failed['retry_safe'] and not failed['terminal']
    assert (await tools.fill_text(target['reference']))['error'] == 'RECOVERY_REINSPECTION_REQUIRED'
    await tools.inspect_control(target['reference'])
    assert (await tools.fill_text(target['reference']))['error'] == 'FAILED_APPROACH_ALREADY_TRIED'
    assert not browser.page.actions
    # Scrolling prepares a genuinely different approach to this same field.
    tools._authorize = original
    assert (await tools.scroll_to_control(target['reference']))['ok']
    assert (await tools.fill_text(target['reference']))['ok']


async def test_two_distinct_safe_failures_stop_without_browser_interaction():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect(); target = data['fields'][0]
    tools._authorize = AsyncMock(side_effect=SetupStop('SETUP_CONTROL_OBSCURED_OR_OFFSCREEN'))
    assert not (await tools.fill_text(target['reference']))['terminal']
    await tools.inspect_control(target['reference'])
    assert (await tools.scroll_to_control(target['reference']))['ok']
    result = await tools.fill_text(target['reference'])
    assert result['error'] == 'STUCK_TWO_APPROACHES_FAILED' and result['terminal']
    assert not browser.page.actions


async def test_incorrect_postwrite_readback_is_terminal_and_never_retried():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect(); ref = data['fields'][0]['reference']
    browser.page.fail_field = 'campaign_name'
    first = await tools.fill_text(ref)
    assert first['terminal'] and not first.get('retry_safe', False)
    await tools.fill_text(ref)
    assert browser.page.actions == [('fill', 'campaign_name')]


async def test_programming_defect_is_code_fix_required():
    _, tools = await bound_setup_tools()
    result = tools.failed(TypeError('PRIVATE_IMPLEMENTATION_DETAIL'))
    assert result['classification'] == 'CODE_FIX_REQUIRED'
    assert result['failure_owner'] == 'tool'
    assert 'PRIVATE_IMPLEMENTATION_DETAIL' not in json.dumps(result)


def test_doer_only_limits_cli_and_forbidden_actions():
    from app.cli import build_parser
    from modules.whatsapp.contract import Page1Limits, validate_capability, CapabilityError, FORBIDDEN_ACTIONS
    args = build_parser().parse_args(['whatsapp-setup', '--doer-only', '--teacher-request-budget', '0'])
    assert args.doer_only
    assert Page1Limits().validate().teacher_requests == 0
    with pytest.raises(ConfigurationError): Page1Limits(teacher_requests=1).validate()
    for action in FORBIDDEN_ACTIONS:
        with pytest.raises(CapabilityError): validate_capability(action)


async def test_failure_capture_is_sanitized_and_precedes_cleanup(monkeypatch, tmp_path):
    from modules.whatsapp.failure_inspection import capture_setup_failure
    import modules.whatsapp.observation as observation
    import modules.whatsapp.learning_loop as loop
    browser, tools = await bound_setup_tools()
    await tools.inspect()
    raw = dict(tools.current)
    raw['private_text'] = 'NEVER_PERSIST_THIS'
    raw['fields']['tags']['value'] = 'NEVER_PERSIST_THIS'
    raw['fields']['tags']['selector'] = '#NEVER_PERSIST_THIS'
    monkeypatch.setattr(observation, 'observe_setup', AsyncMock(return_value=raw))
    async def screenshot(*args, **kwargs):
        assert not browser.close_started
        return {'status': 'FULL_FRAME_REDACTED', 'file': 'redacted.png'}
    monkeypatch.setattr(loop, 'redacted_capture', screenshot)
    tools.terminal_error = True
    evidence = await capture_setup_failure(browser, tools, tmp_path)
    assert evidence['dom_status'] == 'FRESH_SANITIZED_STRUCTURE'
    assert evidence['capture_phase'] == 'BEFORE_BROWSER_CLEANUP'
    assert evidence['screenshot']['visual_content_available'] is False
    assert 'NEVER_PERSIST_THIS' not in json.dumps(evidence)
    assert 'NEVER_PERSIST_THIS' not in (tmp_path/'failure-evidence.json').read_text()


async def test_protection_loss_preserves_explicit_capture_gap(tmp_path):
    from modules.whatsapp.failure_inspection import capture_setup_failure
    browser, tools = await bound_setup_tools()
    browser.guard.failed = True
    evidence = await capture_setup_failure(browser, tools, tmp_path)
    assert evidence['dom_status'] == 'UNAVAILABLE'
    assert evidence['capture_gap']
    assert not list(tmp_path.iterdir())


async def test_unconfirmed_handoff_starts_no_skill_read_or_whatsapp_interaction():
    from test_whatsapp_phase1 import session
    from app.state import Observation
    from modules.whatsapp.workflow import continue_setup
    from modules.whatsapp.contract import Page1Limits
    from modules.whatsapp.session import Phase1Budget
    browser = session()
    browser.observe = AsyncMock(return_value=Observation(expected_host=True))
    result = {'verification': 'UNEXPECTED_PAGE', 'reason_code': 'MISSING_EVIDENCE', 'error': None}
    checkpoint = AsyncMock(side_effect=SetupStop('POC_CHECKPOINT_DECLINED'))
    model = Page1Model()
    await continue_setup(browser, SimpleNamespace(terminal_error=False), result, model,
        Phase1Budget(Page1Limits()), Page1Limits(), checkpoint=checkpoint,
        authorized_mutations=True, native_skill=True)
    assert result['setup_status'] == 'BLOCKED' and result['poc_checkpoint'] == 'NOT_ACCEPTED'
    assert result['setup_execution']['remaining_fields'] == list(ORDER)
    assert not browser.setup_tab_attempted and model.calls == 0
    assert not any(f['kind'] == 'SKILL_READ' for f in browser.trace.step_facts)


async def test_controller_programming_failure_is_preserved_as_code_fix_required(monkeypatch):
    from test_whatsapp_phase1 import session
    from app.state import Observation
    from modules.whatsapp import workflow
    from modules.whatsapp.contract import Page1Limits
    from modules.whatsapp.session import Phase1Budget, PocGrant
    browser = session()
    browser.observe = AsyncMock(return_value=Observation(expected_host=True))
    result = {'verification':'UNEXPECTED_PAGE','reason_code':'MISSING_EVIDENCE','error':None}
    async def checkpoint(*args):return PocGrant(browser)
    monkeypatch.setattr(workflow,'create_setup_doer',lambda *a,**k: (_ for _ in ()).throw(TypeError('PRIVATE_ERROR')))
    await workflow.continue_setup(browser,SimpleNamespace(terminal_error=False),result,Page1Model(),
        Phase1Budget(Page1Limits()),Page1Limits(),checkpoint=checkpoint,native_skill=True)
    assert result['setup_stop_reason'] == 'CODE_FIX_REQUIRED'
    assert result['setup_failure_evidence']['failure']['reason'] == 'CODE_FIX_REQUIRED'
    assert 'PRIVATE_ERROR' not in json.dumps(result)


async def test_doer_only_cli_rejects_teacher_budget_before_private_configuration():
    from app.cli import build_parser, run
    args = build_parser().parse_args(['whatsapp-setup', '--authorized-live', '--authorized-setup',
        '--doer-only', '--total-api-budget', '16'])
    with patch('app.config.Config.load', side_effect=AssertionError('Must not read private configuration')):
        with pytest.raises(ConfigurationError): await run(args)


async def test_doer_only_runner_constructs_no_teacher_and_writes_no_candidate(monkeypatch, tmp_path):
    from modules.whatsapp import workflow
    from modules.whatsapp.contract import Page1Limits
    from app.config import Config
    from app.model_settings import ModelSettings
    model = SimpleNamespace(root_async_client=SimpleNamespace(models=SimpleNamespace(retrieve=AsyncMock())),
        bind_meter=lambda *args: None)
    roles=[]
    monkeypatch.setattr(workflow, 'openai_model', lambda role, settings: roles.append(role) or model)
    monkeypatch.setattr(workflow.sys if hasattr(workflow,'sys') else __import__('sys'), 'stdin', SimpleNamespace(isatty=lambda:True))
    monkeypatch.setattr(ModelSettings, 'load', lambda: ModelSettings())
    monkeypatch.setattr('modules.login.credentials.LocalCredentials.load', lambda: object())
    monkeypatch.setattr('modules.login.evidence.EvidenceConfig.for_session', lambda *a, **k: None)
    monkeypatch.setattr('modules.login.credentials.CredentialBroker', lambda *a: None)
    monkeypatch.setattr(workflow, 'HandoffLoginTools', lambda *a: SimpleNamespace())
    monkeypatch.setattr(workflow, 'ROOT', tmp_path)
    (tmp_path/'outputs').mkdir()
    async def execution(browser, tools, *args, **kwargs):
        return {'run_id':browser.trace.run_id,'verification':'UNEXPECTED_PAGE','reason_code':'NOT_TESTED','error':None,
            'mocked':True,'token_usage_available':False,'token_usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0}}
    monkeypatch.setattr(workflow, '_execute_doer', execution)
    async def close(browser):browser.trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED'
    monkeypatch.setattr(workflow.TaskSession, 'close', close)
    monkeypatch.setattr(workflow, '_finish_result', lambda *a: None)
    monkeypatch.setattr(workflow, 'review_evidence', AsyncMock(side_effect=AssertionError('No Teacher')))
    monkeypatch.setattr(workflow, 'write_draft', lambda *a: pytest.fail('No draft writer'))
    result = await workflow.run_phase1(Config('unused',live_login_enabled=True), True, True, Page1Limits(), doer_only=True)
    assert roles == ['doer']
    assert result['teacher_status'] == result['learning']['state'] == 'DISABLED_DOER_ONLY'
    assert result['token_usage']['total_tokens'] is None


async def test_wrong_selected_state_after_dropdown_action_is_terminal():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect()
    data = await tools.fill_text(data['fields'][0]['reference'])
    waba = data['fields'][1]
    locator = browser.page.locator
    def wrong_readback(selector):
        result = locator(selector)
        async def wrong(*args, **kwargs):
            browser.page.actions.append(('select', 'waba'))
            browser.page.values['waba'] = 'INCORRECT'
        result.select_option = wrong
        return result
    browser.page.locator = wrong_readback
    result = await tools.select_observed_option(waba['reference'], waba['options'][0]['reference'])
    assert result['terminal'] and result['error'] == 'FIELD_VALUE_NOT_VERIFIED'
    assert 'waba' in tools.execution_summary()['remaining_fields']
    await tools.select_observed_option(waba['reference'], waba['options'][0]['reference'])
    assert browser.page.actions.count(('select', 'waba')) == 1


async def test_lost_current_mapping_removes_verified_fact():
    browser, tools = await bound_setup_tools()
    data = await tools.inspect()
    await tools.fill_text(data['fields'][0]['reference'])
    evaluate = browser.page.evaluate
    async def changed(source):
        data = await evaluate(source)
        if isinstance(data, dict) and 'fields' in data:
            data['fields']['campaign_name']['visible'] = False
        return data
    browser.page.evaluate = changed
    await tools.inspect()
    assert 'campaign_name' in tools.execution_summary()['remaining_fields']
    assert 'CAMPAIGN_NAME_VERIFIED' in {f['kind'] for f in browser.trace.step_facts}


def test_missing_setup_usage_never_inherits_login_usage():
    import modules.whatsapp.workflow as workflow
    assert hasattr(workflow, 'request_usage'), 'Need request-complete stage usage accounting'
    trace = Trace()
    trace.model_diagnostics = [
        {'role':'doer','stage':'MODEL_CALL','usage_recorded':True,'input_tokens':100,'output_tokens':20,'total_tokens':120},
        {'role':'doer','stage':'MODEL_CALL','status':'STARTED'}]
    trace.role_usage['doer'].update(requests=2,usage_available=True,input_tokens=100,output_tokens=20,total_tokens=120)
    assert workflow.request_usage(trace,0,1)['total_tokens'] == 120
    setup = workflow.request_usage(trace,1)
    assert setup['requests'] == 1 and setup['total_tokens'] is None
    assert setup['input_tokens'] is setup['output_tokens'] is None
    assert workflow.request_usage(trace)['total_tokens'] is None


async def test_interrupted_write_records_timing_outcome_and_unsafe_retry():
    import asyncio
    browser, tools = await bound_setup_tools()
    data = await tools.inspect(); ref = data['fields'][0]['reference']
    async def interrupted(reference):
        tools.active_approach=('campaign_name','fill:control:DIRECT')
        tools.write_started=True
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await tools.invoke_recorded('fill_text',interrupted,ref)
    event = tools.events[-1]
    assert event['seconds'] >= 0 and event['result']['ok'] is False
    assert event['result']['terminal'] and event['result']['retry_safe'] is False
    assert event['result']['error'] == 'SETUP_ACTION_UNCERTAIN_NO_RETRY'
    assert tools.execution_summary()['failure']['reason'] == 'SETUP_ACTION_UNCERTAIN_NO_RETRY'


@pytest.mark.skipif(os.getenv('RUN_REAL_PAGE1_DOER') != '1', reason='Explicit local Chromium Page 1 acceptance')
async def test_actual_chromium_native_skill_doer_and_failure_evidence(tmp_path):
    from app.config import Config, OriginPolicy
    from browser.stagehand_client import StagehandClient
    from browser.protected_tabs import create_protected_tab
    from evals.local_browser_baseline import settled
    from evals.local_setup_agent_acceptance import local_fixture_server, fixture_facts
    from modules.whatsapp.tools import SetupTools
    from modules.whatsapp.failure_inspection import capture_setup_failure
    trace = Trace()
    with local_fixture_server() as server:
        browser = StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'], headless=True,
            login_url='about:blank', expected_host='127.0.0.1', max_actions=400, timeout_seconds=120),
            OriginPolicy({server.origin}), trace, fixture=True)
        try:
            await browser.start()
            original = browser.page; context = browser.browser.context; process = browser.process
            await original.goto('about:blank'); await settled(browser, browser.guard.primary_session)
            page, binding = await create_protected_tab(browser)
            browser.page = page; browser.setup_binding = binding; browser.login_page = original
            browser.task_phase = 'setup'
            await page.goto(server.url); await settled(browser, binding.session)
            tools = SetupTools.for_fixture(browser, server.url, authorized_mutations=True)
            model = Page1Model()
            agent, _ = create_setup_doer(model, tools, trace, InferenceBudget(24), offline=True, native_skill=True)
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                result = await agent.ainvoke({'messages':[{'role':'user','content':'Complete the approved Page 1 fixture.'}]},
                    {'configurable':{'thread_id':trace.run_id},'recursion_limit':100})
            assert result['structured_response'].status == 'SETUP_FILLED_UNSAVED'
            assert (await tools.verify())['ok']
            facts = await fixture_facts(page)
            assert all(facts[k] for k in ('campaign_matches','waba_matches','business_matches','tag_matches'))
            assert facts['event_counts']['forbidden_clicks'] == facts['event_counts']['submissions'] == server.fixture_posts == 0
            assert await page.evaluate('window.scrollY>0')
            assert context is browser.browser.context and process is browser.process
            assert browser.login_submissions == trace.openai_api_calls == 0
            operations = tools.execution_summary()['browser_operations']
            assert not any(op['field']=='waba' and op['operation']=='select' for op in operations)
            assert any(op['field']=='tags' and op['operation']=='click' and op['verified'] for op in operations)
            # A local operator change invalidates current readback; evidence must
            # be preserved before owned cleanup, with no attempt to restore it.
            await page.locator('#local_business').select_option([''])
            assert not (await tools.verify())['ok']
            evidence = await capture_setup_failure(browser, tools, tmp_path)
            assert not browser.close_started
            assert evidence['dom_status'] == 'FRESH_SANITIZED_STRUCTURE'
            assert evidence['screenshot']['status'] == 'FULL_FRAME_REDACTED'
            assert (tmp_path/evidence['screenshot']['file']).read_bytes().startswith(b'\x89PNG')
            (ROOT/'outputs/phase-b-20261113/local-native.json').write_text(json.dumps({
                'four_fields_verified':True,'skill_provenance':tools.skill_provenance,
                'local_model_requests':model.calls,'provider_requests':0,'evidence':evidence,
                'forbidden_clicks':0,'fixture_posts':server.fixture_posts},indent=2))
        finally:
            await browser.close()
        assert trace.cleanup_result == 'CLOSED_AND_PROFILE_REMOVED'
