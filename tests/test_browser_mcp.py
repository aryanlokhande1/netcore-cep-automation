from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip('mcp')

from app.browser_mcp import ActiveStep, BrowserRouter, ToolDenied, ToolScope
from app.trace import Trace
from deep_agents.live_tools import LiveBrowserTools
from modules.whatsapp.contract import V2ManualNoVisionAction
from modules.whatsapp.ga_teacher_evidence import GATeacherEvidence


@pytest.mark.asyncio
@pytest.mark.parametrize('phase', ['protect', 'invoke'])
async def test_independent_observer_failure_preserves_only_sanitized_details(phase):
    from langchain_core.tools import StructuredTool
    from app.browser_mcp import EmptyArguments
    from app.browser_mcp_client import StepTools, open_step
    from modules.whatsapp.contract import V2Stop

    trace = Trace()
    browser = SimpleNamespace(trace=trace)
    async def verify():
        if phase == 'invoke':
            raise RuntimeError('private message must never leave the browser handler')
        return {}
    async def protect():
        if phase == 'protect':
            raise V2Stop('SMS_SETUP_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        return ('target', 'session', 1)
    tool = StructuredTool.from_function(coroutine=verify, name='verify_sms_setup',
                                        description='Verify Setup', args_schema=EmptyArguments)
    observer = SimpleNamespace(browser=browser, stage='sms_setup', protect=protect,
                               registered_tools=lambda: [tool])
    step = StepTools('observer', 'sms_setup', frozenset({tool.name}))
    async with open_step(observer, step, document_identity=lambda _: ('target', 'session', 1),
                         independent_observer=True) as client:
        with pytest.raises(V2Stop, match='MCP_BROWSER_DISPATCH_FAILED') as failure:
            await client.read(tool.name)
    assert failure.value.diagnostic == {'operation': tool.name, 'phase': phase,
        'category': 'SMS_SETUP_DOCUMENT_CHANGED' if phase == 'protect' else 'OBSERVER_HANDLER_ERROR'}


@pytest.mark.asyncio
async def test_browser_action_uses_active_contract_and_rechecks_trusted_session():
    trace = Trace()
    browser = SimpleNamespace(trace=trace)
    step = ActiveStep(trace.run_id, browser, 'owned', 'setup')
    current = step
    protection = AsyncMock()
    tools = SimpleNamespace(browser=browser, stage='setup', action_schema=lambda: V2ManualNoVisionAction,
                            action=AsyncMock(return_value=({'ok': True}, None)))
    doer = BrowserRouter(ToolScope(step, 'doer', frozenset({'inspect', 'type'}), lambda: current, protection),
                         whatsapp=tools)
    observer = BrowserRouter(ToolScope(step, 'observer', frozenset({'verify', 'type'}), lambda: current,
                                       protection), whatsapp=tools)
    assert set(doer.catalog()) == {'browser_inspect', 'browser_type'}
    assert set(observer.catalog()) == {'browser_verify'}
    assert doer.catalog()['browser_inspect'].schema['required'] == ['reference']
    with pytest.raises(ToolDenied, match='OUTSIDE_ACTIVE_STEP'):
        await observer.dispatch('browser_type', {'field': 'campaign_name', 'reference': 'a'})
    with pytest.raises(ToolDenied, match='INVALID_BROWSER_ARGUMENTS'):
        await doer.dispatch('browser_inspect', {'reference': 'a', 'field': 'campaign_name'})
    tools.action.assert_not_awaited()
    await doer.dispatch('browser_inspect', {'reference': 'a'})
    tools.action.assert_awaited_once_with({'action': 'inspect', 'reference': 'a'})
    current = ActiveStep(step.run_id, browser, 'different', 'setup')
    assert doer.catalog() == {}
    with pytest.raises(ToolDenied, match='OUTSIDE_ACTIVE_STEP'):
        await doer.dispatch('browser_inspect', {'reference': 'a'})
    assert tools.action.await_count == 1


def test_login_catalog_uses_existing_handler_schemas():
    trace = Trace()
    browser = SimpleNamespace(trace=trace)
    login = SimpleNamespace(browser=browser)
    login.registered_tools = lambda: LiveBrowserTools.registered_tools(login)
    step = ActiveStep(trace.run_id, browser, 'owned', 'login')
    protection = AsyncMock()
    names = frozenset({'navigate_approved', 'inspect_page', 'submit_login', 'verify_dashboard'})
    router = BrowserRouter(ToolScope(step, 'doer', names, lambda: step, protection), login=login)
    assert set(router.catalog()) == names
    assert set(router.catalog()['submit_login'].schema['required']) == {
        'account_reference', 'username_reference', 'password_reference', 'submit_reference'}
    empty = BrowserRouter(ToolScope(step, 'doer', frozenset(), lambda: step, protection), login=login)
    assert empty.catalog() == {}


@pytest.mark.asyncio
async def test_teacher_receives_image_content_from_existing_held_evidence_registration():
    from mcp.types import ImageContent

    trace = Trace()
    browser = SimpleNamespace(trace=trace)
    held = SimpleNamespace(setup=SimpleNamespace(browser=browser),
                           inspect=AsyncMock(return_value={'status': 'OBSERVED'}),
                           view=AsyncMock(return_value=({'status': 'IMAGE_RETURNED'}, 'iVBORw0KGgo=')))
    held.registered = lambda: GATeacherEvidence.registered(held)
    step = ActiveStep(trace.run_id, browser, 'owned', 'setup')
    router = BrowserRouter(ToolScope(step, 'teacher', frozenset({'inspect_ga_setup', 'view_ga_setup'}),
                                     lambda: step, AsyncMock()), held_ga=held)
    await router.dispatch('inspect_ga_setup', {})
    parts = await router.dispatch('view_ga_setup', {'reason': 'layout_ambiguous'})
    assert isinstance(parts[1], ImageContent)
    assert parts[1].mimeType == 'image/png' and parts[1].data == 'iVBORw0KGgo='
    held.view.assert_awaited_once_with('layout_ambiguous')


@pytest.mark.asyncio
async def test_campaign_name_write_scope_is_one_shot_and_reference_bound():
    trace = Trace()
    browser = SimpleNamespace(trace=trace)
    step = ActiveStep(trace.run_id, browser, 'owned', 'setup')
    invoke = AsyncMock(return_value=({'ok': True}, None))
    tools = SimpleNamespace(browser=browser, stage='setup',
                            action_schema=lambda: V2ManualNoVisionAction, action=invoke)
    scope = ToolScope(step, 'doer', frozenset({'type'}), lambda: step, AsyncMock(),
                      campaign_name_reference='fresh')
    router = BrowserRouter(scope, whatsapp=tools)
    assert set(router.catalog()) == {'browser_type'}
    assert router.catalog()['browser_type'].schema['properties']['field']['const'] == 'campaign_name'
    for args in ({'field': 'waba', 'reference': 'fresh'},
                 {'field': 'campaign_name', 'reference': 'stale'},
                 {'field': 'campaign_name', 'reference': 'fresh', 'value': 'arbitrary'}):
        with pytest.raises(ToolDenied):
            await router.dispatch('browser_type', args)
    invoke.assert_not_awaited()
    args = {'field': 'campaign_name', 'reference': 'fresh'}
    await router.dispatch('browser_type', args)
    assert router.catalog() == {}
    with pytest.raises(ToolDenied):
        await router.dispatch('browser_type', args)
    invoke.assert_awaited_once_with({'action': 'type', **args})
