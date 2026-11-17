"""Targeted repair regressions. Real browser tests use loopback only; no paid inference."""
import asyncio
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import Config,OriginPolicy
from app.trace import Trace
from app.state import Observation,Result
from browser.stagehand_client import StagehandClient
from browser.evidence_diagnostics import EvidenceFailure,evidence_diagnostic

@pytest.fixture
def owned(tmp_path,monkeypatch):
    import browser.lifecycle as lifecycle
    monkeypatch.setattr(lifecycle,'PROFILE_ROOT',tmp_path)
    browser=StagehandClient(Config('unused'),OriginPolicy(set()),Trace(),fixture=True)
    browser.profile=tmp_path/browser.trace.run_id;browser.profile.mkdir()
    process=SimpleNamespace(exited=lambda:True,close=lambda:None)
    browser.process=process
    browser.browser=SimpleNamespace(close=AsyncMock())
    browser.guard=SimpleNamespace(closing=False,close=AsyncMock())
    browser.stagehand=SimpleNamespace(close=AsyncMock())
    return browser

@pytest.mark.parametrize('sdk_error',[False,True])
async def test_confirmed_cleanup_survives_sdk_exception_and_is_idempotent(owned,sdk_error):
    if sdk_error:owned.stagehand.close.side_effect=RuntimeError('PRIVATE-SDK-TEXT')
    await owned.close();await owned.close()
    assert owned.trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
    assert not owned.profile.exists() and owned.stagehand.close.await_count==1
    assert owned.trace.cleanup_diagnostic['state']=='CONFIRMED'
    assert bool(owned.trace.cleanup_diagnostic['warnings'])==sdk_error
    assert 'PRIVATE' not in json.dumps(owned.trace.summary())

async def test_unconfirmed_process_never_removes_profile_or_claims_success(owned):
    owned.process.exited=lambda:False
    with pytest.raises(RuntimeError):await owned.close()
    assert owned.profile.exists() and owned.trace.cleanup_result=='CLEANUP_FAILED'
    assert owned.trace.cleanup_diagnostic['process_exit_confirmed'] is False

async def test_profile_removal_failure_is_confirmed_failure(owned,monkeypatch):
    def denied(*a,**k):raise PermissionError('PRIVATE-PATH')
    monkeypatch.setattr('browser.lifecycle.shutil.rmtree',denied)
    with pytest.raises(RuntimeError):await owned.close()
    assert owned.trace.cleanup_result=='CLEANUP_FAILED'
    assert owned.trace.cleanup_diagnostic['warnings'][-1]['category']=='PROFILE_REMOVE_FAILED'

@pytest.mark.parametrize('deadline',[False,True])
@pytest.mark.parametrize('cleanup_failure',[False,True])
async def test_enter_or_deadline_always_closes_then_retains_teacher_review(owned,monkeypatch,deadline,cleanup_failure):
    from deep_agents.controller import _execute_doer,review_completed
    from deep_agents.schemas import DoerReport
    from deep_agents.permissions import InferenceBudget
    from deep_agents.offline import SyntheticChatModel
    owned.page=object();owned.evidence_review=True
    if cleanup_failure:owned.process.exited=lambda:False
    owned.start=AsyncMock()
    owned.observe=AsyncMock(return_value=Observation(expected_host=True))
    owned.guard.failed=False;owned.guard.blocking_hosts=set()
    class Agent:
        async def ainvoke(self,*a,**k):return {'structured_response':DoerReport(status='blocked',reason='unrecognized_page')}
    monkeypatch.setattr('deep_agents.controller.create_doer',lambda *a,**k:(Agent(),None))
    monkeypatch.setattr('browser.discovery.ReadOnlyDiscovery.start',AsyncMock())
    hold=AsyncMock(side_effect=TimeoutError() if deadline else None,return_value=None)
    monkeypatch.setattr('browser.evidence_review.evidence_review_hold',hold)
    result=await _execute_doer(owned,SimpleNamespace(entry_url='http://127.0.0.1:1234',navigation_count=1,terminal_error=False),
        'test',None,InferenceBudget(4),True,hold_seconds=1)
    assert result['cleanup_result']==('CLEANUP_FAILED' if cleanup_failure else 'CLOSED_AND_PROFILE_REMOVED')
    assert owned.observe.await_count==2
    await review_completed(result,owned.trace,SyntheticChatModel(role='teacher'),InferenceBudget(4),True)
    assert result['teacher_status']=='ANALYZED'
    if deadline:assert result['inspection_status']=='HOLD_DEADLINE_REACHED'

def test_unresolved_verification_and_guard_loss_always_stop_next_live_submission():
    from modules.login.policy import login_safety_stop
    result={'cleanup_result':'CLOSED_AND_PROFILE_REMOVED','verification':str(Result.UNEXPECTED_PAGE),
        'login_submissions':1,'teacher_status':'ANALYZED'}
    assert login_safety_stop(result,True)=='LIVE_STATE_NOT_INDEPENDENTLY_VERIFIED_NO_FURTHER_SUBMISSION'
    result.update(verification=str(Result.AUTHENTICATED_AND_VERIFIED),navigation_guard_status={'failed':True})
    assert login_safety_stop(result,True)=='NAVIGATION_GUARD_UNAVAILABLE'

