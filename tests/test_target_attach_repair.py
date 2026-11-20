"""Target lifecycle regression: strictly local Chromium, no credentials/model calls."""
import asyncio
import json
import os
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4
import pytest
from app.config import Config,OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server
from browser.navigation_guard import NavigationGuard,auto_attach_settings

def attachment(kind='page'):
    return {'method':'Target.attachedToTarget','params':{'sessionId':'PRIVATE-SESSION',
        'targetInfo':{'targetId':'PRIVATE-TARGET','type':kind,'url':'PRIVATE-URL'},'waitingForDebugger':True}}

@pytest.mark.parametrize('kind',['page','iframe'])
async def test_interception_is_armed_before_resume_and_renderer_query(kind):
    guard=NavigationGuard(OriginPolicy(set()));commands=[]
    async def send(method,params=None,session=None):
        commands.append(method)
        if method=='Target.setAutoAttach':assert params==auto_attach_settings()
        return {'frameTree':{'frame':{'id':'PRIVATE-FRAME'}}} if method=='Page.getFrameTree' else {}
    guard.send=send
    await guard._event(attachment(kind))
    assert not guard.failed
    assert commands[:4]==['Fetch.enable','Network.enable','Target.setAutoAttach','Runtime.runIfWaitingForDebugger']
    assert commands[4:]==['Page.enable','Page.getFrameTree']
    assert 'PRIVATE' not in json.dumps(guard.lifecycle)

async def test_detached_session_fails_immediately_with_exact_pending_command():
    guard=NavigationGuard(OriginPolicy(set()))
    class Wire:
        async def send(self,raw):
            # Destroy during the first setup command, without waiting ten seconds.
            guard.target_event({'method':'Target.detachedFromTarget','params':{'sessionId':'PRIVATE-SESSION'}})
    guard.ws=Wire()
    await asyncio.wait_for(guard._event(attachment()),1)
    assert guard.failed and not guard.sessions
    assert guard.failure_diagnostic['command']=='Fetch.enable'
    assert guard.failure_diagnostic['detached'] is True
    first=dict(guard.failure_diagnostic)
    guard.fail_closed('READER_END')
    assert guard.failure_diagnostic==first
    assert 'PRIVATE' not in json.dumps(guard.lifecycle)+json.dumps(first)

async def test_missing_response_records_exact_command_and_duration(monkeypatch):
    import browser.navigation_guard as module
    guard=NavigationGuard(OriginPolicy(set()))
    class Wire:
        async def send(self,raw):pass
    guard.ws=Wire()
    real_wait=asyncio.wait_for
    async def short_wait(future,timeout):return await real_wait(future,.01)
    monkeypatch.setattr(module.asyncio,'wait_for',short_wait)
    await guard._event(attachment())
    assert guard.failure_diagnostic['command']=='Fetch.enable'
    assert guard.failure_diagnostic['category']=='TIMEOUT'
    assert not guard.pending
    assert any(e['state']=='COMMAND_FAILED' and e['duration_ms']>=0 for e in guard.lifecycle)

def test_filter_preserves_page_and_frame_interception_without_pausing_workers():
    filters=auto_attach_settings()['filter']
    for kind,included in [('page',True),('iframe',True),('worker',False),('service_worker',False),('shared_worker',False)]:
        match=next(f for f in filters if 'type' not in f or f['type']==kind)
        assert (not match.get('exclude',False))==included

async def test_failed_guard_never_resumes_target_or_allowed_document():
    guard=NavigationGuard(OriginPolicy({'http://127.0.0.1:1234'}));guard.fail_closed('TEST')
    calls=[]
    async def send(method,*a,**k):calls.append(method);return {}
    guard.send=send
    await guard._event(attachment())
    await guard._event({'method':'Fetch.requestPaused','sessionId':'PRIVATE-SESSION','params':{
        'requestId':'PRIVATE-REQUEST','request':{'url':'http://127.0.0.1:1234/'}}})
    assert calls==['Fetch.failRequest']

