"""Real loopback Chromium + installed Deep Agents; labeled model double, no provider."""
import asyncio,hashlib,json
from pathlib import Path
from uuid import uuid4
from unittest.mock import patch
from urllib.parse import urlsplit
from langchain_core.messages import AIMessage,ToolMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from deep_agents.offline import SyntheticChatModel
from app.config import Config,OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.discovery import ReadOnlyDiscovery
from browser.protected_tabs import create_protected_tab
from evals.fixture import fixture_server,Handler


class DiagnosticHandler(Handler):
    def do_GET(self):
        if urlsplit(self.path).path=='/tenant/whatsapp':
            return self.respond('<main><h1>WhatsApp campaigns</h1><form><label>Campaign name<input id="campaign" value="LOCAL_VALUE"></label><label>WABA id<select id="waba"><option>Local option</option></select></label></form></main>')
        return self.respond('<main><h1>Local dashboard</h1><nav><a id="wa" href="/tenant/whatsapp">WhatsApp</a><a id="other" href="/tenant/whatsapp-other">WhatsApp other</a></nav></main>')


class DiagnosticModel(SyntheticChatModel):
    model_name:str='human-assisted-local-no-provider'
    control_reference:str|None=None

    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        tool_messages=[m for m in messages if isinstance(m,ToolMessage)]
        previous={}
        if tool_messages:
            content=tool_messages[-1].content
            if isinstance(content,list):content=next(b['text'] for b in content if b.get('type')=='text')
            previous=json.loads(content)
            if tool_messages[-1].name=='browser_action' and previous.get('ok') is not True:
                raise AssertionError('LOCAL_BROWSER_TOOL_FAILED:'+str(previous.get('error')))
        step=self.calls;self.calls+=1
        if step==0:name,args='browser_action',{'action':'observe'}
        elif step==1:
            assert len(previous['navigation'])==1,'OPERATOR_BINDING_FILTER_NOT_ENFORCED'
            name,args='browser_action',{'action':'navigate','reference':previous['navigation'][0]['reference']}
        elif step==2:
            assert previous['whatsapp_page_observed'] is True
            self.control_reference=previous['controls'][0]['reference']
            name,args='browser_action',{'action':'readback','reference':self.control_reference}
        elif step==3:name,args='browser_action',{'action':'inspect','reference':self.control_reference}
        elif step==4:name,args='browser_action',{'action':'scroll','reference':self.control_reference,'delta_y':80}
        else:name,args='V2Turn',{'status':'VERIFY'}
        return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}]))])


