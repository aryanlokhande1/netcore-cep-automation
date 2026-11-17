"""Focused CDP teardown and document-decision regressions, never live Netcore."""
import asyncio
import json
import os
from dataclasses import replace
import pytest
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.navigation_guard import NavigationGuard
from browser.guard_state import SessionDetached
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server,Handler


def attached(session,target,kind='iframe',parent='main'):
    return {'method':'Target.attachedToTarget','sessionId':parent,'params':{
        'sessionId':session,'targetInfo':{'targetId':target,'type':kind},'waitingForDebugger':True}}


def ready_guard():
    guard=NavigationGuard(OriginPolicy({'http://127.0.0.1:8765'}))
    owner=guard.register_attachment(attached('main','main-target','page',None))
    owner.intercepting=owner.protected=True
    guard.session_frames['main']=('page','root')
    guard.sessions.add(owner.target);guard.page_targets.add(owner.target)
    guard.frame_parents['root']=None;guard.frame_owners['root']='main'
    return guard


def teardown(guard,session,target,destroyed=True):
    guard.target_event({'method':'Target.detachedFromTarget','params':{'sessionId':session,'targetId':target}})
    if destroyed:guard.target_event({'method':'Target.targetDestroyed','params':{'targetId':target}})


async def settled(guard):
    tasks=[o.settling for o in guard.session_states.values() if o.settling]
    if tasks:await asyncio.gather(*tasks)


@pytest.mark.parametrize('point',['pending_resume','before_resume','before_setup'])
async def test_proven_destroyed_child_teardown_does_not_poison_primary(point):
    guard=ready_guard();event=attached('child','child-target')
    guard.register_attachment(event);wire=[]
    class Wire:
        async def send(self,raw):
            request=json.loads(raw);method=request['method'];wire.append(method)
            if (point=='pending_resume' and method=='Runtime.runIfWaitingForDebugger') or (point=='before_resume' and method=='Target.setAutoAttach'):
                if point=='before_resume':guard.pending[request['id']].set_result({})
                teardown(guard,'child','child-target')
            else:guard.pending[request['id']].set_result({})
    guard.ws=Wire()
    if point=='before_setup':teardown(guard,'child','child-target')
    await asyncio.wait_for(guard._event(event),1)
    await settled(guard)
    assert not guard.failed and guard.primary_observable()
    assert guard.session_states['child'].completed
    assert not guard.pending and not guard.paused_requests
    if point!='pending_resume':assert 'Runtime.runIfWaitingForDebugger' not in wire
    assert any(e['state']=='CHILD_TARGET_DETACHED' for e in guard.lifecycle)
    await guard.ensure_observable()


@pytest.mark.parametrize('condition',['primary','unknown_parent','not_destroyed','unresolved_parent_document','live_descendant'])
async def test_uncertain_teardown_remains_fail_closed(condition):
    guard=ready_guard()
    if condition=='primary':teardown(guard,'main','main-target')
    else:
        event=attached('child','child-target',parent='unknown' if condition=='unknown_parent' else 'main')
        guard.register_attachment(event)
        if condition=='live_descendant':guard.register_attachment(attached('grandchild','nested-target',parent='child'))
        if condition=='unresolved_parent_document':
            guard.register_request({'method':'Fetch.requestPaused','sessionId':'main','params':{
                'requestId':'held','frameId':'root','resourceType':'Document'}})
        teardown(guard,'child','child-target',condition!='not_destroyed')
    await settled(guard)
    assert guard.failed and guard.failure_diagnostic['code']=='PRIMARY_TARGET_UNAVAILABLE'
    with pytest.raises(PermissionError):await guard.ensure_observable()


async def test_destroyed_child_owns_no_leftover_paused_document():
    guard=ready_guard();guard.register_attachment(attached('child','child-target'))
    guard.session_frames['child']=('iframe',None)
    guard.register_request({'method':'Fetch.requestPaused','sessionId':'child','params':{
        'requestId':'private-request','frameId':'private-frame','resourceType':'Document'}})
    teardown(guard,'child','child-target');await settled(guard)
    assert not guard.failed and not guard.paused_requests
    assert any(e.get('completion')=='TARGET_DESTROYED' for e in guard.lifecycle)


