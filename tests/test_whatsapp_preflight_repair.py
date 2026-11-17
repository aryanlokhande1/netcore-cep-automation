"""Saved trace, pure DOM/model doubles and guarded failure-hold preflight; no live login."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import ROOT
from app.state import Observation,Result
from modules.whatsapp.contract import Phase1Limits,SetupStop
from modules.whatsapp.safety import safety_diagnostic,SAFETY_SNAPSHOT,NEGATIVES
from modules.whatsapp.failure_inspection import inspect_failed_setup
from modules.whatsapp.session import PocGrant
from modules.login.verifier import verify
from test_whatsapp_phase1 import session,setup_tools,bound_setup_tools,fill_all,protocol_metadata_transport
from test_evidence_discovery_repair import dom_expression,BASE

RUN='985a065565e84b778777763c06d5baba'


def snapshot(**updates):
    return {**{k:False for k in NEGATIVES},'negative_scope_complete':True,**updates}


def test_saved_run_proves_negative_preflight_branch_but_not_which_boolean():
    result=json.loads((ROOT/'outputs'/f'{RUN}-result.json').read_text(encoding='utf-8'))
    events=json.loads((ROOT/'outputs'/f'{RUN}.json').read_text(encoding='utf-8'))
    assert len(events)==260
    assert events[214]['diagnostic']['code']=='WHATSAPP_DESTINATION_OPENED'
    assert events[215]['stop_code']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert events[216]['diagnostic']['stage']=='STAGEHAND_CLOSE'
    assert result['poc_checkpoint']=='HUMAN_CONFIRMED_FOR_POC' and result['stage_requests']['setup']==0
    assert result['navigation_guard_status']['failed'] is False
    assert not any(f['kind']=='SETUP_FORM_DETECTED' for f in result['step_facts'])
    assert not any('negative_flags' in e.get('diagnostic',{}) or e.get('diagnostic',{}).get('state')=='SETUP_PREFLIGHT_FAILED' for e in events)
    assert events[221]['diagnostic']['code']=='DOCUMENT_BLOCKED_BY_POLICY'
    assert events[221]['elapsed_seconds']>events[216]['elapsed_seconds']


@pytest.mark.parametrize('negative',NEGATIVES)
def test_exact_negative_predicate_is_sanitized_and_remains_blocking(negative):
    result=safety_diagnostic(snapshot(**{negative:True}))
    assert result['code']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert result['failed_predicates']==[negative+'_present']
    assert result['gates'][negative+'_absent']=='FAIL'
    assert result['strict_independent_authentication_reapplied'] is False


def test_coverage_gap_is_distinct_from_rejected_authentication_but_still_blocked():
    result=safety_diagnostic(snapshot(negative_scope_complete=False,_scope_diagnostics={
        'shadow_roots_uninspected':1,'permitted_frames_unavailable':0,'documents_inspected':1,
        'private_text':'FORBIDDEN_DOM_CONTENT_MARKER'}))
    assert result['code']=='SAFETY_OBSERVATION_INCOMPLETE' and result['failed_predicates']==['negative_scope_complete']
    assert not any(result['negative_flags'].values()) and result['scope_reason_counts']['shadow_roots_uninspected']==1
    assert 'FORBIDDEN_DOM_CONTENT_MARKER' not in json.dumps(result)
    both=safety_diagnostic(snapshot(challenge=True,negative_scope_complete=False))
    assert both['code']=='AUTHENTICATION_OR_SECURITY_STOP' and both['failed_predicates']==['challenge_present','negative_scope_complete']


@pytest.mark.parametrize('invalid',[None,{}, {k:False for k in NEGATIVES},snapshot(challenge='FORBIDDEN_DOM_CONTENT_MARKER'),snapshot(negative_scope_complete=1)])
def test_malformed_safety_observation_cannot_authorize_actions_or_expose_values(invalid):
    result=safety_diagnostic(invalid)
    assert result['code']=='SAFETY_OBSERVATION_INVALID' and result['failed_predicates']==['safety_snapshot_schema']
    assert 'FORBIDDEN_DOM_CONTENT_MARKER' not in json.dumps(result)


def test_actual_safety_expression_inspects_open_root_without_private_dom_values():
    doc={'url':BASE+'/', 'children':[{'tag':'div','text':'FORBIDDEN_DOM_CONTENT_MARKER','shadow':True}]}
    actual=dom_expression(SAFETY_SNAPSHOT,doc)
    result=safety_diagnostic(actual)
    assert result['code'] is None
    assert result['scope_reason_counts']['shadow_roots_uninspected']==0
    assert result['scope_reason_counts']['open_shadow_roots_inspected']==1
    assert not any(result['negative_flags'].values())
    assert 'FORBIDDEN_DOM_CONTENT_MARKER' not in json.dumps(actual)


@pytest.mark.parametrize('negative',NEGATIVES)
async def test_setup_scan_persists_actual_failed_predicate_before_throwing(negative):
    browser,tools=await bound_setup_tools();browser.page.negative=negative
    result=await tools.inspect()
    assert result['error']=='AUTHENTICATION_OR_SECURITY_STOP'
    diagnostic=next(e['diagnostic'] for e in browser.trace.events if e.get('diagnostic',{}).get('state')=='SETUP_PREFLIGHT_FAILED')
    assert diagnostic['failed_predicates']==[negative+'_present','negative_scope_complete'] and diagnostic['negative_flags'][negative]
    assert diagnostic['poc_route']=='SUBMITTED_ONCE_POC' and not diagnostic['guard_failed']
    assert not browser.page.actions


async def test_incomplete_coverage_allows_form_observation_but_not_unauthorized_writes():
    browser,tools=await bound_setup_tools()
    original=browser.page.evaluate
    async def incomplete(source):
        if 'const alerts' in source:return snapshot(negative_scope_complete=False,
            _scope_diagnostics={'permitted_frames_unavailable':1,'documents_inspected':1,'open_shadow_roots_inspected':0})
        return await original(source)
    browser.page.evaluate=AsyncMock(side_effect=incomplete)
    tools.authorized_mutations=False
    result=await tools.inspect()
    assert result['ok'] and result['form_recognized']
    assert tools.safety_status['code']=='SAFETY_OBSERVATION_INCOMPLETE'
    assert tools.safety_status['scope_reason_counts']['permitted_frames_unavailable']==1
    assert not any(tools.safety_status['negative_flags'].values())
    denied=await tools.fill_text(result['fields'][0]['reference'])
    assert denied['error']=='MUTATION_AUTHORIZATION_REQUIRED' and not denied['terminal']
    assert not browser.page.actions and (await tools.inspect())['ok']
    assert any(f['kind']=='SETUP_FORM_DETECTED' for f in browser.trace.step_facts)


async def test_redirect_is_still_blocked_and_identified_before_any_dom_read():
    browser,tools=await bound_setup_tools();browser.page.location=browser.config.login_url
    browser.page.evaluate=AsyncMock(side_effect=AssertionError('Unexpected destination must not be scanned'))
    result=await tools.inspect()
    assert result['error']=='UNEXPECTED_SETUP_DESTINATION' and result['terminal']
    assert tools.last_stop=={'phase':'OBSERVATION','reason':'UNEXPECTED_SETUP_DESTINATION','failed_predicate':'HOST'}
    browser.page.evaluate.assert_not_awaited()
    assert browser.login_submissions==1 and not browser.page.actions


async def test_valid_poc_setup_does_not_reapply_strict_independent_login_gates():
    browser,tools=await bound_setup_tools();browser.evidence=None
    current=Observation(expected_host=True)
    assert verify(current)==Result.UNEXPECTED_PAGE
    result=await fill_all(tools)
    assert result['status']=='SETUP_FILLED_UNSAVED'
    assert browser.evidence is None and browser.trace.verification_snapshot is None
    assert not any(e.get('diagnostic',{}).get('state')=='SETUP_PREFLIGHT_FAILED' for e in browser.trace.events)


async def bound_failure_session():
    browser,tools=setup_tools();await browser.open_setup_tab(tools.grant)
    result={'setup_stop_reason':'AUTHENTICATION_OR_SECURITY_STOP','poc_checkpoint':'HUMAN_CONFIRMED_FOR_POC',
        'setup_status':'BLOCKED','verification':'UNEXPECTED_PAGE','error':None,
        'setup_failure_diagnostic':{'failed_predicates':['challenge_present'],'negative_flags':{'challenge':True}}}
    return browser,result


async def test_healthy_failed_page_gets_read_only_hold_without_resuming_actions():
    browser,result=await bound_failure_session()
    async def operator(seconds):
        assert seconds==120 and not browser.close_started
        assert browser.task_phase=='inspection' and browser.phase_action_end==browser.trace.actions
        with pytest.raises(SetupStop):browser.budget()
        with pytest.raises(PermissionError):browser.claim_login_submission()
        with pytest.raises(SetupStop):await browser.navigate(browser.config.login_url)
    await inspect_failed_setup(browser,result,Phase1Limits(),hold=operator)
    assert result['failure_inspection']['status']=='OPERATOR_CLOSED'
    assert result['failure_inspection']['resume_allowed'] is False
    assert result['setup_status']=='BLOCKED' and result['verification']=='UNEXPECTED_PAGE'
    assert not browser.close_started and browser.login_submissions==1


async def test_guard_failure_skips_failure_hold_and_never_reopens_browser():
    browser,result=await bound_failure_session();browser.guard.failed=True
    operator=AsyncMock()
    await inspect_failed_setup(browser,result,Phase1Limits(),hold=operator)
    assert result['failure_inspection']['status']=='SKIPPED_PROTECTION_UNAVAILABLE'
    operator.assert_not_awaited()


async def test_protection_loss_during_failure_hold_ends_it_immediately():
    browser,result=await bound_failure_session();entered=asyncio.Event()
    async def operator(seconds):entered.set();await asyncio.Future()
    async def lose_guard():
        await entered.wait();browser.guard.fail_closed('TEST_FAILURE',code='DOCUMENT_PROTECTION_UNCERTAIN')
    losing=asyncio.create_task(lose_guard())
    await asyncio.wait_for(inspect_failed_setup(browser,result,Phase1Limits(),hold=operator),1)
    await losing
    assert result['failure_inspection']['status']=='CLOSED_PROTECTION_LOST' and browser.guard.failed


async def test_failure_hold_timeout_does_not_turn_into_success_or_retry():
    browser,result=await bound_failure_session()
    async def deadline(seconds):raise TimeoutError
    await inspect_failed_setup(browser,result,Phase1Limits(),hold=deadline)
    assert result['failure_inspection']['status']=='DEADLINE_REACHED'
    assert result['setup_status']=='BLOCKED' and browser.login_submissions==1


async def test_failure_hold_bounds_a_stalled_operator_and_cancels_background_tasks():
    browser,result=await bound_failure_session();cancelled=asyncio.Event()
    async def stalled(seconds):
        try:await asyncio.Future()
        finally:cancelled.set()
    await asyncio.wait_for(inspect_failed_setup(browser,result,Phase1Limits(hold_seconds=1),hold=stalled),2)
    assert cancelled.is_set() and result['failure_inspection']['status']=='DEADLINE_REACHED'
    assert result['failure_inspection']['seconds']==1 and result['setup_status']=='BLOCKED'
    assert browser.phase_action_end==browser.trace.actions and browser.login_submissions==1
    assert not browser.close_started


async def test_failure_hold_cancellation_still_leaves_cleanup_to_owner_and_no_resume():
    browser,result=await bound_failure_session()
    async def cancelled(seconds):raise asyncio.CancelledError
    await inspect_failed_setup(browser,result,Phase1Limits(),hold=cancelled)
    assert result['failure_inspection']['status']=='OPERATOR_CANCELLED'
    assert result['error']=='OPERATOR_CANCELLED' and result['setup_status']=='BLOCKED'
    assert result['setup_stop_reason']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert result['failure_inspection']['resume_allowed'] is False
    assert not browser.close_started and browser.login_submissions==1


async def test_failure_hold_respects_global_session_deadline():
    browser,result=await bound_failure_session();browser.started-=1000
    operator=AsyncMock()
    await inspect_failed_setup(browser,result,Phase1Limits(),hold=operator)
    assert result['failure_inspection']['status']=='SKIPPED_SESSION_DEADLINE'
    operator.assert_not_awaited()


async def test_failure_hold_noninteractive_is_recorded_without_hanging(monkeypatch):
    import modules.whatsapp.failure_inspection as module
    browser,result=await bound_failure_session();monkeypatch.setattr(module.sys.stdin,'isatty',lambda:False)
    await inspect_failed_setup(browser,result,Phase1Limits())
    assert result['failure_inspection']['status']=='SKIPPED_NONINTERACTIVE'


async def test_first_doer_observation_detects_challenge_then_hold_precedes_cleanup(monkeypatch,tmp_path):
    import deep_agents.controller as controller
    import modules.whatsapp.workflow as workflow
    from deep_agents.schemas import DoerReport
    browser=session();browser.page.location='https://'+browser.config.expected_host+'/'
    browser.start=AsyncMock();browser.observe=AsyncMock(return_value=Observation(expected_host=True))
    browser.verification_details={'reason':'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED','missing':['dashboard','tenant_matches','readonly_access','evidence_configured']}
    original_create=browser.browser.context.new_page
    async def challenged_page(url):
        page=await original_create(url);page.negative='challenge';return page
    browser.browser.context.new_page=challenged_page
    async def close():
        assert any(e.get('diagnostic',{}).get('state')=='FAILURE_INSPECTION_COMPLETED' for e in browser.trace.events)
        browser.trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED';browser.trace.add('close')
    browser.close=AsyncMock(side_effect=close)
    class LoginAgent:
        async def ainvoke(self,*args):return {'structured_response':DoerReport(status='blocked',reason='wrong_context')}
    monkeypatch.setattr(controller,'create_doer',lambda *args,**kwargs:(LoginAgent(),None))
    monkeypatch.setattr(controller,'ROOT',tmp_path)
    from modules.whatsapp.agent import create_setup_doer
    from modules.whatsapp.session import Phase1Budget
    from test_setup_agent_refactor import SyntheticChatModel,AIMessage,ChatResult,ChatGeneration
    class StopModel(SyntheticChatModel):
        def _generate(self,messages,**kwargs):
            self.calls+=1
            name,args=('observe_page',{}) if self.calls==1 else ('SetupReport',{'status':'BLOCKED','reason':'tool_error'})
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[
                {'name':name,'args':args,'id':str(self.calls),'type':'tool_call'}]))])
    model=StopModel();budget=Phase1Budget(Phase1Limits())
    monkeypatch.setattr(workflow,'create_setup_doer',lambda *args,**kwargs:create_setup_doer(*args,**kwargs,offline=True))
    async def checkpoint(session,seconds):return PocGrant(browser)
    async def inspect_only(seconds):
        assert browser.close.await_count==0 and browser.task_phase=='inspection'
        assert browser.page.negative=='challenge' and browser.page.values=={}
    async def continuation(session,tools,result):
        await workflow.continue_setup(session,tools,result,model,budget,Phase1Limits(),checkpoint=checkpoint,hold=inspect_only)
    login=SimpleNamespace(terminal_error=False,navigation_count=1,entry_url=browser.config.login_url)
    result=await controller._execute_doer(browser,login,'login',None,None,True,after_login=continuation,login_timeout=1)
    assert result['setup_stop_reason']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert result['setup_failure_diagnostic']['failed_predicates']==['challenge_present','negative_scope_complete']
    assert result['failure_inspection']['status']=='OPERATOR_CLOSED'
    assert result['authentication_verification']=='UNEXPECTED_PAGE' and result['setup_status']=='BLOCKED'
    assert result['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED' and browser.close.await_count==1
    assert browser.login_submissions==1
    assert model.calls==0 and budget.stage_requests['setup']==0
    assert not any(e.get('diagnostic',{}).get('state')=='SETUP_DOER_STARTED' for e in browser.trace.events)
    assert not any(f['kind']=='WHATSAPP_DESTINATION_OPENED' for f in browser.trace.step_facts)


def test_shared_teacher_security_contract_and_saved_real_run_are_unchanged():
    manifest=json.loads((ROOT/'outputs/whatsapp-preflight-preservation.json').read_text(encoding='utf-8'))
    for name,digest in manifest.items():
        # The subsequent shadow-coverage repair intentionally replaces only this
        # file's negative scanner; its positive constants are independently pinned.
        if name=='modules/login/discovery.py':
            import modules.login.discovery as shared
            preserved=json.loads((ROOT/'outputs/shadow-safety-preservation.json').read_text(encoding='utf-8'))
            for constant,sha in preserved['shared_positive_constants'].items():
                assert hashlib.sha256(getattr(shared,constant).encode()).hexdigest()==sha
        elif name=='modules/whatsapp/session.py':
            from test_setup_destination import assert_session_only_destination_policy_changed
            assert_session_only_destination_policy_changed(digest)
        else:assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
