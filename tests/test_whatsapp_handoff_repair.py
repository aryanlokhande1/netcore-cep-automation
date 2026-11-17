"""Saved-record and protocol/model-double preflight only. No browser or network."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from app.config import ROOT
from app.state import Observation,Result
from app.trace import Trace
from deep_agents.schemas import DEFAULT_TASK
from deep_agents.offline import SyntheticChatModel
from deep_agents.doer import create_doer
from modules.whatsapp.contract import SETUP_URL,Phase1Limits,SetupStop
from modules.whatsapp.session import Phase1Budget,PocGrant,healthy_observation,source_signature
from modules.whatsapp.login_handoff import HandoffLoginTools
from browser.protected_tabs import bound_tab_observable
from test_whatsapp_phase1 import session,setup_tools,PageDouble,review_fixture,protocol_metadata_transport

FAILED='3d8675b41e85459d92d8009cf88b2a7d'
SUCCESS='c416f931253644efb7062e5a168f5dfb'


def test_complete_saved_traces_prove_early_model_completion_not_submission_or_guard_failure():
    failed=json.loads((ROOT/'outputs'/f'{FAILED}.json').read_text(encoding='utf-8'))
    baseline=json.loads((ROOT/'outputs'/f'{SUCCESS}.json').read_text(encoding='utf-8'))
    assert len(failed)==94 and len(baseline)==219
    assert failed[62]['observation']==baseline[69]['observation']
    assert failed[62]['observation']['credential_form'] and failed[62]['step_result']=='HUMAN_ACTION_REQUIRED'
    assert failed[66]['tool_name']=='DoerReport' and failed[66]['tool_phase']=='requested'
    assert not any(e.get('tool_name')=='submit_login' for e in failed)
    assert not any(e.get('tool_phase')=='denied' for e in failed)
    assert not any(e.get('diagnostic',{}).get('state')=='GUARD_FAILURE' for e in failed)
    assert baseline[71]['tool_name']=='submit_login'
    assert failed[69]['diagnostic']['stage']=='checkpoint' and failed[70]['stop_code']=='AUTHENTICATION_OR_SECURITY_STOP'


class LoginToolsDouble(HandoffLoginTools):
    async def _inspect(self):
        observation=await self.browser.observe();self.browser.trace.add('observe',observation)
        self.references={key:{'key':key,'selector':'#'+key} for key in ['account','username','password','submit']}
        self.browser.trace.fact('LOGIN_RECOGNIZED')
        return {'ok':True,'state':'observed','observation':observation.model_dump(),'elements':[
            {'reference':ref,'kind':d['key']} for ref,d in self.references.items()]}
    async def _resolve(self,reference):return self.references[reference],None

class EarlyReportModel(SyntheticChatModel):
    choices:list=[]
    def bind_tools(self,tools,**kwargs):
        super().bind_tools(tools,**kwargs);self.choices.append(kwargs.get('tool_choice'));return self
    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.calls+=1
        last=next((m for m in reversed(messages) if isinstance(m,ToolMessage)),None)
        if last is None:name,args='navigate_approved',{'url':'https://login.netcoresmartech.com/'}
        elif last.name=='navigate_approved':name,args='inspect_page',{}
        elif last.name=='inspect_page':
            if 'DoerReport' in self.bound_tool_names:name,args='DoerReport',{'status':'blocked','reason':'wrong_context'}
            else:
                refs={d['kind']:d['reference'] for d in json.loads(last.content)['elements']}
                name,args='submit_login',{k+'_reference':refs[k] for k in ['account','username','password','submit']}
        elif last.name=='submit_login':name,args='verify_dashboard',{}
        else:name,args='DoerReport',{'status':'blocked','reason':'unrecognized_page'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{
            'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}]))])


async def test_genuine_doer_cannot_finish_wrong_context_in_the_normal_form_action_state():
    browser=session();browser.login_submissions=0;browser.trace.step_facts=[]
    browser.page.location=browser.config.login_url
    async def observe():
        return Observation(credential_form=browser.login_submissions==0,login_entry=browser.login_submissions==0,expected_host=browser.login_submissions==1)
    browser.observe=observe
    calls=[]
    async def submit(mapping):
        calls.append(set(mapping));browser.claim_login_submission()
        browser.trace.add('entry');browser.trace.fact('SUBMITTED_ONCE');browser.page.location='https://'+browser.config.expected_host+'/'
    tools=LoginToolsDouble(browser,SimpleNamespace(submit=submit))
    budget=Phase1Budget(Phase1Limits());model=EarlyReportModel()
    agent,_=create_doer(model,tools,browser.trace,budget,offline=True)
    state=await agent.ainvoke({'messages':[{'role':'user','content':DEFAULT_TASK}]},
        {'configurable':{'thread_id':browser.trace.run_id},'recursion_limit':40})
    assert calls==[{'account','username','password','submit'}] and browser.login_submissions==1
    assert 'submit_login' in model.choices and model.calls==5 and browser.trace.openai_api_calls==0
    assert state['structured_response'].reason=='unrecognized_page'
    assert not tools.submission_pending()


@pytest.mark.parametrize('negative',['challenge','expired','rejected','account_restricted'])
def test_completion_gate_does_not_force_submission_under_a_negative_state(negative):
    browser=session();browser.login_submissions=0
    tools=HandoffLoginTools(browser,None);tools.navigation_count=1
    tools.references={k:{'key':k} for k in ['account','username','password','submit']}
    browser.trace.current_observation=Observation(login_entry=True,credential_form=True,**{negative:True}).model_dump()
    assert not tools.submission_pending()


@pytest.mark.parametrize('evidence',['missing','unreviewed','incomplete','valid'])
def test_zero_submit_existing_session_requires_current_independent_evidence(evidence):
    browser=session();browser.login_submissions=0;browser.trace.step_facts=[]
    browser.evidence=None if evidence=='missing' else SimpleNamespace(reviewed=evidence!='unreviewed')
    observed=Observation(expected_host=True,dashboard=evidence!='incomplete',tenant_matches=True,readonly_access=True,evidence_configured=True)
    if evidence=='valid':assert healthy_observation(browser,observed)=='EXISTING_SESSION_VERIFIED'
    else:
        with pytest.raises(SetupStop):healthy_observation(browser,observed)
    assert browser.login_submissions==0 and not browser.trace.step_facts


async def test_new_tab_is_same_context_protected_bound_and_visible_before_navigation():
    browser,tools=setup_tools();original=browser.page;context=browser.browser.context;profile=browser.profile
    await browser.open_setup_tab(tools.grant)
    assert browser.page is not original and browser.login_page is original
    assert browser.browser.context is context and browser.profile is profile and context.creations==1
    assert context.requested_urls==['about:blank']
    assert context.active is browser.page and original in context.items
    assert browser.page.location=='about:blank' and bound_tab_observable(browser.guard,browser.setup_binding)
    assert browser.guard.primary_session=='source-session' and browser.setup_binding.session=='setup-session'
    await browser.navigate(SETUP_URL)
    assert browser.page.location==SETUP_URL and not original.actions
    assert not original.marker and not browser.page.marker
    with pytest.raises(SetupStop):await browser.open_setup_tab(tools.grant)


async def test_waits_for_interception_and_protection_before_any_navigation():
    browser,tools=setup_tools();context=browser.browser.context;create=context.new_page
    protection_observed_pending=asyncio.Event();completed=asyncio.Event();tasks=[]
    original_send=browser.guard.send
    async def observe_pending(method,params=None,session=None):
        data=await original_send(method,params,session)
        if method=='Target.getTargets' and len(context.items)>1 and not context.items[-1].owner.protected:
            protection_observed_pending.set()
        return data
    browser.guard.send=observe_pending
    async def delayed(url):
        page=await create(url);page.owner.protected=False
        async def finish_protection():
            await protection_observed_pending.wait()
            assert page.location=='about:blank' and not page.actions
            page.owner.protected=True;browser.guard.lifecycle_changed.set();completed.set()
        tasks.append(asyncio.create_task(finish_protection()))
        return page
    context.new_page=delayed
    await browser.open_setup_tab(tools.grant)
    await asyncio.gather(*tasks)
    assert protection_observed_pending.is_set() and completed.is_set()
    assert bound_tab_observable(browser.guard,browser.setup_binding)


async def test_waits_for_frame_loader_coherence_before_pinning_the_blank_document():
    browser,tools=setup_tools();context=browser.browser.context
    frame_query_observed=asyncio.Event();event_applied=asyncio.Event()
    original_send=browser.guard.send;frame_queries=0
    async def delayed_frame_event():
        await frame_query_observed.wait()
        page=context.items[-1]
        assert page.location=='about:blank' and not page.actions
        browser.guard.frame_event({'method':'Page.frameNavigated','sessionId':page.owner.session,
            'params':{'frame':{'id':page.owner.frame_id,'loaderId':'settled-blank-loader','url':'about:blank'}}})
        event_applied.set()
    async def frame_tree(method,params=None,session=None):
        nonlocal frame_queries
        data=await original_send(method,params,session)
        if method=='Page.getFrameTree' and session=='setup-session':
            frame_queries+=1
            data['frameTree']['frame']['loaderId']='settled-blank-loader'
            if not event_applied.is_set():frame_query_observed.set()
        return data
    browser.guard.send=frame_tree
    event_task=asyncio.create_task(delayed_frame_event())
    try:
        await browser.open_setup_tab(tools.grant)
        await event_task
    finally:
        if not event_task.done():event_task.cancel();await asyncio.gather(event_task,return_exceptions=True)
    assert frame_queries>=2 and event_applied.is_set()
    assert browser.setup_binding.blank_loader_id=='settled-blank-loader'
    assert browser.setup_binding.blank_document_generation==2
    assert bound_tab_observable(browser.guard,browser.setup_binding) and not browser.page.actions


@pytest.mark.parametrize('failure,code',[
    ('context','NEW_TARGET_CONTEXT_OR_DOCUMENT_MISMATCH'),('ambiguous','AMBIGUOUS_NEW_PAGE_TARGET'),
    ('binding','NEW_PAGE_TARGET_BINDING_UNPROVEN'),('source_change','SOURCE_DOCUMENT_CHANGED_DURING_TAB_CREATION'),
    ('new_document_change','NEW_PAGE_CHANGED_BEFORE_ADMISSION')])
async def test_unproven_tab_identity_or_lifecycle_never_navigates(failure,code):
    browser,tools=setup_tools();send=browser.guard.send;context=browser.browser.context
    async def changed(method,params=None,session=None):
        data=await send(method,params,session)
        if method=='Target.getTargets' and len(context.items)>1:
            if failure=='context':data['targetInfos'][-1]['browserContextId']='different-context'
            if failure=='ambiguous':data['targetInfos'].append({'type':'page','targetId':'other-new','url':'about:blank','browserContextId':'same-context'})
        if method=='Runtime.evaluate' and session=='setup-session':
            if failure=='binding':return {'result':{'value':False}}
            if failure=='new_document_change':context.items[-1].owner.document_generation+=1
        return data
    browser.guard.send=changed
    if failure=='source_change':
        create=context.new_page
        async def changed_source(url):
            page=await create(url);context.items[0].owner.document_generation+=1;return page
        context.new_page=changed_source
    with pytest.raises(SetupStop) as error:await browser.open_setup_tab(tools.grant)
    assert error.value.code==code and browser.guard.failed
    assert not any(p.actions for p in context.items)
    assert not any(p.marker for p in context.items)


async def test_new_target_generation_change_cannot_be_implicitly_adopted():
    browser,tools=setup_tools();await browser.open_setup_tab(tools.grant)
    guard=browser.guard;replacement=guard.register_attachment({'method':'Target.attachedToTarget','params':{
        'sessionId':'replacement-session','targetInfo':{'targetId':'setup-target','type':'page'}}})
    replacement.protected=replacement.intercepting=True
    assert replacement.generation==2 and not bound_tab_observable(guard,browser.setup_binding)
    with pytest.raises(SetupStop):await browser.navigate(SETUP_URL)
    assert guard.failed and not browser.page.actions


async def test_explicit_setup_root_retains_correct_child_frame_generation_and_retirement():
    browser,tools=setup_tools();await browser.open_setup_tab(tools.grant);guard=browser.guard
    child=guard.register_attachment({'method':'Target.attachedToTarget','sessionId':'setup-session','params':{
        'sessionId':'child-session','targetInfo':{'targetId':'child-target','type':'iframe'}}})
    child.frame_id='child-frame';child.protected=child.intercepting=True
    assert child.parent_verified and child.root_session=='setup-session' and child.primary_document_generation==1
    setup=guard.session_states['setup-session'];setup.document_generation=2;setup.loader_id='new-setup-loader'
    original=guard.send
    async def tree(method,params=None,session=None):
        if method=='Page.getFrameTree':
            assert session=='setup-session'
            return {'frameTree':{'frame':{'id':'setup-frame','loaderId':'new-setup-loader'}}}
        return await original(method,params,session)
    guard.send=tree
    guard.mark_detached(child.session);await child.settling
    assert child.frame_retired and child.completed and not guard.failed
    assert guard.primary_session=='source-session'


async def test_new_tab_authentication_redirect_stops_without_resubmitting():
    browser,tools=setup_tools();context=browser.browser.context;create=context.new_page
    async def redirected_page(url):
        page=await create(url)
        async def redirect(url):page.location=browser.config.login_url;page.actions.append(('navigate',url))
        page.goto=redirect;return page
    context.new_page=redirected_page
    with pytest.raises(SetupStop):await tools.open()
    assert browser.login_submissions==1 and not browser.page.values
    assert browser.login_page in context.items and context.creations==1


def test_saved_teacher_failure_was_offered_cleanup_and_new_contract_declares_it():
    from deep_agents.evidence import compile_evidence
    from deep_agents.review_contract import ReviewDecision,EvidenceError
    from modules.whatsapp.review import WhatsAppReviewAdapter
    result=json.loads((ROOT/'outputs'/f'{FAILED}-result.json').read_text(encoding='utf-8'))
    events=json.loads((ROOT/'outputs'/f'{FAILED}.json').read_text(encoding='utf-8'))
    assert 92 in result['teacher_evidence']['offered_event_indices']
    assert events[92]['action_category']=='close' and result['teacher_diagnostic']['validation_code']=='UNCITED_CLEANUP'
    adapter=WhatsAppReviewAdapter(result,events);package=compile_evidence(result,events,adapter)
    assert package.module_evidence['required_citation_groups']=={'LOGIN_RECOGNIZED':[62],'CLEANUP':[92]}
    assert 'EVERY' in package.module_evidence['citation_instruction']
    rejected=ReviewDecision.model_validate(result['teacher_rejected_decision'])
    with pytest.raises(EvidenceError,match='UNCITED_CLEANUP'):adapter.validate(rejected,package)
    # This is a local contract double, never a replacement for the saved real reply.
    reviewed=rejected.model_copy(update={'evidence_events':[62,68,92]})
    adapter.validate(reviewed,package)
    assert set(reviewed.evidence_events)<={i for c in package.evidence for i in c.events}


def test_successful_setup_still_requires_real_cleanup_and_factual_citations():
    from deep_agents.review_contract import ReviewDecision,EvidenceError
    trace,result,adapter,package=review_fixture()
    groups=package.module_evidence['required_citation_groups']
    assert groups['CLEANUP']==[4] and groups['SETUP_FIELDS_VERIFIED']==[3]
    valid=ReviewDecision(outcome='success',decision='no_change',finding='Setup fields verified, remains unsaved.',evidence_events=[0,1,2,3,4],strategy_rule=None,proposed_change='')
    adapter.validate(valid,package)
    with pytest.raises(EvidenceError,match='UNCITED_CLEANUP'):adapter.validate(valid.model_copy(update={'evidence_events':[0,1,2,3]}),package)


def test_original_records_and_accepted_teacher_replays_are_unchanged():
    manifest=json.loads((ROOT/'outputs/whatsapp-handoff-preservation.json').read_text(encoding='utf-8'))
    for name,digest in manifest.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest


async def test_blank_document_drift_before_the_first_navigation_is_not_accepted():
    browser,tools=setup_tools();await browser.open_setup_tab(tools.grant)
    browser.page.owner.document_generation+=1
    with pytest.raises(SetupStop,match='SETUP_BLANK_DOCUMENT_CHANGED'):await browser.navigate(SETUP_URL)
    assert browser.setup_navigation_count==0 and not browser.page.actions
