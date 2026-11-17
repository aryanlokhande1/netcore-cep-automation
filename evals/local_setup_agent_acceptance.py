"""Actual local Setup observation/action acceptance; no Netcore/login/inference.

Only an application-owned loopback form is served. Fixture event counters expose
booleans/counts, never page values, arbitrary URLs, selector strings or DOM bodies.
"""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from html import escape
import hashlib
import json
from pathlib import Path
from threading import Thread
from time import monotonic
from importlib.metadata import version
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab,bound_tab_observable
from evals.local_shadow_safety import sanitized_error_types
from modules.whatsapp.contract import VALUES


def fixture_html(scenario):
    """Static local form; all requested values remain application-owned constants."""
    values={k:escape(v,quote=True) for k,v in VALUES.items()}
    correct=scenario=='correct'
    campaign_value=' value="'+values['campaign_name']+'"' if correct else ''
    tag_value=' value="'+values['tags']+'"' if correct else ''
    security=("<input type='password' aria-label='Local blocked password'>" if scenario=='credential'
        else "<input autocomplete='one-time-code' aria-label='Local blocked challenge'>" if scenario=='challenge'
        else '')
    duplicate="<label>Campaign name<input type='text'></label>" if scenario=='ambiguous' else ''
    hidden="style='display:none'" if scenario=='hidden' else ''
    return f'''<!doctype html><html><head><meta charset="utf-8"><title>Local Setup fixture</title>
    <style>body{{font:16px sans-serif;margin:24px}}label{{display:block;margin:16px}}input,select,button{{padding:8px}}
    #local_component{{position:fixed;right:8px;top:8px;display:block;width:120px;height:40px}}</style></head><body>
    <h1>WhatsApp campaign</h1><h2 aria-current="step">Setup</h2><div style="height:150vh" aria-hidden="true"></div>
    <form id="local_form" aria-label="WhatsApp Setup">
      <label {hidden}>Campaign name<input id="local_campaign" type="text"{campaign_value}></label>
      {duplicate}
      <div><label for="local_waba">WABA ID</label><select id="local_waba"><option value="{values['waba']}" selected>{values['waba']}</option></select></div>
      <div><label for="local_business">Business number</label><select id="local_business"><option value=""{'' if correct else ' selected'}>Select</option><option value="approved_business"{' selected' if correct else ''}>{values['business_number']}</option></select></div>
      <label>Add tags<input id="local_tags" type="text" role="combobox" aria-controls="local_tag_options" aria-expanded="{'true' if correct else 'false'}"{tag_value}></label>
      <div id="local_tag_options" role="listbox"{'' if correct else ' hidden'}>
        <input type="search" aria-label="Search existing tags">
        <div id="local_existing_tag" role="option" data-existing="true" aria-selected="{'true' if correct else 'false'}">{values['tags']}</div>
      </div>
      <div id="local_forbidden_controls">
        {''.join('<button type="button" data-local-forbidden="true">'+label+'</button>' for label in ['Save','Next Step','Finish Later','Send','Schedule','Publish','Activate','Audience'])}
      </div>
    </form>
    <div id="local_component" role="complementary" aria-label="Local help component"></div>
    {security}
    <script>
      window.__localFixture={{forbidden_clicks:0,submissions:0,input_events:0,change_events:0,dropdown_opens:0,dom_mutations:0}};
      const form=document.getElementById('local_form'),tags=document.getElementById('local_tags'),popup=document.getElementById('local_tag_options'),option=document.getElementById('local_existing_tag');
      form.addEventListener('submit',e=>{{e.preventDefault();window.__localFixture.submissions++}});
      form.addEventListener('input',()=>window.__localFixture.input_events++);
      form.addEventListener('change',()=>window.__localFixture.change_events++);
      for(const b of document.querySelectorAll('[data-local-forbidden]'))b.addEventListener('click',()=>window.__localFixture.forbidden_clicks++);
      tags.addEventListener('click',()=>{{popup.hidden=false;tags.setAttribute('aria-expanded','true');window.__localFixture.dropdown_opens++}});
      option.addEventListener('click',()=>{{tags.value={json.dumps(VALUES['tags'])};option.setAttribute('aria-selected','true');tags.dispatchEvent(new Event('change',{{bubbles:true}}))}});
      new MutationObserver(records=>window.__localFixture.dom_mutations+=records.length).observe(form,{{subtree:true,attributes:true,childList:true,characterData:true}});
      {'document.getElementById("local_component").attachShadow({mode:"closed"}).append(document.createElement("span"));' if scenario=='closed' else ''}
    </script></body></html>'''


