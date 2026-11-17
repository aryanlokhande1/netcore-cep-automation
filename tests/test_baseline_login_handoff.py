"""Narrow login handoff contracts; no browsers, credentials, .env or providers."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import Config,OriginPolicy,ConfigurationError
from app.trace import Trace
from app.whatsapp_baseline import BaselineBrowser,validate_login_options,prepare_setup,run_baseline
from modules.whatsapp.browser_baseline import BaselineStop
from modules.whatsapp.baseline_login import execute_login,validate_handoff


async def test_missing_explicit_login_budget_never_loads_configuration(monkeypatch):
    monkeypatch.setattr(Config,'load',lambda:pytest.fail('No private config before approved budget'))
    for budget in (0,7):
        args=SimpleNamespace(authorized_live=True,login_mode='doer',login_api_budget=budget)
        with pytest.raises(ConfigurationError):await run_baseline(args)
    assert validate_login_options(SimpleNamespace(login_mode='manual',login_api_budget=0))=='manual'


async def test_doer_timeout_retains_owned_cleanup_and_closes_login_capability(monkeypatch):
    import deep_agents.controller as controller
    browser=BaselineBrowser(Config('unused'),OriginPolicy({'http://127.0.0.1:9000'}),Trace(),fixture=True)
    tools=SimpleNamespace(references={'opaque':{}},terminal_error=False)
    execute=AsyncMock(return_value={'error':'TIMEOUT'})
    monkeypatch.setattr(controller,'_execute_doer',execute)
    with pytest.raises(BaselineStop,match='LOGIN_DOER_STOPPED'):
        await execute_login(browser,tools,None,None,offline=True)
    assert execute.call_args.kwargs['close_session'] is False
    assert browser.login_closed and not browser.close_started and tools.references=={}
    with pytest.raises(PermissionError,match='LOGIN_PHASE_CLOSED'):browser.claim_login_submission()
    with pytest.raises(BaselineStop,match='LOGIN_CHECKPOINT_REQUIRED'):await prepare_setup(browser)


async def test_known_security_failure_cannot_be_overridden_by_later_safe_page():
    for flag in ('challenge','rejected','expired','account_restricted'):
        browser=SimpleNamespace(close_started=False,trace=SimpleNamespace(events=[{'observation':{flag:True}}]))
        with pytest.raises(BaselineStop,match='AUTHENTICATION_OR_SECURITY_STOP'):
            await validate_handoff(browser,{'error':None})
    with pytest.raises(BaselineStop,match='AUTHENTICATION_OR_SECURITY_STOP'):
        await validate_handoff(browser,{'reason_code':'INCORRECT_TENANT'})
    from app.state import Observation
    uncertain=SimpleNamespace(close_started=False,trace=Trace(),login_submissions=1,
        guard=SimpleNamespace(failed=False,primary_observable=lambda:True,blocking_hosts=set()),
        observe=AsyncMock(return_value=Observation(expected_host=True)))
    with pytest.raises(BaselineStop,match='SINGLE_COMPLETED_LOGIN_REQUIRED'):
        await validate_handoff(uncertain,{'error':None})
    async def wrong_tenant_now():
        uncertain.verification_details={'reason':'INCORRECT_TENANT'}
        return Observation(expected_host=True)
    uncertain.observe=wrong_tenant_now
    with pytest.raises(BaselineStop,match='AUTHENTICATION_OR_SECURITY_STOP'):
        await validate_handoff(uncertain,{'error':None,'reason_code':'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED'})


async def test_checkpoint_context_change_stops_before_creating_tab(monkeypatch):
    import app.whatsapp_baseline as module
    browser=SimpleNamespace(baseline_checkpoint=('approved',),setup_tab_attempted=False)
    monkeypatch.setattr(module,'login_context',AsyncMock(return_value=('changed',)))
    create=AsyncMock(side_effect=AssertionError('No tab before current C proof'))
    monkeypatch.setattr(module,'create_protected_tab',create)
    with pytest.raises(BaselineStop,match='LOGIN_CHECKPOINT_CONTEXT_CHANGED'):await module.prepare_setup(browser)
    create.assert_not_awaited()