async def acceptance(executable,output):
    from modules.whatsapp.workflow import human_assisted_session_safety,human_assisted_route
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.agent import create_v2_doer
    from modules.whatsapp.contract import V2Stop
    from learning.skill_storage.registry import module_baseline
    from deep_agents.permissions import InferenceBudget
    from app.state import Observation
    from langsmith import tracing_context
    trace=Trace(roles=('doer','observer'));trace.enable_agent_trace();trace.stream_browser_diagnostics=True
    report={'scope':'LOCAL_LOOPBACK_ONLY','provider_requests':0,'netcore_requests':0,'credential_submissions':0,'model_kind':'SCRIPTED_OFFLINE_DOUBLE','cases':[]}
    def check(name,condition,**facts):
        report['cases'].append({'case':name,'passed':bool(condition),**facts})
        assert condition,name
    browser=None
    with fixture_server() as server:
        server.RequestHandlerClass=DiagnosticHandler
        with patch('modules.whatsapp.contract.SETUP_URL',server.base+'/tenant/campaign/create'),patch('modules.whatsapp.tools.SETUP_URL',server.base+'/tenant/campaign/create'):
            browser=StagehandClient(Config(str(executable),headless=False,login_url=server.base+'/',
                expected_host='127.0.0.1',max_actions=500,timeout_seconds=180,evidence_file=str(Path(output).with_suffix('.private.json'))),OriginPolicy({server.base}),trace,fixture=True)
            try:
                await browser.start();await browser.navigate(server.base+'/tenant/home?view=observed');browser.login_page=browser.page
                from browser.diagnostics import owned_window_observation
                window=owned_window_observation(browser)
                check('owned_headed_window_visible',window['status']=='OBSERVED' and window['visible_owned_window_count']>0,observation=window)
                proof=await human_assisted_session_safety(browser)
                check('query_has_precise_fail_closed_predicate',proof['failed_predicates']==['query_absent'],proof=proof)
                await browser.navigate(server.base+'/tenant/home')
                proof=await human_assisted_session_safety(browser)
                check('owned_document_proven_without_authentication',proof['diagnostic_session_safe'])
                check('diagnostic_is_not_production_authentication',human_assisted_route(Observation(expected_host=True),diagnostic_consent=True,session_safe=True)=='DIAGNOSTIC')
                discovery=ReadOnlyDiscovery(browser);browser.discovery=discovery
                await discovery.prepare()
                candidates=await discovery.operator_candidates('navigation')
                chosen=next(c for c in candidates if discovery._operator_candidate_refs[c['reference']]['target']['selector']=='#wa')
                binding=await discovery.bind_candidate(chosen['reference'])
                await browser.page.evaluate("history.pushState({},'', '/tenant/changed');true")
                try:await discovery.bind_candidate(chosen['reference'])
                except (ValueError,PermissionError) as e:check('SPA_invalidates_candidate',getattr(e,'code',None)=='CANDIDATE_REFERENCE_STALE')
                else:check('SPA_invalidates_candidate',False)
                await browser.navigate(server.base+'/tenant/home')
                page,bound=await create_protected_tab(browser);browser.page=page;browser.setup_page=page;browser.setup_binding=bound
                await browser.navigate(server.base+'/tenant/home')
                tools=V2BrowserTools(browser,diagnostic_origin=server.base)
                tools.operator_navigation_selector_sha256=binding['selector_sha256']
                trace.agent_phase='task_doer';trace.agent_stage='whatsapp_diagnostic'
                model=DiagnosticModel();budget=InferenceBudget(8,role_limits={'doer':8,'observer':0,'teacher':0})
                agent=create_v2_doer(model,tools,trace,budget,module_baseline('whatsapp'))
                with tracing_context(enabled=False):
                    result=await agent.ainvoke({'messages':[{'role':'user','content':'Local read-only dispatch acceptance; no writes.'}]},{'configurable':{'thread_id':trace.run_id},'recursion_limit':20})
                check('real_DeepAgents_dispatch_and_readback',result['structured_response'].status=='VERIFY' and [e['action'] for e in tools.events]==['observe','navigate','readback','inspect','scroll'],offline_model_calls=model.calls)
                check('WhatsApp_observed_after_real_browser_navigation',tools.last_observation['whatsapp_page_observed'] is True)
                bad,_=await tools.action({'action':'navigate','url':server.base+'/tenant/whatsapp'})
                check('arbitrary_URL_rejected_before_browser',bad['error']=='INVALID_BROWSER_ACTION_ARGUMENTS')
                for action in ('click','type','select','advance','persist'):
                    bad,_=await tools.action({'action':action});check('write_rejected_'+action,bad['error']=='INVALID_BROWSER_ACTION_ARGUMENTS')
                from modules.whatsapp.tools import readonly_whatsapp_destination
                try:readonly_whatsapp_destination(server.base+'/tenant/home',server.base+'/other/whatsapp',server.base)
                except V2Stop:check('cross_tenant_navigation_rejected',True)
                else:check('cross_tenant_navigation_rejected',False)
                old_reference=next(iter(tools.references))
                await browser.page.evaluate("history.pushState({},'', '/tenant/whatsapp?view=changed');true")
                stale,_=await tools.action({'action':'readback','reference':old_reference})
                check('SPA_invalidates_task_reference',stale.get('ok') is False,actual_result=stale)
                check('selected_skill_consumed_locally',any(f['kind']=='SKILL_READ' for f in trace.step_facts))
            finally:
                await browser.close();report['cleanup']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
                report['events']=[e['agent_execution'] for e in trace.events if 'agent_execution' in e]
                report['passed']=all(c['passed'] for c in report['cases']) and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
                Path(output).write_text(json.dumps(report,indent=2)+'\n')
    return report