async def test_live_child_protocol_failure_is_not_waived():
    guard=ready_guard()
    async def broken(*a,**k):raise RuntimeError('SYNTHETIC-PRIVATE-ERROR')
    guard.send=broken
    await guard._event(attached('child','child-target'))
    assert guard.failed and guard.failure_diagnostic['code']=='PROTOCOL_COMMAND_FAILED'
    assert 'PRIVATE' not in json.dumps(guard.lifecycle)


async def test_old_session_generation_cannot_resume_replacement_target():
    guard=ready_guard()
    guard.register_attachment(attached('old','shared-target'))
    guard.register_attachment(attached('new','shared-target'))
    assert guard.session_states['new'].generation==2
    with pytest.raises(SessionDetached):await guard.send('Runtime.runIfWaitingForDebugger',session='old')
    assert not guard.pending


async def test_late_request_on_destroyed_child_is_retired_without_dispatch():
    guard=ready_guard();guard.register_attachment(attached('child','child-target'))
    teardown(guard,'child','child-target');await settled(guard)
    await guard._event({'method':'Fetch.requestPaused','sessionId':'child','params':{
        'requestId':'late-private','resourceType':'Document','request':{'url':'http://127.0.0.1:8765/'}}})
    assert not guard.failed and not guard.paused_requests and not guard.pending


async def test_shutdown_does_not_latch_a_late_teardown_failure():
    guard=ready_guard();guard.register_attachment(attached('child','child-target'))
    teardown(guard,'child','child-target');guard.closing=True
    await settled(guard)
    assert not guard.failed and not guard.primary_observable()


def test_removed_frame_ancestry_is_not_reused():
    guard=ready_guard();guard.frame_parents['child-frame']='root'
    assert guard.document_scope('main','child-frame')=='subframe'
    guard.frame_event({'method':'Page.frameDetached','sessionId':'main','params':{'frameId':'child-frame','reason':'remove'}})
    assert guard.document_scope('main','child-frame')=='unknown'


@pytest.mark.parametrize('where,permitted,scope',[('root',True,'top_level'),('child-frame',True,'subframe'),
    ('root',False,'top_level'),('child-frame',False,'subframe'),('unproven',False,'unknown')])
