"""Actual Stagehand/Chromium loopback acceptance; no credentials or inference."""
import asyncio
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from threading import Thread
from time import monotonic, sleep

from app.config import Config, OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient


async def local_guard_race(executable, *, headless=True, iterations=3):
    hits={'allowed':0,'denied':0}
    def handler(kind):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*a):pass
            def do_GET(self):
                hits[kind]+=1
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/done');self.end_headers();return
                if self.path=='/slow':sleep(.3)
                data=b'<html><body><main><h1>Local guard fixture</h1><button>fixture</button></main></body></html>'
                self.send_response(200);self.send_header('Content-Type','text/html');self.send_header('Content-Length',str(len(data)));self.end_headers()
                try:self.wfile.write(data)
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        return Handler
    allowed=ThreadingHTTPServer(('127.0.0.1',0),handler('allowed'))
    denied=ThreadingHTTPServer(('127.0.0.1',0),handler('denied'))
    for server in (allowed,denied):Thread(target=server.serve_forever,daemon=True).start()
    origin='http://127.0.0.1:'+str(allowed.server_port)
    blocked='http://127.0.0.1:'+str(denied.server_port)
    trace=Trace();trace.reasoner_mode='REAL_LOCAL_CHROMIUM_GUARD_NO_MODEL'
    browser=StagehandClient(Config(str(executable),headless=headless,login_url=origin+'/',
        expected_host='127.0.0.1',max_actions=300,timeout_seconds=120),OriginPolicy({origin}),trace,fixture=True)
    report={'scope':'REAL_LOOPBACK_CHROMIUM_ONLY','headless':headless,'iterations':iterations,
            'model_requests':0,'credential_submissions':0,'netcore_requests':0,'cases':[]}
    release=asyncio.Event();release.set()
    try:
        await browser.start();guard=browser.guard;original_send=guard.send
        await original_send('Network.enable',session=guard.primary_session)
        await browser.navigate(origin+'/base')
        # Normal redirects, protected child targets and blocked subframe documents.
        await browser.navigate(origin+'/redirect')
        await browser.page.evaluate('(u=>{const f=document.createElement("iframe");f.src=u;document.body.append(f);return true})('+json.dumps(blocked+'/denied')+')')
        await asyncio.sleep(.3)
        report['cases'].append({'case':'normal_denied_subframe','healthy':not guard.failed})
        for index in range(iterations):
            if guard.failed:break
            await browser.navigate(origin+'/base')
            paused=asyncio.Event();release=asyncio.Event()
            async def delayed(method,params=None,session=None):
                if method=='Fetch.failRequest' and not guard.failed:
                    paused.set();await release.wait()
                return await original_send(method,params,session)
            guard.send=delayed
            await browser.page.evaluate('(u=>{for(let i=0;i<2;i++){const f=document.createElement("iframe");f.src=u+"/race-"+i;document.body.append(f)}return true})('+json.dumps(blocked)+')')
            await asyncio.wait_for(paused.wait(),3)
            # Delay an actual command while Chromium cancels old child requests
            # during a genuine parent navigation. No protocol response is mocked.
            await browser.page.evaluate('(u=>{location.href=u;return true})('+json.dumps(origin+'/done')+')')
            deadline=monotonic()+3
            while monotonic()<deadline and await browser.page.url()!=origin+'/done':await asyncio.sleep(.02)
            await asyncio.sleep(.15);release.set();await asyncio.sleep(.3)
            report['cases'].append({'case':'cancelled_child_during_parent_navigation','iteration':index+1,
                'healthy':not guard.failed,'parent_destination_observed':await browser.page.url()==origin+'/done'})
            guard.send=original_send
        if not guard.failed:
            # Actual allowed child completion, cancellation, and frame removal.
            await browser.page.evaluate('(u=>{const f=document.createElement("iframe");f.id="local-complete";f.src=u+"/done";document.body.append(f);return true})('+json.dumps(origin)+')')
            await asyncio.sleep(.25)
            await browser.page.evaluate('(u=>{const f=document.createElement("iframe");f.src=u+"/slow";document.body.append(f);setTimeout(()=>f.remove(),10);return true})('+json.dumps(origin)+')')
            await asyncio.sleep(.4)
            finished=any(e.get('state')=='DOCUMENT_REQUEST_TERMINAL' and e.get('completion')=='FINISHED' for e in guard.lifecycle)
            detached=any(e.get('state')=='FRAME_DETACHED' for e in guard.lifecycle)
            report['cases'].append({'case':'allowed_child_completion_and_detachment','healthy':not guard.failed and finished and detached,
                'finished_document_observed':finished,'frame_detach_observed':detached})
            # A denied top-level document must be refused before server dispatch.
            try:await browser.page.goto(blocked+'/top-denied')
            except Exception:pass
            await asyncio.sleep(.15)
            protected_view_blocked=bool(guard.blocking_hosts)
            denied_top=any(e.get('state')=='REQUEST_DECISION' and e.get('scope')=='top_level' and e.get('policy')=='DENY' for e in guard.lifecycle)
            report['cases'].append({'case':'denied_top_level','healthy':denied_top and protected_view_blocked and hits['denied']==0})
            # Real guard-transport loss stays terminal; no protocol response double.
            await guard.ws.close();await asyncio.sleep(.1)
            report['cases'].append({'case':'genuine_transport_loss_remains_blocking','healthy':guard.failed and not guard.primary_observable()})
        report['passed']=len(report['cases'])==iterations+4 and all(c['healthy'] for c in report['cases']) and hits['denied']==0
        report['expected_transport_failure_injected']=report['passed']
        report['guard_failure']=guard.failure_diagnostic
    except Exception as error:
        report.update(passed=False,exception_class=type(error).__name__)
    finally:
        release.set()
        try:await browser.close()
        except BaseException as error:report['cleanup_exception_class']=type(error).__name__
        allowed.shutdown();denied.shutdown();allowed.server_close();denied.server_close()
        report.update(hits=hits,cleanup=trace.cleanup_result,cleanup_diagnostic=trace.cleanup_diagnostic,
            profile_absent=not browser.profile.exists(),guard_events=list(browser.guard.lifecycle) if browser.guard else [])
    return report
