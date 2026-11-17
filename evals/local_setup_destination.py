"""Actual protected Chromium SPA destination regression, loopback only."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic
from uuid import uuid4
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from app.config import ROOT,Config,OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab,bound_tab_observable
from deep_agents.offline import SyntheticChatModel
from modules.whatsapp.agent import create_setup_doer
from modules.whatsapp.contract import Phase1Limits
from modules.whatsapp.destination import validate_setup_destination,DestinationRejected
from modules.whatsapp.session import Phase1Budget
from modules.whatsapp.tools import SetupTools
from evals.local_setup_agent_acceptance import fixture_html,fixture_facts


@contextmanager
def server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/regular':self.send_error(404);return
            body=fixture_html('closed').encode()
            self.send_response(200);self.send_header('Content-Length',str(len(body)))
            self.send_header('Content-Type','text/html; charset=utf-8');self.end_headers();self.wfile.write(body)
        def log_message(self,*args):pass
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    worker=Thread(target=lambda:service.serve_forever(poll_interval=.05),daemon=True);worker.start()
    try:yield 'http://127.0.0.1:'+str(service.server_port)
    finally:service.shutdown();service.server_close();worker.join(2)


class ReadOnlyDoer(SyntheticChatModel):
    def _generate(self,messages,**kwargs):
        self.calls+=1
        if self.calls==1:name,args='observe_page',{}
        elif self.calls==2:
            data=json.loads(next(m.content for m in reversed(messages) if isinstance(m,ToolMessage)))
            name,args='inspect_control',{'reference':next(f['reference'] for f in data['fields'] if f['field']=='campaign_name')}
        else:name,args='SetupReport',{'status':'BLOCKED','reason':'unsupported_control'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{
            'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}]))])


async def accept_destination(executable):
    trace=Trace();started=monotonic();report={'passed':False,'cases':[],'paid_inference':0,'live_login':False}
    browser=None
    with server() as origin:
        expected=origin+'/regular'
        browser=StagehandClient(Config(str(executable),headless=False,login_url='about:blank',expected_host='127.0.0.1',
            timeout_seconds=90,max_actions=100),OriginPolicy({origin}),trace,fixture=True)
        try:
            await browser.start();original=browser.page
            await original.goto('about:blank')
            async def settled(session,url):
                async with asyncio.timeout(5):
                    while True:
                        browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                        owner=browser.guard.session_states[session]
                        frame=(await browser.guard.send('Page.getFrameTree',session=session))['frameTree']['frame']
                        if frame.get('url')==url and frame.get('id')==owner.frame_id and frame.get('loaderId')==owner.loader_id:return
                        await browser.guard.lifecycle_changed.wait()
            await settled(browser.guard.primary_session,'about:blank')
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding;browser.task_phase='setup'
            await page.goto(expected);await page.wait_for_load_state('domcontentloaded',timeout=5000)
            await settled(binding.session,expected)
            validate_setup_destination(await page.url(),expected)
            trace.fact('WHATSAPP_DESTINATION_OPENED')
            before=await fixture_facts(page);evaluate=page.evaluate;transitioned=False
            async def transition(source,*args,**kwargs):
                nonlocal transitioned
                data=await evaluate(source,*args,**kwargs)
                if not transitioned and 'const controls=all.filter' in source:
                    await evaluate("history.replaceState(null,'',location.pathname+'#setup')")
                    transitioned=True
                return data
            page.evaluate=transition
            tools=SetupTools.for_fixture(browser,expected,authorized_mutations=False)
            initial=await tools.open()
            assert initial['ok'] and initial['page_state']=='ALLOWED_ORIGIN' and tools.safety_status is None
            model=ReadOnlyDoer();budget=Phase1Budget(Phase1Limits());budget.phase='setup'
            agent,_=create_setup_doer(model,tools,trace,budget,offline=True)
            tools.milestone('SETUP_DOER_STARTED')
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                state=await agent.ainvoke({'messages':[{'role':'user','content':'Observe the current Setup form read-only; do not request writes.'}]},
                    {'configurable':{'thread_id':trace.run_id},'recursion_limit':30})
            after=await fixture_facts(page)
            states={e.get('diagnostic',{}).get('state') for e in trace.events}
            valid=transitioned and (await page.url())==expected+'#setup' and model.calls==3 and budget.requests==3
            valid &= {'SETUP_PAGE_OBSERVED','SETUP_DOER_STARTED'}<=states and before==after and tools.mutation_grant is None
            report['cases'].append({'case':'regular_to_setup_fragment_actual_readonly_doer','passed':bool(valid),
                'local_model_requests':model.calls,'global_coverage_complete':tools.safety_status['code'] is None,
                'page_observed':'SETUP_PAGE_OBSERVED' in states,'doer_started':'SETUP_DOER_STARTED' in states,
                'form_unchanged':before==after})
            page.evaluate=evaluate
            for label,suffix,predicate in [('wrong_path','/wrong','PATH'),('wrong_query','/regular?bad=1','QUERY'),
                ('wrong_fragment','/regular#audience','FRAGMENT'),('wrong_step','/regular#setup','SETUP_IDENTITY')]:
                await page.goto(expected);await page.wait_for_load_state('domcontentloaded',timeout=5000)
                await settled(binding.session,expected)
                await evaluate('history.replaceState(null,"",'+json.dumps(suffix)+')')
                if label=='wrong_step':await evaluate("document.querySelector('h2').textContent='Audience'")
                other=SetupTools.for_fixture(browser,expected,authorized_mutations=False)
                observed=await other.observe_page()
                assert observed['ok'] and observed['page_state']=='ALLOWED_ORIGIN' and not observed['form_recognized']
                result=await other.fill_text(next(f['reference'] for f in observed['fields'] if f['field']=='campaign_name'))
                actual=[e.get('diagnostic',{}).get('reason') for e in trace.events if e.get('diagnostic',{}).get('state')=='SETUP_DESTINATION_REJECTED'][-1]
                report['cases'].append({'case':label,'passed':result.get('error')=='UNEXPECTED_SETUP_DESTINATION' and actual==predicate,
                    'read_only_observation_allowed':observed['ok'],'field_write_refused':not result.get('ok'),
                    'predicate':actual})
            current=await page.url()
            try:validate_setup_destination(expected.replace('127.0.0.1','localhost'),expected)
            except DestinationRejected as error:wrong_origin=error.predicate=='HOST'
            else:wrong_origin=False
            report['cases'].append({'case':'wrong_origin_refused_before_navigation','passed':wrong_origin and await page.url()==current})
            report['checks']={'guard_healthy':not browser.guard.failed,'bound_tab':bound_tab_observable(browser.guard,binding),
                'original_retained':await original.evaluate('document.documentElement!==null') is True,
                'exactly_two_pages':len(await browser.browser.context.pages())==2,
                'no_login_or_api':browser.login_submissions==trace.model_calls==trace.openai_api_calls==0}
            report['passed']=all(c['passed'] for c in report['cases']) and all(report['checks'].values())
        except Exception as error:report['error_category']=type(error).__name__;report['error_code']=getattr(error,'code',None)
        finally:
            await browser.close()
            report['cleanup_result']=trace.cleanup_result
            report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
            report['seconds']=round(monotonic()-started,3)
            (ROOT/'outputs/real-local-setup-destination.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
