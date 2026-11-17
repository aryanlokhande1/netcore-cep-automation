"""Sanitized origin-failure regressions; no private configuration or real services."""
import json
import os
from types import SimpleNamespace
import pytest
from app.config import Config,OriginPolicy
from app.trace import Trace
from app.state import Result
from browser.navigation_guard import NavigationGuard
from browser.stagehand_client import StagehandClient
from browser.live_dom import LIVE_DOM,VALIDATE_MAPPING
from deep_agents.browser_tools import BrowserTools
from learning.policies import Strategy
from evals.fixture import Handler,fixture_server

@pytest.mark.parametrize('context,frame,scope',[
    (('page','root'),'root','top_level'),(('page','root'),'child','subframe'),
    (('iframe',None),'child','subframe'),(None,'child','unknown'),
    (('page',None),'child','unknown'),(('page','root'),None,'unknown')])
async def test_every_unapproved_document_is_denied_with_private_free_scope(context,frame,scope):
    guard=NavigationGuard(OriginPolicy({'https://approved.example'}))
    if context is not None:
        guard.session_frames['session']=context
        if context[0]=='page':guard.frame_parents['child']=context[1]
        if context[0]=='iframe':
            from browser.guard_state import GuardSession
            guard.session_states['session']=GuardSession('session','target','iframe',1,parent_verified=True)
    commands=[]
    async def send(method,params=None,session=None):
        commands.append((method,params))
    guard.send=send
    await guard._event({'method':'Fetch.requestPaused','sessionId':'session','params':{
        'requestId':'request','frameId':frame,
        'request':{'url':'https://denied.example/private?token=SYNTHETIC-PRIVATE-MARKER'}}})
    assert commands==[('Fetch.failRequest',{'requestId':'request','errorReason':'BlockedByClient'})]
    assert guard.blocked_hosts=={'denied.example'}
    assert guard.blocked_documents==[{'host':'denied.example','scope':scope}]
    assert guard.blocking_hosts==(set() if scope=='subframe' else {'denied.example'})
    assert 'SYNTHETIC-PRIVATE-MARKER' not in json.dumps(guard.blocked_documents)

def test_unclassified_and_top_level_blocks_remain_fatal_even_for_same_child_host():
    guard=NavigationGuard(OriginPolicy(set()))
    guard.blocked_hosts={'denied.example'}
    assert guard.blocking_hosts=={'denied.example'}
    guard.blocked_documents=[{'host':'denied.example','scope':'subframe'},{'host':'denied.example','scope':'top_level'}]
    assert guard.blocking_hosts=={'denied.example'}

def test_tool_trace_contains_canonical_names_only():
    trace=Trace()
    trace.tool('submit_login','doer','requested')
    trace.tool('SYNTHETIC-PRIVATE-MARKER','doer','denied')
    assert trace.events[0]['tool_name']=='submit_login'
    assert trace.events[1]['tool_name']=='FORBIDDEN'
    assert 'SYNTHETIC-PRIVATE-MARKER' not in json.dumps(trace.events)
    assert not any('args' in e or 'content' in e for e in trace.events)

async def test_blocked_hold_is_inspection_only_and_explains_stop(monkeypatch,capsys):
    from deep_agents.controller import _execute_doer
    from deep_agents.permissions import InferenceBudget
    from app.state import Observation
    trace=Trace()
    guard=NavigationGuard(OriginPolicy(set()))
    guard.blocked_hosts={'denied.example'}
    class Browser:
        config=SimpleNamespace(timeout_seconds=5)
        fixture=True
        page=object()
        login_submissions=0
        verification_details=None
        async def start(self):pass
        async def observe(self):return Observation(allowed=False)
        async def close(self):trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED'
    browser=Browser();browser.trace=trace;browser.guard=guard
    tools=SimpleNamespace(entry_url='http://127.0.0.1:1234/start',navigation_count=1,terminal_error=True)
    class Agent:
        async def ainvoke(self,*args,**kwargs):
            from deep_agents.schemas import DoerReport
            return {'structured_response':DoerReport(status='blocked',reason='unrecognized_page')}
    monkeypatch.setattr('deep_agents.controller.create_doer',lambda *a,**k:(Agent(),None))
    holds=[]
    async def hold(seconds):holds.append(seconds)
    monkeypatch.setattr('browser.inspection.inspection_hold',hold)
    result=await _execute_doer(browser,tools,'test',None,InferenceBudget(),True,hold_seconds=1)
    assert holds==[1] and result['verification']==Result.UNEXPECTED_PAGE
    output=capsys.readouterr().out
    assert 'UNAPPROVED_ORIGIN_APPROVAL_REQUIRED' in output and 'login submissions: 0' in output
    assert 'Inspection does not resume login.' in output

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Local-only headed Chromium fixture')
async def test_blocked_child_does_not_poison_approved_form_observation_or_verification():
    # Two exact loopback origins; the denied origin must receive no requests at all.
    with fixture_server() as approved,fixture_server() as denied:
        class EmbeddedHandler(Handler):
            def do_GET(self):
                if self.path!='/embedded-test':
                    return super().do_GET()
                self.send_response(200)
                self.send_header('Content-Type','text/html; charset=utf-8')
                self.send_header('Content-Security-Policy',f"default-src 'self'; frame-src {denied.base}; form-action 'self'")
                self.end_headers()
                body=self.form().replace('Continue securely','LOGIN')+f'<iframe src="{denied.base}/start?scenario=already"></iframe>'
                self.wfile.write(body.encode())
        approved.RequestHandlerClass=EmbeddedHandler
        config=Config(os.environ['CHROMIUM_EXECUTABLE_PATH'])
        browser=StagehandClient(config,OriginPolicy({approved.base}),Trace(),fixture=True)
        tools=BrowserTools(browser,approved.base+'/start?scenario=valid',strategy=Strategy(rules=('prefer_semantic_roles',)))
        try:
            await browser.start()
            assert (await tools.navigate(tools.entry_url))['ok']
            await browser.navigate(approved.base+'/embedded-test')
            observation=await browser.observe()
            assert browser.guard.blocked_documents==[{'host':'127.0.0.1','scope':'subframe'}]
            assert not browser.guard.blocking_hosts and observation.allowed and observation.credential_form
            assert not denied.sessions
            # Exercise the exact live DOM discovery/validation code against synthetic fields.
            elements=await browser.page.evaluate(LIVE_DOM.replace('PREFIX',json.dumps('local-regression')))
            mapping={e['key']:e for e in elements}
            assert set(mapping)=={'account','username','password','submit'}
            assert await browser.page.evaluate(VALIDATE_MAPPING.replace('MAPPING',json.dumps(mapping)))
            state=await tools.inspect()
            assert state['ok']
            ref=next(e['reference'] for e in state['elements'] if e['kind']=='submit')
            assert (await tools.interact(ref,'submit_fixture'))['ok']
            assert browser.login_submissions==1 and not denied.sessions
            assert (await tools.verify())['verification']==Result.AUTHENTICATED_AND_VERIFIED
        finally:
            await browser.close()
