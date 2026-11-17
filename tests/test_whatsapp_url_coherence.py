"""Paired URL reads under real local history changes; no provider or tenant."""
import json
import os
from unittest.mock import AsyncMock
import pytest
from app.config import ROOT
from modules.whatsapp.contract import SETUP_URL, SetupStop
from test_whatsapp_phase1 import protocol_metadata_transport
from test_whatsapp_phase_b import bound_setup_tools


def test_saved_run_distinguishes_missing_comparison_details_and_later_form():
    run='688cb9c230394e89a98eb8a991136f63'
    result=json.loads((ROOT/'outputs'/f'{run}-result.json').read_text())
    events=json.loads((ROOT/'outputs'/f'{run}.json').read_text())
    assert result['setup_navigation']['sdk_and_cdp_urls_agree'] is False
    assert result['setup_navigation']['sdk_route']==result['setup_navigation']['final_route']=='INTENDED_WHATSAPP_ROUTE'
    assert 'url_samples' not in result['setup_navigation']
    assert result['setup_observation']['snapshot']['sequence']==2
    assert result['setup_observation']['dom_form_recognized'] is False
    assert result['setup_failure_evidence']['dom']['form_recognized'] is True
    assert events[267]['elapsed_seconds']==29.422 and events[271]['elapsed_seconds']==30.0
    assert not any(f['kind']=='SKILL_READ' for f in result['step_facts'])


async def test_same_document_change_between_sdk_and_cdp_is_reinspected():
    browser,tools=await bound_setup_tools();send=browser.guard.send;changed=False
    async def race(method,params=None,session=None):
        nonlocal changed
        if method=='Page.getFrameTree' and not changed:
            changed=True;browser.page.location=SETUP_URL+'#setup'
        return await send(method,params,session)
    browser.guard.send=race
    await tools.destination_check(probe=True)
    assert tools.navigation_evidence['sdk_and_cdp_urls_agree']
    assert len(tools.navigation_evidence['url_samples'])>=2


@pytest.mark.parametrize('permanent',[False,True])
async def test_missing_cdp_url_is_unavailable_not_a_wrong_target(permanent):
    browser,tools=await bound_setup_tools();send=browser.guard.send;calls=0
    async def missing(method,params=None,session=None):
        nonlocal calls
        data=await send(method,params,session)
        if method=='Page.getFrameTree':
            calls+=1
            if permanent or calls==1:data['frameTree']['frame'].pop('url')
        return data
    browser.guard.send=missing
    if permanent:
        with pytest.raises(SetupStop,match='SETUP_NAVIGATION_OBSERVATION_UNAVAILABLE'):await tools.destination_check(probe=True)
    else:await tools.destination_check(probe=True)
    assert tools.navigation_evidence['url_samples'][0]['cdp_url_present'] is False


async def test_wrong_frame_is_terminal_and_diagnosed():
    browser,tools=await bound_setup_tools();send=browser.guard.send
    async def wrong(method,params=None,session=None):
        data=await send(method,params,session)
        if method=='Page.getFrameTree':data['frameTree']['frame']['id']='WRONG_FRAME'
        return data
    browser.guard.send=wrong
    with pytest.raises(SetupStop,match='SETUP_NAVIGATION_DOCUMENT_NOT_COHERENT'):await tools.destination_check(probe=True)
    assert tools.navigation_evidence['url_samples'][-1]['frame_id_matches'] is False


async def test_same_url_source_tab_cannot_impersonate_bound_sdk_page():
    browser,tools=await bound_setup_tools()
    browser.login_page.location=SETUP_URL
    browser.page=browser.login_page
    with pytest.raises(SetupStop,match='SETUP_NAVIGATION_SDK_PAGE_BINDING_MISMATCH'):
        await tools.destination_check(probe=True)


