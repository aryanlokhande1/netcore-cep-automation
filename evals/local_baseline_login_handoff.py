"""One headed loopback Login Doer -> model-free baseline session, no providers.

Authentication here is the existing explicitly synthetic fixture protocol. No real
credential object, private browser state, cookie value or session token is read.
Only server-side authenticated access and fixed booleans prove continuity.
"""
from contextlib import contextmanager
from http.server import ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic
from unittest.mock import patch
from urllib.parse import urlsplit

from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.protected_tabs import bound_tab_observable
from deep_agents.browser_tools import BrowserTools
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from evals.fixture import Handler as LoginHandler
from evals.local_browser_baseline import fixture_html,reference,facts


class Handler(LoginHandler):
    def do_GET(self):
        if urlsplit(self.path).path=='/regular':
            session=self.session()
            if not session or not session['authenticated']:
                self.server.denied_setup_requests+=1
                return self.respond('<h1>Session expired</h1>',401)
            self.server.authenticated_setup_requests+=1
            body=fixture_html('valid')+'''<script>
              fetch('/context').then(r=>r.ok?r.json():null).then(data=>{
                window.__localHandoffAuthenticated=data?.authenticated===true&&data?.tenant==='synthetic-tenant';
              });</script>'''
            return self.respond(body)
        return super().do_GET()

    def do_POST(self):
        if self.path=='/login':self.server.login_posts+=1
        return super().do_POST()


@contextmanager
def handoff_server():
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.daemon_threads=True;server.sessions={}
    server.base='http://127.0.0.1:'+str(server.server_port)
    server.login_posts=server.authenticated_setup_requests=server.denied_setup_requests=0
    worker=Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True);worker.start()
    try:yield server
    finally:server.shutdown();server.server_close();worker.join(2)


async def accept_baseline_login_handoff(executable,*,output=None):
    import app.whatsapp_baseline as module
    report={'scope':'LOCAL_LOGIN_DOER_BASELINE_HANDOFF','live_netcore':False,'paid_inference':0,
        'real_credential_access':False,'headless':False,'passed':False}
    trace=Trace();started=monotonic();browser=None
    with handoff_server() as server:
        config=Config(str(executable),headless=False,login_url=server.base+'/start?scenario=valid',
            expected_host='127.0.0.1',timeout_seconds=120,max_actions=250)
        browser=module.BaselineBrowser(config,OriginPolicy({server.base}),trace,fixture=True)
        tools=BrowserTools(browser,config.login_url)
        model=SyntheticChatModel();budget=InferenceBudget(8,role_limits={'doer':8,'teacher':0})
        starts=[];closes=[];checkpoint_calls=[]
        original_start=browser.start;original_close=browser.close
        async def counted_start():
            starts.append(True)
            await original_start()
        async def counted_close():
            closes.append(True)
            return await original_close()
        browser.start=counted_start;browser.close=counted_close
        try:
            login=await module.automated_login(browser,tools,model,budget,offline=True,login_seconds=45)
            source=browser.page;context=browser.browser.context;process=browser.process
            login_calls=model.calls
            report['login']={'actual_deep_agents_requests':login_calls,'single_submission':browser.login_submissions==1,
                'single_server_login_post':server.login_posts==1,'started_once':len(starts)==1,
                'not_closed_after_doer':not closes and not browser.close_started,
                'completed_submission_fact':any(f['kind']=='SUBMITTED_ONCE' for f in trace.step_facts)}
            try:await module.prepare_setup(browser,destination=server.base+'/regular')
            except Exception as error:checkpoint_required=getattr(error,'code',None)=='LOGIN_CHECKPOINT_REQUIRED'
            else:checkpoint_required=False
            report['login']['setup_refused_before_c']=checkpoint_required and len(await context.pages())==1
            async def confirm(prompt,allowed,seconds,actual):
                if actual is not browser or allowed!={'c'}:raise AssertionError('LOCAL_CHECKPOINT_CONTRACT_CHANGED')
                checkpoint_calls.append(True)
                if len(await context.pages())!=1:raise AssertionError('SETUP_TAB_OPENED_BEFORE_C')
                return 'c'
            with patch.object(module,'key',confirm):
                await module.confirm_login(browser,login,automated=True,seconds=10)
            executor=await module.prepare_setup(browser,destination=server.base+'/regular')
            await browser.page.wait_for_function('window.__localHandoffAuthenticated===true',timeout=5000)
            authenticated_in_new_tab=await browser.page.evaluate('window.__localHandoffAuthenticated===true') is True
            discovery=await executor.discover()
            campaign=await executor.fill_campaign(reference(discovery,'campaign_name'),authorized=True)
            state=await facts(browser.page)
            try:browser.claim_login_submission()
            except PermissionError:login_retry_refused=True
            else:login_retry_refused=False
            report['handoff']={'human_c_once':len(checkpoint_calls)==1,'same_browser_object':executor.browser is browser,
                'same_context':browser.browser.context is context,'same_owned_process':browser.process is process,
                'original_tab_retained':any(p.page_id==source.page_id for p in await context.pages()) and await source.evaluate('document.documentElement!==null') is True,
                'exactly_two_tabs':len(await context.pages())==2,
                'protected_setup_tab':bound_tab_observable(browser.guard,browser.setup_binding),
                'authenticated_context_reused':authenticated_in_new_tab and server.authenticated_setup_requests==1 and server.denied_setup_requests==0,
                'no_relaunch':len(starts)==1,'no_premature_cleanup':not closes and not browser.close_started,
                'login_closed_before_setup':login_retry_refused and browser.login_submissions==1 and server.login_posts==1,
                'campaign_readback_verified':campaign.get('status')=='CAMPAIGN_NAME_VERIFIED' and state['campaign_matches'],
                'baseline_model_free':model.calls==login_calls,'teacher_not_invoked':budget.role_requests['teacher']==0 and trace.teacher_result=='NOT_RUN',
                'no_explicit_commit':state['counts']['save']==0,'no_paid_api_requests':trace.openai_api_calls==0}
            report['passed']=all(report['login'][k] for k in report['login'] if k!='actual_deep_agents_requests') and all(report['handoff'].values())
        except Exception as error:
            from modules.whatsapp.browser_baseline import sanitized_action_error
            report['error']=sanitized_action_error(error,'LOCAL_HANDOFF_ACCEPTANCE')
        finally:
            try:await browser.close()
            except Exception:report['cleanup_exception']=True
            report['cleanup_result']=trace.cleanup_result;report['close_calls']=len(closes)
            report['passed']=bool(report['passed'] and len(closes)==1 and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED')
            report['seconds']=round(monotonic()-started,3)
            Path(output or ROOT/'outputs/real-local-baseline-login-handoff.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