@contextmanager
def local_fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path!='/setup':self.send_error(404);return
            self.server.fixture_gets+=1
            body=fixture_html(self.server.scenario).encode('utf-8')
            self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.end_headers();self.wfile.write(body)
        def do_POST(self):
            self.server.fixture_posts+=1;self.send_error(405)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.scenario='normal';server.fixture_gets=server.fixture_posts=0
    server.origin='http://127.0.0.1:'+str(server.server_port);server.url=server.origin+'/setup'
    worker=Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True);worker.start()
    try:yield server
    finally:server.shutdown();server.server_close();worker.join(2)


async def fixture_facts(page):
    """Exact-value booleans and event counters only; never return current values."""
    settings=json.dumps(VALUES)
    return await page.evaluate('''(values=>({
      campaign_matches:document.getElementById('local_campaign').value===values.campaign_name,
      waba_matches:document.getElementById('local_waba').value===values.waba,
      business_matches:document.getElementById('local_business').selectedOptions[0]?.textContent===values.business_number,
      tag_matches:document.getElementById('local_tags').value===values.tags,
      event_counts:{...window.__localFixture}
    }))(VALUES)'''.replace('VALUES',settings))


class LocalAcceptanceError(Exception):
    def __init__(self,code):self.code=code;super().__init__(code)