@pytest.mark.skipif(os.getenv('RUN_REAL_URL_COHERENCE')!='1',reason='Explicit loopback Chromium reproduction')
@pytest.mark.parametrize('mode',['race','missing_once','missing_always','wrong_tab','wrong_tab_same_url','delayed'])
async def test_real_chromium_url_coherence_and_first_doer(mode):
    from app.config import Config,OriginPolicy
    from app.trace import Trace
    from browser.stagehand_client import StagehandClient
    from browser.protected_tabs import create_protected_tab
    from evals.local_browser_baseline import settled
    from test_whatsapp_navigation_repair import navigation_server
    from modules.whatsapp.tools import SetupTools
    from modules.whatsapp.agent import create_setup_doer
    from deep_agents.permissions import InferenceBudget
    from test_whatsapp_phase_b import Page1Model
    from langchain_core.messages import ToolMessage
    class Probe(Page1Model):
        def _generate(self,messages,**kwargs):
            self.calls+=1
            last=next((m for m in reversed(messages) if isinstance(m,ToolMessage)),None)
            if last is None:return self.reply('read_module_skill',{})
            if last.name=='read_module_skill':return self.reply('observe_page',{})
            if last.name=='observe_page':return self.reply('inspect_control',{'reference':json.loads(last.content)['fields'][0]['reference']})
            return self.reply('SetupReport',{'status':'BLOCKED','reason':'unsupported_control'})
    trace=Trace()
    with navigation_server('delayed' if mode=='delayed' else 'valid') as server:
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=True,login_url='about:blank',
            expected_host='127.0.0.1',max_actions=150,timeout_seconds=90),OriginPolicy({server.origin}),trace,fixture=True)
        try:
            await browser.start();source=browser.page
            await source.goto(server.dashboard if mode=='wrong_tab' else server.entry if mode=='wrong_tab_same_url' else 'about:blank')
            await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding;browser.login_page=source;browser.task_phase='setup'
            await page.goto(server.entry);await settled(browser,binding.session)
            tools=SetupTools.for_fixture(browser,server.entry,authorized_mutations=False)
            send=browser.guard.send;triggered=False
            async def intercepted(method,params=None,session=None):
                nonlocal triggered
                if method=='Page.getFrameTree' and session==binding.session:
                    if mode=='race' and not triggered:
                        triggered=True
                        await page.evaluate('history.replaceState(null,"",location.pathname+"#setup")')
                    data=await send(method,params,session)
                    if mode=='missing_always' or mode=='missing_once' and not triggered:
                        triggered=True;data['frameTree']['frame'].pop('url',None)
                    return data
                return await send(method,params,session)
            browser.guard.send=intercepted
            if mode in {'wrong_tab','wrong_tab_same_url'}:browser.page=source
            if mode in {'wrong_tab','wrong_tab_same_url','missing_always'}:
                code='SETUP_NAVIGATION_SDK_PAGE_BINDING_MISMATCH' if mode.startswith('wrong_tab') else 'SETUP_NAVIGATION_OBSERVATION_UNAVAILABLE'
                with pytest.raises(SetupStop,match=code):await tools.open()
                model_calls=0
            else:
                data=await tools.open()
                assert data['destination_ready'] and data['action_form_identity_bound']
                model=Probe();agent,_=create_setup_doer(model,tools,trace,InferenceBudget(6),offline=True,native_skill=True)
                from langsmith import tracing_context
                with tracing_context(enabled=False):
                    await agent.ainvoke({'messages':[{'role':'user','content':'Read-only Page 1 probe.'}]},
                        {'configurable':{'thread_id':trace.run_id},'recursion_limit':40})
                model_calls=model.calls
                assert model_calls==4 and tools.skill_provenance
                assert any(e['tool']=='inspect_control' for e in tools.events)
            assert tools.browser_operations==[] and trace.openai_api_calls==browser.login_submissions==server.posts==0
            report={'mode':mode,'navigation':tools.navigation_evidence,'local_model_calls':model_calls,'provider_requests':0}
        finally:await browser.close()
        assert trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        report['cleanup']=trace.cleanup_result
        (ROOT/'outputs/phase-b-url-coherence-repair-20261113'/f'local-{mode}.json').write_text(json.dumps(report,indent=2))