async def test_failed_live_no_change_never_starts_fixture_evaluation(tmp_path,monkeypatch,capsys):
    from learning.autonomous import process_learning
    from learning.policies import Strategy
    from learning.skill_storage.registry import SkillStore
    from tests.test_three_run import record
    from app.state import Result
    attempt=record(Strategy());attempt.update(source='live',verification=str(Result.UNEXPECTED_PAGE))
    attempt['step_facts']=[f for f in attempt['step_facts'] if f['kind']!='DESTINATION_VERIFIED']
    attempt['teacher_proposal'].update(outcome='failure',analysis_kind='evidence_gap')
    async def forbidden(*a,**k):raise AssertionError('No fixture browser should start')
    monkeypatch.setattr('learning.autonomous._trial',forbidden)
    learned=await process_learning(Config('unused'),attempt,attempt['_events'],store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert learned['decision']=='NO_CHANGE' and learned['evaluation_status']=='NOT_RUN'
    assert not learned['evaluation_passed'] and learned['candidate_skill']['state']=='DRAFT'
    assert 'EVALUATION SKIPPED' in capsys.readouterr().out

async def test_routine_fixture_evaluation_is_explicitly_headless(monkeypatch):
    from learning.autonomous import _trial
    from learning.three_run import fixture_trial
    from learning.policies import Strategy
    seen=[]
    async def fake(config,**kwargs):
        seen.append((config,kwargs));return {'_events':[]}
    monkeypatch.setattr('deep_agents.controller.run_synthetic',fake)
    cfg=Config('unused')
    await _trial(cfg,'valid',Strategy(),None)
    await fixture_trial(cfg,'valid',Strategy(),None)
    assert not cfg.headless and all(c.headless for c,_ in seen)
    assert all(k.get('_evaluation') and not k.get('learning') for _,k in seen)

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Local Chromium required')
async def test_local_paused_popup_attachment(tmp_path):
    with fixture_server() as server:
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH']),OriginPolicy({server.base}),Trace(),fixture=True)
        try:
            await browser.start()
            await browser.navigate(server.base+'/start?scenario=already')
            # A new auto-attached page is initially waitingForDebugger, unlike the startup page.
            await browser.guard.send('Target.createTarget',{'url':server.base+'/start?scenario=already'})
            for _ in range(120):
                if browser.guard.failed or len(browser.guard.sessions)>=2:break
                await asyncio.sleep(.1)
            safe=browser.guard.lifecycle
            from app.config import ROOT
            (ROOT/'outputs'/'target-attach-local-timing.json').write_text(json.dumps(safe,indent=2))
            assert not browser.guard.failed,json.dumps(safe)
            assert len(browser.guard.sessions)>=2
            from browser.readonly_cdp import ReadOnlyCDP
            monitor=ReadOnlyCDP(lambda e:None,diagnostic=browser.trace.lifecycle)
            browser.discovery=SimpleNamespace(close=monitor.close)
            await monitor.start(browser.browser.cdp_url,browser.guard.page_targets)
            assert len(monitor.active_sessions)>=2
        finally:await browser.close()

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Local Chromium required')
async def test_headless_transition_worker_and_blocked_frame(tmp_path,monkeypatch):
    from evals.fixture import Handler
    original=Handler.do_GET
    def serve(self):
        if self.path=='/worker-page':
            self.send_response(200);self.send_header('Content-Type','text/html')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; worker-src 'self'; frame-src http://localhost:"+str(self.server.server_port))
            self.end_headers();self.wfile.write(b'<h1>Local transition fixture</h1>');return
        if self.path=='/worker.js':
            self.send_response(200);self.send_header('Content-Type','application/javascript');self.end_headers()
            self.wfile.write(b'postMessage("ready");');return
        return original(self)
    monkeypatch.setattr(Handler,'do_GET',serve)
    with fixture_server() as server:
        cfg=replace(Config(os.environ['CHROMIUM_EXECUTABLE_PATH']),headless=True)
        browser=StagehandClient(cfg,OriginPolicy({server.base}),Trace(),fixture=True)
        try:
            await browser.start()
            await browser.navigate(server.base+'/worker-page')
            # Workers must run without the document guard pausing their runtime.
            worker=await asyncio.wait_for(browser.page.evaluate("new Promise(resolve=>{const w=new Worker('/worker.js');const t=setTimeout(()=>{w.terminate();resolve('TIMEOUT')},3000);w.onerror=()=>{clearTimeout(t);resolve('WORKER_ERROR')};w.onmessage=()=>{clearTimeout(t);w.terminate();resolve('READY')}})"),8)
            assert worker=='READY',worker
            for scenario in ('valid','already'):
                await browser.navigate(server.base+'/start?scenario='+scenario)
            await browser.navigate(server.base+'/worker-page')
            from urllib.parse import urlsplit
            port=urlsplit(server.base).port
            await browser.page.evaluate("(()=>{const f=document.createElement('iframe');f.src='http://localhost:"+str(port)+"/start?scenario=already';document.body.append(f);return true})()")
            for _ in range(60):
                if browser.guard.blocked_documents or browser.guard.failed:break
                await asyncio.sleep(.1)
            assert not browser.guard.failed,json.dumps(browser.guard.lifecycle)
            assert any(d['host']=='localhost' for d in browser.guard.blocked_documents)
            assert not any(e.get('kind') in {'worker','service_worker','shared_worker'} for e in browser.guard.lifecycle if e['state']=='TARGET_ATTACHED')
        finally:await browser.close()
        assert browser.trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