async def local_setup_agent_acceptance(executable,*,headless=False,output=None):
    source_names=('modules/whatsapp/tools.py','modules/whatsapp/authorization.py','modules/whatsapp/observation.py',
        'modules/whatsapp/scope_evidence.py','modules/whatsapp/safety.py','modules/whatsapp/dom.py',
        'modules/whatsapp/workflow.py','modules/whatsapp/agent.py','modules/whatsapp/review.py',
        'modules/whatsapp/session.py','modules/whatsapp/contract.py','app/trace.py',
        'deep_agents/permissions.py','deep_agents/offline.py','browser/protected_tabs.py',
        'browser/navigation_guard.py','browser/stagehand_client.py',
        'browser/observed_control.py','browser/metadata_channel.py','browser/shadow_inventory.py',
        'evals/local_setup_agent_acceptance.py')
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in source_names}
    from modules.whatsapp.tools import SetupTools
    executable=Path(executable)
    if not executable.is_file():raise ValueError('Installed Chromium executable required')
    report={'scope':'ACTUAL_LOCAL_SETUP_TOOLS_ONLY','headless':headless,'paid_inference':0,
        'credential_access':False,'netcore_navigation_attempts':0,'passed':False,
        'operator_review_is_local_fixture_double':True,'runtime_source_sha256':source_hashes,'cases':[],'tool_calls':[]}
    trace=Trace();trace.reasoner_mode='LOCAL_SETUP_ACTION_ACCEPTANCE_NO_MODEL'
    stage='BROWSER_START';started=monotonic();browser=None
    with local_fixture_server() as server:
        config=Config(str(executable),headless=headless,login_url='about:blank',expected_host='127.0.0.1',
            timeout_seconds=180,max_actions=400)
        browser=StagehandClient(config,OriginPolicy({server.origin}),trace,fixture=True)
        try:
            await browser.start();report['browser_started']=True
            report['chromium_product']=(await browser.guard.send('Browser.getVersion'))['product']
            report['stagehand_version']=version('stagehand');stage='PROTECTED_ABOUT_TABS'
            original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank')
            async with asyncio.timeout(5):
                while True:
                    browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                    owner=browser.guard.session_states[browser.guard.primary_session]
                    frame=(await browser.guard.send('Page.getFrameTree',session=owner.session))['frameTree']['frame']
                    if (frame.get('url')=='about:blank' and frame.get('id')==owner.frame_id
                        and (frame.get('loaderId') or None)==owner.loader_id):break
                    await browser.guard.lifecycle_changed.wait()
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding;browser.login_page=original
            browser.task_phase='setup'
            expected_tools={'observe_page','inspect_control','scroll_to_control','click_observed_control',
                'fill_text','select_observed_option','verify_field'}

            async def reset(scenario='normal',*,authorized=True,review=None):
                server.scenario=scenario;await page.goto(server.url)
                await page.wait_for_load_state('domcontentloaded',timeout=5000)
                async with asyncio.timeout(5):
                    while True:
                        browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                        owner=browser.guard.session_states[binding.session]
                        frame=(await browser.guard.send('Page.getFrameTree',session=owner.session))['frameTree']['frame']
                        if (frame.get('url')==server.url and frame.get('id')==owner.frame_id
                            and (frame.get('loaderId') or None)==owner.loader_id):break
                        await browser.guard.lifecycle_changed.wait()
                tools=SetupTools.for_fixture(browser,server.url,authorized_mutations=authorized,mutation_review=review)
                if {t.name for t in tools.registered_tools()}!=expected_tools:raise LocalAcceptanceError('SEVEN_TOOL_CONTRACT_MISMATCH')
                return tools

            async def call(tools,name,**args):
                from langsmith import tracing_context
                tool=next(t for t in tools.registered_tools() if t.name==name)
                with tracing_context(enabled=False):response=await tool.ainvoke(args)
                report['tool_calls'].append({'tool':name,'ok':response.get('ok') is True,
                    'code':response.get('error'),'phase':response.get('phase')})
                return response

            def require(response):
                if not response.get('ok'):raise LocalAcceptanceError(response.get('error','LOCAL_TOOL_FAILED'))
                return response

            def field(response,key):
                candidates=[f for f in response.get('fields',[]) if f.get('field')==key]
                if len(candidates)!=1 or not candidates[0].get('reference'):raise LocalAcceptanceError('LOCAL_FIELD_REFERENCE_UNAVAILABLE')
                return candidates[0]

            def no_events(facts):
                counts=facts['event_counts']
                return not any(counts[k] for k in ('input_events','change_events','forbidden_clicks','submissions','dom_mutations'))

            async def fill_contract(tools):
                observed=require(await call(tools,'observe_page'))
                before=await fixture_facts(page)
                require(await call(tools,'inspect_control',reference=field(observed,'campaign_name')['reference']))
                read_only_unchanged=no_events(await fixture_facts(page))
                scrolled=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
                actual_scroll=await page.evaluate('window.scrollY>0') is True
                current=require(await call(tools,'fill_text',reference=field(scrolled,'campaign_name')['reference']))
                current=require(await call(tools,'verify_field',reference=field(current,'campaign_name')['reference']))
                campaign_first=(await fixture_facts(page))['campaign_matches'] is True
                current=require(await call(tools,'verify_field',reference=field(current,'waba')['reference']))
                current=require(await call(tools,'scroll_to_control',reference=field(current,'business_number')['reference']))
                business=field(current,'business_number')
                if len(business['options'])!=1:raise LocalAcceptanceError('LOCAL_BUSINESS_OPTION_UNAVAILABLE')
                current=require(await call(tools,'select_observed_option',reference=business['reference'],option_reference=business['options'][0]['reference']))
                current=require(await call(tools,'verify_field',reference=field(current,'business_number')['reference']))
                current=require(await call(tools,'scroll_to_control',reference=field(current,'tags')['reference']))
                current=require(await call(tools,'click_observed_control',reference=field(current,'tags')['reference']))
                current=require(await call(tools,'inspect_control',reference=field(current,'tags')['reference']))
                tag=field(current,'tags')
                if len(tag['options'])!=1:raise LocalAcceptanceError('LOCAL_TAG_OPTION_UNAVAILABLE')
                current=require(await call(tools,'select_observed_option',reference=tag['reference'],option_reference=tag['options'][0]['reference']))
                require(await call(tools,'verify_field',reference=field(current,'tags')['reference']))
                verified=require(await tools.verify());facts=await fixture_facts(page)
                return {'read_only_unchanged':read_only_unchanged and no_events(before),'actual_scroll':actual_scroll,
                    'campaign_first':campaign_first,'all_four_match':all(facts[k] for k in ('campaign_matches','waba_matches','business_matches','tag_matches')),
                    'no_commit':facts['event_counts']['forbidden_clicks']==facts['event_counts']['submissions']==0,
                    'all_four_facts_recorded':tools.completed==set(VALUES),'status':verified['status'],
                    'mutation_authorization':verified['mutation_authorization']}

            stage='NORMAL_SEVEN_TOOL_FLOW';tools=await reset()
            normal=await fill_contract(tools)
            report['cases'].append({'case':'complete_coverage_seven_tool_flow','passed':all(normal[k] for k in
                ('read_only_unchanged','actual_scroll','campaign_first','all_four_match','no_commit','all_four_facts_recorded')),
                **normal})

            stage='UNAUTHORIZED_READ_ONLY';tools=await reset(authorized=False)
            observed=require(await call(tools,'observe_page'))
            observed=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            denied=await call(tools,'fill_text',reference=field(observed,'campaign_name')['reference'])
            again=await call(tools,'observe_page');facts=await fixture_facts(page)
            report['cases'].append({'case':'separate_application_mutation_authority','passed':denied.get('error')=='MUTATION_AUTHORIZATION_REQUIRED'
                and not denied.get('terminal') and again.get('ok') is True and not facts['campaign_matches'] and no_events(facts),
                'code':denied.get('error'),'read_only_remains_available':again.get('ok') is True})

            reviews=[]
            async def decline(payload):reviews.append(payload);return False
            stage='CLOSED_SCOPE_DECLINED';tools=await reset('closed',review=decline)
            observed=require(await call(tools,'observe_page'));facts=await fixture_facts(page)
            closed_observed=observed['global_safety']['code']=='SAFETY_OBSERVATION_INCOMPLETE' and no_events(facts)
            observed=require(await call(tools,'inspect_control',reference=field(observed,'campaign_name')['reference']))
            observed=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            denied=await call(tools,'fill_text',reference=field(observed,'campaign_name')['reference'])
            again=await call(tools,'observe_page');facts=await fixture_facts(page)
            report['cases'].append({'case':'closed_scope_read_only_before_separate_review','passed':closed_observed and len(reviews)==1
                and denied.get('error')=='MUTATION_REVIEW_DECLINED' and not denied.get('terminal') and again.get('ok') is True
                and not facts['campaign_matches'] and no_events(facts),'code':denied.get('error'),
                'global_coverage_remains_incomplete':closed_observed,'operator_review_calls':len(reviews)})

            reviews=[]
            async def approve(payload):reviews.append(payload);return True
            stage='CLOSED_SCOPE_EXPLICIT_APPROVAL';tools=await reset('closed',review=approve)
            scoped=await fill_contract(tools);summary=scoped['mutation_authorization']
            report['cases'].append({'case':'separate_operator_scoped_poc','passed':all(scoped[k] for k in
                ('read_only_unchanged','actual_scroll','campaign_first','all_four_match','no_commit','all_four_facts_recorded'))
                and len(reviews)==1 and summary.get('route')=='OPERATOR_REVIEWED_SCOPED_POC'
                and summary.get('complete_negative_coverage') is False and summary.get('independent_authentication_certified') is False,
                'operator_review_calls':len(reviews),**scoped})

            stage='GENUINE_DEEP_AGENTS_READ_ONLY_LOCAL_MODEL';tools=await reset('closed',authorized=False)
            from deep_agents.offline import SyntheticChatModel
            from langchain_core.messages import AIMessage,ToolMessage
            from langchain_core.outputs import ChatGeneration,ChatResult
            from langsmith import tracing_context
            from modules.whatsapp.agent import create_setup_doer
            from modules.whatsapp.contract import Phase1Limits
            from modules.whatsapp.session import Phase1Budget
            from uuid import uuid4
            class ReadOnlySetupModel(SyntheticChatModel):
                observed_incomplete:bool=False
                actual_read_results_ok:bool=True
                def _generate(self,messages,stop=None,run_manager=None,**kwargs):
                    self.calls+=1
                    reads=[m for m in messages if isinstance(m,ToolMessage)]
                    if not reads:name,args='observe_page',{}
                    else:
                        last=reads[-1];data=json.loads(last.content)
                        self.actual_read_results_ok &= data.get('ok') is True and data.get('form_recognized') is True
                        self.observed_incomplete |= data.get('global_safety',{}).get('code')=='SAFETY_OBSERVATION_INCOMPLETE'
                        if last.name=='observe_page':
                            campaign=next(f for f in data['fields'] if f['field']=='campaign_name')
                            name,args='inspect_control',{'reference':campaign['reference']}
                        else:name,args='SetupReport',{'status':'BLOCKED','reason':'unsupported_control'}
                    message=AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}])
                    return ChatResult(generations=[ChatGeneration(message=message)])
            local_model=ReadOnlySetupModel(model_name='scripted-local-real-browser-read-only')
            local_budget=Phase1Budget(Phase1Limits());local_budget.phase='setup'
            graph,middleware=create_setup_doer(local_model,tools,trace,local_budget,offline=True)
            graph_start=len(trace.events);before=await fixture_facts(page)
            with tracing_context(enabled=False):
                state=await graph.ainvoke({'messages':[{'role':'user','content':
                    'Inspect only the observed local Setup form. No mutation is authorized. Report BLOCKED.'}]},
                    {'configurable':{'thread_id':trace.run_id+'-readonly-fixture'},'recursion_limit':20})
            after=await fixture_facts(page);structured=state.get('structured_response')
            actual_reads=[e['tool_name'] for e in trace.events[graph_start:] if e.get('tool_phase')=='started'
                and e.get('tool_name') in {'observe_page','inspect_control'}]
            graph_ok=(local_model.calls==local_budget.requests==local_budget.stage_requests['setup']==3
                and actual_reads==['observe_page','inspect_control'] and local_model.actual_read_results_ok
                and local_model.observed_incomplete and getattr(structured,'status',None)=='BLOCKED'
                and before==after and no_events(after) and tools.mutation_grant is None
                and trace.openai_api_calls==0 and not any(e.get('tool_phase')=='denied' for e in trace.events[graph_start:]))
            report['cases'].append({'case':'genuine_deep_agents_local_model_actual_read_only_browser','passed':graph_ok,
                'local_model_requests':local_model.calls,'setup_request_cap_unchanged':local_budget.stage_caps['setup'],
                'actual_registered_read_sequence':actual_reads,'structured_status':getattr(structured,'status',None),
                'global_coverage_remains_incomplete':local_model.observed_incomplete,
                'dom_and_values_unchanged':before==after and no_events(after),'mutation_grant_absent':tools.mutation_grant is None,
                'actual_api_requests':trace.openai_api_calls,'model_is_explicit_local_double':True})
            report['local_model_requests']=local_model.calls

            stage='CONFIRMED_SECURITY_NEGATIVES';security=[]
            for scenario in ('credential','challenge'):
                tools=await reset(scenario,review=approve);response=await call(tools,'observe_page');facts=await fixture_facts(page)
                security.append({'scenario':scenario,'passed':response.get('error')=='AUTHENTICATION_OR_SECURITY_STOP'
                    and response.get('terminal') is True and no_events(facts),'code':response.get('error')})
            report['cases'].append({'case':'confirmed_security_negatives_hard_stop','passed':all(c['passed'] for c in security),'subcases':security})

            stage='HIDDEN_AND_AMBIGUOUS';unsafe=[]
            for scenario in ('hidden','ambiguous'):
                tools=await reset(scenario);observed=require(await call(tools,'observe_page'))
                campaign=next(f for f in observed['fields'] if f['field']=='campaign_name')
                denied=await call(tools,'fill_text',reference='unobserved-local-fixture-control')
                facts=await fixture_facts(page)
                unsafe.append({'scenario':scenario,'passed':campaign.get('reference') is None and denied.get('ok') is False
                    and not facts['campaign_matches'] and no_events(facts),'code':denied.get('error')})
            report['cases'].append({'case':'hidden_and_ambiguous_targets_refused','passed':all(c['passed'] for c in unsafe),'subcases':unsafe})

            stage='PROHIBITED_TOOL_REFERENCES';prohibited=[]
            tools=await reset()
            for label in ('Save','Next Step','Finish Later','Send','Schedule','Publish','Activate','Audience'):
                tools=SetupTools.for_fixture(browser,server.url,authorized_mutations=True)
                require(await call(tools,'observe_page'))
                denied=await call(tools,'click_observed_control',reference='unobserved-'+label)
                prohibited.append({'control':label,'passed':denied.get('error')=='UNOBSERVED_SETUP_ELEMENT','code':denied.get('error')})
            facts=await fixture_facts(page)
            report['cases'].append({'case':'all_prohibited_controls_unexposed_to_tools','passed':all(c['passed'] for c in prohibited)
                and no_events(facts),'subcases':prohibited})

            stage='STALE_REFERENCE';tools=await reset()
            observed=require(await call(tools,'observe_page'))
            observed=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            prior=field(observed,'campaign_name')['reference']
            await page.evaluate("(() => {const e=document.getElementById('local_campaign');e.replaceWith(e.cloneNode(true));return true})()")
            before=await fixture_facts(page);denied=await call(tools,'fill_text',reference=prior);after=await fixture_facts(page)
            report['cases'].append({'case':'stale_backend_identity_refused','passed':denied.get('error')=='STALE_CONTROL_REFERENCE'
                and before==after and not after['campaign_matches'],'code':denied.get('error')})

            stage='SDK_SHADOW_DUPLICATE';tools=await reset()
            await page.evaluate("(() => {const h=document.createElement('div');document.body.append(h);const r=h.attachShadow({mode:'open'});const e=document.createElement('input');e.id='local_campaign';r.append(e);window.__localShadowInput=e;return true})()")
            observed=require(await call(tools,'observe_page'))
            observed=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            sdk_count=await page.locator('#local_campaign').count(timeout=2000)
            response=await call(tools,'fill_text',reference=field(observed,'campaign_name')['reference'])
            facts=await fixture_facts(page);shadow_untouched=await page.evaluate("window.__localShadowInput.value===''" ) is True
            safe=(sdk_count>1 and response.get('error')=='ACTION_LOCATOR_NOT_UNIQUE' and not facts['campaign_matches']) or (sdk_count==1 and response.get('ok') is True and facts['campaign_matches'])
            report['cases'].append({'case':'actual_sdk_shadow_selector_uniqueness','passed':safe and shadow_untouched,
                'sdk_match_count':sdk_count,'code':response.get('error'),'unobserved_shadow_input_untouched':shadow_untouched})

            stage='DOM_FORBIDDEN_TRANSITION_GUARD';tools=await reset();await fill_contract(tools)
            # Fixture-only direct clicks prove the installed DOM transition guard;
            # these controls remain absent from the seven application tools.
            for index in range(8):await page.locator('[data-local-forbidden]').nth(index).click(timeout=2000)
            guard=await page.evaluate('({attempts:window.__netcoreSetupGuard?.attempts??null})')
            facts=await fixture_facts(page);blocked=await tools.verify()
            report['cases'].append({'case':'real_prohibited_control_events_blocked','passed':guard['attempts']==8
                and facts['event_counts']['forbidden_clicks']==facts['event_counts']['submissions']==0
                and blocked.get('error')=='FORBIDDEN_TRANSITION_ATTEMPTED','blocked_events':guard['attempts'],
                'code':blocked.get('error')})

            stage='FINAL_OWNERSHIP_CHECK';pages=await context.pages()
            checks={'all_cases_passed':all(c['passed'] for c in report['cases']),
                'all_seven_tools_exercised':{c['tool'] for c in report['tool_calls']}==expected_tools,
                'same_owned_process':browser.process is process,'same_context':browser.browser.context is context,
                'exactly_two_same_context_pages':len(pages)==2 and {p.page_id for p in pages}=={original.page_id,page.page_id},
                'original_available':await original.evaluate('document.documentElement!==null') is True,
                'setup_tab_protected':bound_tab_observable(browser.guard,binding),'guard_healthy':not browser.guard.failed,
                'no_premature_close':not browser.close_started and process.status_now()=='ALIVE',
                'zero_login_submissions':browser.login_submissions==0,'zero_model_calls':trace.model_calls==trace.openai_api_calls==0,
                'zero_fixture_posts':server.fixture_posts==0}
            report['checks']=checks;report['passed']=all(checks.values())
        except Exception as error:
            report.update(error_category=type(error).__name__,error_code=getattr(error,'code',None),failure_stage=stage,
                error_causes=sanitized_error_types(error),browser_created=browser.browser is not None,
                guard_created=browser.guard is not None,owned_process_identified=browser.process is not None)
        finally:
            try:await browser.close()
            except Exception:report['cleanup_exception']=True
            report['cleanup_result']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
            report['duration_seconds']=round(monotonic()-started,3)
            report['runtime_sources_unchanged']=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
                for name,digest in source_hashes.items())
            report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED' and report['runtime_sources_unchanged']
            path=Path(output or ROOT/'outputs'/'real-local-setup-agent-acceptance.json')
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report