async def test_lost_guard_cannot_reuse_historical_verified_snapshot(owned):
    from deep_agents.controller import capture_verification
    owned.trace.add('verify',Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True),Result.AUTHENTICATED_AND_VERIFIED)
    owned.guard.failed=True;owned.guard.blocking_hosts=set();owned.guard.failure_diagnostic={'stage':'READER','category':'CONNECTION_CLOSED'}
    owned.observe=AsyncMock(side_effect=PermissionError('PRIVATE'))
    result={}
    await capture_verification(owned,SimpleNamespace(terminal_error=False),result)
    assert result['verification']==Result.UNEXPECTED_PAGE
    assert result['reason_code']=='NAVIGATION_GUARD_UNAVAILABLE'
    assert result['missing_evidence']==['CURRENT_INDEPENDENT_OBSERVATION']
    assert owned.trace.verification_snapshot is not None

@pytest.mark.parametrize('error,code',[(EvidenceFailure('INVALID_SELECTOR'),'INVALID_SELECTOR'),
    (FileNotFoundError(),'MISSING_EVIDENCE_CONFIGURATION'),(PermissionError('PRIVATE'),'OBSERVATION_POLICY_BLOCKED'),
    (RuntimeError('PRIVATE'),'INTERNAL_VERIFIER_EXCEPTION')])
def test_diagnostics_are_structured_and_do_not_serialize_exception_text(error,code):
    value=evidence_diagnostic(error,'VERIFIER')
    assert value['code']==code and set(value['gates'].values())=={'NOT_CHECKED'}
    assert 'PRIVATE' not in json.dumps(value)

async def test_optional_cdp_connection_loss_is_isolated_from_guard():
    from browser.readonly_cdp import ReadOnlyCDP
    from browser.navigation_guard import NavigationGuard
    guard=NavigationGuard(OriginPolicy(set()))
    class Lost:
        def __aiter__(self):return self
        async def __anext__(self):raise RuntimeError('PRIVATE-WS-REASON')
    monitor=ReadOnlyCDP(lambda e:None);monitor.ws=Lost()
    await monitor._read()
    assert monitor.failure=='DISCOVERY_TRANSPORT_UNAVAILABLE' and not guard.failed
    assert 'PRIVATE' not in monitor.failure

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Explicit headed loopback only')
async def test_headed_guard_monitor_idle_discovery_and_confirmed_cleanup(tmp_path):
    from evals.fixture import fixture_server
    from browser.discovery import ReadOnlyDiscovery
    from pydantic import SecretStr
    with fixture_server() as server:
        from dataclasses import replace
        from urllib.parse import urlsplit
        cfg=replace(Config(os.environ['CHROMIUM_EXECUTABLE_PATH']),timeout_seconds=120,
            expected_host=urlsplit(server.base).hostname,evidence_file=str(tmp_path/'evidence.json'))
        browser=StagehandClient(cfg,OriginPolicy({server.base}),Trace(),fixture=True)
        browser.expected_account=SecretStr('synthetic-tenant')
        try:
            await browser.start()
            browser.discovery=ReadOnlyDiscovery(browser);await browser.discovery.start()
            await browser.navigate(server.base+'/start?scenario=already')
            await browser.settle('#dashboard')
            await browser.page.evaluate("fetch('/context').then(r=>r.json()).then(()=>true)")
            await asyncio.sleep(45)
            summary=await browser.discovery.prepare()
            assert summary['observed_json_resources']>=1
            from modules.login.evidence import EvidenceConfig,observe_live
            browser.evidence=EvidenceConfig(reviewed=True,dashboard_selector='#dashboard',tenant_selector='#tenant',
                readonly_path='/context',tenant_json_key='tenant',authenticated_json_key='authenticated')
            url=await browser.page.url()
            from modules.login.verifier import verify
            assert verify(await observe_live(browser,url))==Result.AUTHENTICATED_AND_VERIFIED
            browser.evidence=browser.evidence.model_copy(update={'dashboard_selector':'['})
            assert not (await observe_live(browser,url)).dashboard
            assert browser.verification_details['issues']==['INVALID_SELECTOR']
            browser.evidence=browser.evidence.model_copy(update={'dashboard_selector':'#missing','readonly_path':'/missing'})
            assert not (await observe_live(browser,url)).readonly_access
            assert set(browser.verification_details['issues'])>={'MISSING_DASHBOARD_INDICATOR','READONLY_RESOURCE_NOT_OBSERVED'}
            browser.evidence=browser.evidence.model_copy(update={'dashboard_selector':'#dashboard','readonly_path':'/context'})
            browser.expected_account=SecretStr('other-synthetic')
            assert not (await observe_live(browser,url)).tenant_matches
            assert set(browser.verification_details['issues'])>={'TENANT_MISMATCH','ACCOUNT_CONTEXT_CHANGED'}
            browser.expected_account=SecretStr('synthetic-tenant')
            await browser.page.evaluate("document.body.insertAdjacentHTML('beforeend','<div role=alert>Session expired</div>')")
            assert verify(await observe_live(browser,url))==Result.SESSION_EXPIRED
            assert 'BROWSER_SESSION_EXPIRED' in browser.verification_details['issues']
            # Losing optional discovery must not close or weaken the navigation guard.
            await browser.discovery.close()
            assert not browser.guard.failed and not (await browser.observe()).readonly_access
        finally:await browser.close()
        assert browser.trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        assert not browser.profile.exists()