async def test_request_decisions_preserve_policy_and_verified_scope(where,permitted,scope):
    guard=ready_guard();guard.frame_parents['child-frame']='root';commands=[]
    async def send(method,params=None,session=None):commands.append(method);return {}
    guard.send=send
    url=('http://127.0.0.1:8765' if permitted else 'https://denied.example')+'/path?token=SYNTHETIC-PRIVATE-TOKEN'
    await guard._event({'method':'Fetch.requestPaused','sessionId':'main','params':{
        'requestId':'SYNTHETIC-PRIVATE-REQUEST','frameId':where,'resourceType':'Document','request':{'url':url}}})
    assert commands==['Fetch.continueRequest' if permitted else 'Fetch.failRequest']
    record=next(e for e in guard.lifecycle if e['state']=='REQUEST_DECISION')
    assert record['scope']==scope and record['attached'] and record['generation']==1
    assert record['primary_observable'] and record['policy']==('ALLOW' if permitted else 'DENY')
    assert not guard.paused_requests and 'PRIVATE' not in json.dumps(guard.lifecycle)
    assert all('elapsed_ms' in e for e in guard.lifecycle)


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Explicit local Chromium only')
async def test_post_submit_redirect_with_actual_ephemeral_iframes_and_client_block(monkeypatch):
    original=Handler.do_GET
    def serve(self):
        if self.path in {'/child-document','/lifecycle'}:
            self.send_response(200);self.send_header('Content-Type','text/html')
            # This test-only document permits its explicitly configured local child.
            self.end_headers();self.wfile.write(b'<h1>Local lifecycle fixture</h1>');return
        if self.path=='/post-submit-redirect':return self.redirect('/start?scenario=already')
        if self.path=='/redirect-unauthenticated':return self.redirect('/login-again')
        if self.path=='/login-again':return self.respond(self.form('valid'))
        return original(self)
    monkeypatch.setattr(Handler,'do_GET',serve)
    with fixture_server() as server,fixture_server() as child_server:
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH']),OriginPolicy({server.base}),Trace(),fixture=True)
        try:
            await browser.start()
            from deep_agents.browser_tools import BrowserTools
            tools=BrowserTools(browser,server.base+'/start?scenario=valid')
            assert (await tools.navigate(tools.entry_url))['ok']
            state=await tools.inspect()
            submit=next(e['reference'] for e in state['elements'] if e['kind']=='submit')
            assert (await tools.interact(submit,'submit_fixture'))['ok']
            assert (await browser.observe()).readonly_access
            await browser.navigate(server.base+'/lifecycle')
            from urllib.parse import urlsplit
            child_origin='http://localhost:'+str(urlsplit(child_server.base).port)
            # Only this local guard test approves a second explicit loopback origin
            # to force OOPIF creation. No production/Netcore origin is changed.
            browser.guard.policy=OriginPolicy({server.base,child_origin})
            original_send=browser.guard.send
            removed=[]
            async def remove_before_resume(method,params=None,session=None):
                owner=browser.guard.session_states.get(session)
                if method=='Runtime.runIfWaitingForDebugger' and owner and owner.kind=='iframe' and not removed:
                    removed.append(owner)
                    await asyncio.wait_for(browser.page.evaluate("document.querySelector('iframe').remove();true"),5)
                    for _ in range(100):
                        if owner.destroyed:break
                        await asyncio.sleep(.01)
                return await original_send(method,params,session)
            browser.guard.send=remove_before_resume
            await browser.page.evaluate("(()=>{const f=document.createElement('iframe');f.src='"+child_origin+"/child-document';document.body.append(f);return true})()")
            for _ in range(150):
                if browser.guard.failed or (removed and removed[0].completed):break
                await asyncio.sleep(.01)
            assert removed and removed[0].parent_verified,json.dumps(browser.guard.lifecycle)
            assert removed[0].completed and not browser.guard.failed,json.dumps(browser.guard.lifecycle)
            assert not browser.guard.paused_requests
            await browser.navigate(server.base+'/post-submit-redirect')
            assert (await browser.observe()).readonly_access
            assert browser.guard.primary_observable()
            await browser.navigate(server.base+'/redirect-unauthenticated')
            unauthenticated=await browser.observe()
            from modules.login.verifier import verify
            from app.state import Result
            assert unauthenticated.credential_form
            assert verify(unauthenticated)!=Result.AUTHENTICATED_AND_VERIFIED
            # A third local origin is deliberately NOT approved. Chromium must show
            # the client block, with an explicit guard failRequest completion.
            denied='http://127.0.0.1:'+str(urlsplit(child_server.base).port)+'/child-document'
            try:await asyncio.wait_for(browser.page.goto(denied),8)
            except Exception:pass
            for _ in range(40):
                if any(e.get('completion')=='CLIENT_BLOCKED' for e in browser.guard.lifecycle):break
                await asyncio.sleep(.05)
            decisions=[e for e in browser.guard.lifecycle if e['state']=='REQUEST_DECISION']
            assert any(e['scope']=='top_level' and e['policy']=='ALLOW' for e in decisions)
            assert any(e['scope']=='top_level' and e['policy']=='DENY' and e['command']=='Fetch.failRequest' for e in decisions)
            client_error=await asyncio.wait_for(browser.page.evaluate("document.body.innerText.includes('ERR_BLOCKED_BY_CLIENT')"),3)
            assert client_error
            assert not (await browser.observe()).allowed
            browser.guard.record('LOCAL_CLIENT_BLOCK_CONFIRMED',chromium_error_code_matched=bool(client_error))
            assert not browser.guard.paused_requests
            (ROOT/'outputs'/'post-login-local-decisions.json').write_text(json.dumps(browser.guard.lifecycle,indent=2))
        finally:await browser.close()
        assert browser.trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'


@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Explicit local Chromium only')
async def test_real_primary_target_loss_closes_owned_browser():
    with fixture_server() as server:
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH']),OriginPolicy({server.base}),Trace(),fixture=True)
        try:
            await browser.start();await browser.navigate(server.base+'/start?scenario=already')
            primary=browser.guard.session_states[browser.guard.primary_session]
            await browser.guard.send('Target.closeTarget',{'targetId':primary.target})
            for _ in range(50):
                if browser.guard.failed:break
                await asyncio.sleep(.01)
            assert browser.guard.failed
            assert browser.guard.failure_diagnostic['code']=='PRIMARY_TARGET_UNAVAILABLE'
            with pytest.raises(PermissionError):await browser.observe()
        finally:await browser.close()
        assert browser.trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