async def local_setup_scroll_acceptance(executable,*,headless=False,output=None):
    """Focused final-edge browser proof after the complete eleven-case acceptance."""
    baseline=json.loads((ROOT/'outputs/real-local-setup-agent-acceptance.json').read_text(encoding='utf-8'))
    source_hashes={name:hashlib.sha256((ROOT/name).read_bytes()).hexdigest() for name in baseline['runtime_source_sha256']}
    changed=[name for name,digest in source_hashes.items() if digest!=baseline['runtime_source_sha256'][name]]
    from modules.whatsapp.tools import SetupTools
    from modules.whatsapp.contract import SetupStop
    from langsmith import tracing_context
    executable=Path(executable)
    if not executable.is_file():raise ValueError('Installed Chromium executable required')
    report={'scope':'ACTUAL_LOCAL_SETUP_FINAL_EDGES_ONLY','headless':headless,'paid_inference':0,
        'credential_access':False,'netcore_navigation_attempts':0,'passed':False,'cases':[],
        'runtime_source_sha256':source_hashes,'source_changes_since_full_suite':changed,
        'operator_review_is_local_fixture_double':True}
    trace=Trace();trace.reasoner_mode='LOCAL_SETUP_EDGE_ACCEPTANCE_NO_MODEL'
    started=monotonic();stage='BROWSER_START'
    with local_fixture_server() as server:
        browser=StagehandClient(Config(str(executable),headless=headless,login_url='about:blank',
            expected_host='127.0.0.1',timeout_seconds=90,max_actions=80),OriginPolicy({server.origin}),trace,fixture=True)
        try:
            await browser.start();report['browser_started']=True
            report['chromium_product']=(await browser.guard.send('Browser.getVersion'))['product']
            report['stagehand_version']=version('stagehand')
            original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank')
            async with asyncio.timeout(5):
                while True:
                    browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                    owner=browser.guard.session_states[browser.guard.primary_session]
                    frame=(await browser.guard.send('Page.getFrameTree',session=owner.session))['frameTree']['frame']
                    if (frame.get('url')=='about:blank' and frame.get('id')==owner.frame_id
                        and (frame.get('loaderId') or None)==owner.loader_id):break
                    await browser.guard.lifecycle_changed.wait()
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding;browser.login_page=original;browser.task_phase='setup'

            async def reset(scenario='normal',*,authorized=False,review=None):
                server.scenario=scenario;await page.goto(server.url)
                await page.wait_for_load_state('domcontentloaded',timeout=5000)
                async with asyncio.timeout(5):
                    while True:
                        browser.guard.lifecycle_changed.clear();await browser.guard.ensure_observable()
                        owner=browser.guard.session_states[binding.session]
                        frame=(await browser.guard.send('Page.getFrameTree',session=owner.session))['frameTree']['frame']
                        if (frame.get('url')==server.url and frame.get('id')==owner.frame_id
                            and (frame.get('loaderId') or None)==owner.loader_id):break
                        await browser.guard.lifecycle_changed.wait()
                return SetupTools.for_fixture(browser,server.url,authorized_mutations=authorized,mutation_review=review)

            async def call(tools,name,**args):
                registered=next(t for t in tools.registered_tools() if t.name==name)
                with tracing_context(enabled=False):return await registered.ainvoke(args)

            def require(response):
                if not response.get('ok'):raise LocalAcceptanceError(response.get('error','LOCAL_EDGE_TOOL_FAILED'))
                return response

            def field(response,key):
                return next(f for f in response['fields'] if f['field']==key)

            def unchanged(facts):
                return not any(facts['event_counts'][k] for k in ('input_events','change_events','forbidden_clicks','submissions','dom_mutations'))

            stage='HIDDEN_NATIVE_OPTION_SCROLL_REJECTION';tools=await reset()
            observed=require(await call(tools,'observe_page'));before=await fixture_facts(page)
            option=field(observed,'waba')['options'][0]['reference'];scroll_commands=0;send=browser.guard.send
            async def count_scroll(method,params=None,session=None):
                nonlocal scroll_commands
                if method=='DOM.scrollIntoViewIfNeeded':scroll_commands+=1
                return await send(method,params,session)
            browser.guard.send=count_scroll
            try:denied=await call(tools,'scroll_to_control',reference=option)
            finally:browser.guard.send=send
            after=await fixture_facts(page)
            report['cases'].append({'case':'hidden_native_option_rejected_before_scroll','passed':denied.get('error')=='UNOBSERVED_SETUP_ELEMENT'
                and scroll_commands==0 and before==after and unchanged(after),'code':denied.get('error'),'actual_cdp_scroll_commands':scroll_commands})

            stage='CLOSED_SCOPE_READ_ONLY_CONTROL_SCROLL';tools=await reset('closed')
            observed=require(await call(tools,'observe_page'))
            observed=require(await call(tools,'inspect_control',reference=field(observed,'campaign_name')['reference']))
            before=await fixture_facts(page)
            scrolled=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            after=await fixture_facts(page)
            report['cases'].append({'case':'incomplete_closed_scope_still_permits_real_control_scroll','passed':
                scrolled['global_safety']['code']=='SAFETY_OBSERVATION_INCOMPLETE' and tools.mutation_grant is None
                and before==after and unchanged(after) and await page.evaluate('window.scrollY>0') is True,
                'read_only_dom_unchanged':before==after and unchanged(after),'mutation_grant_absent':tools.mutation_grant is None})

            stage='PREEXISTING_VALUES_CONTROLLER_GUARD';tools=await reset('correct')
            observed=require(await call(tools,'observe_page'));before=await fixture_facts(page)
            verified=require(await tools.verify())
            guard=await page.evaluate('({installed:!!window.__netcoreSetupGuard,attempts:window.__netcoreSetupGuard?.attempts??null})')
            after=await fixture_facts(page)
            protected_without_writes=(all(before[k] for k in ('campaign_matches','waba_matches','business_matches','tag_matches'))
                and before==after and unchanged(after) and guard['installed'] is True and guard['attempts']==0
                and verified['status']=='SETUP_FILLED_UNSAVED' and tools.mutation_grant is None)
            for index in range(8):await page.locator('[data-local-forbidden]').nth(index).click(timeout=2000)
            guard=await page.evaluate('({attempts:window.__netcoreSetupGuard?.attempts??null})')
            facts=await fixture_facts(page);blocked=await tools.verify()
            report['cases'].append({'case':'preexisting_all_fields_get_controller_transition_protection','passed':protected_without_writes
                and guard['attempts']==8 and facts['event_counts']['forbidden_clicks']==facts['event_counts']['submissions']==0
                and blocked.get('error')=='FORBIDDEN_TRANSITION_ATTEMPTED','preexisting_values_verified_without_writes':protected_without_writes,
                'blocked_events':guard['attempts'],'code':blocked.get('error')})

            stage='REVIEW_TIMEOUT_AND_PERSISTENT_DENIAL';review_calls=0
            async def timed_out(payload):
                nonlocal review_calls
                review_calls+=1;raise SetupStop('MUTATION_REVIEW_TIMEOUT')
            tools=await reset('closed',authorized=True,review=timed_out)
            observed=require(await call(tools,'observe_page'))
            observed=require(await call(tools,'scroll_to_control',reference=field(observed,'campaign_name')['reference']))
            denied=await call(tools,'fill_text',reference=field(observed,'campaign_name')['reference'])
            read_after=require(await call(tools,'observe_page'))
            await page.evaluate("(() => {document.getElementById('local_component').remove();return true})()")
            complete=require(await call(tools,'observe_page'))
            still_denied=await call(tools,'fill_text',reference=field(complete,'campaign_name')['reference'])
            facts=await fixture_facts(page)
            report['cases'].append({'case':'operator_timeout_preserves_reads_and_does_not_expire_into_authority','passed':
                denied.get('error')==still_denied.get('error')=='MUTATION_REVIEW_TIMEOUT' and not denied.get('terminal')
                and read_after.get('ok') is True and complete['global_safety']['code'] is None and review_calls==1
                and unchanged(facts) and not facts['campaign_matches'] and tools.mutation_grant is None,
                'code':denied.get('error'),'after_complete_coverage_code':still_denied.get('error'),'review_calls':review_calls,
                'read_only_available_after_timeout':read_after.get('ok') is True})

            stage='FINAL_OWNERSHIP_CHECK';pages=await context.pages()
            allowed_changes={'modules/whatsapp/tools.py','modules/whatsapp/authorization.py','evals/local_setup_agent_acceptance.py'}
            report['checks']={'all_targeted_cases_passed':all(c['passed'] for c in report['cases']),
                'only_declared_sources_changed_since_full_suite':set(changed)<=allowed_changes,
                'same_owned_process':browser.process is process,'same_context':browser.browser.context is context,
                'exactly_two_same_context_pages':len(pages)==2 and {p.page_id for p in pages}=={original.page_id,page.page_id},
                'original_available':await original.evaluate('document.documentElement!==null') is True,
                'setup_tab_protected':bound_tab_observable(browser.guard,binding),'guard_healthy':not browser.guard.failed,
                'no_premature_close':not browser.close_started and process.status_now()=='ALIVE',
                'zero_login_submissions':browser.login_submissions==0,'zero_model_calls':trace.model_calls==trace.openai_api_calls==0,
                'zero_fixture_posts':server.fixture_posts==0}
            report['passed']=all(report['checks'].values())
        except Exception as error:
            report.update(error_category=type(error).__name__,error_code=getattr(error,'code',None),failure_stage=stage,
                error_causes=sanitized_error_types(error))
        finally:
            try:await browser.close()
            except Exception:report['cleanup_exception']=True
            report['cleanup_result']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
            report['duration_seconds']=round(monotonic()-started,3)
            report['runtime_sources_unchanged']=all(hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest
                for name,digest in source_hashes.items())
            report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED' and report['runtime_sources_unchanged']
            path=Path(output or ROOT/'outputs'/'real-local-setup-scroll-acceptance.json')
            path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
