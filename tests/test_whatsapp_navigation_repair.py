"""Navigation/readiness evidence: saved trace and local-only doubles."""
import json
import os
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from threading import Thread
from unittest.mock import AsyncMock

import pytest

from app.config import ROOT
from modules.whatsapp.contract import SETUP_URL, SetupStop
from test_whatsapp_phase1 import setup_tools, protocol_metadata_transport

RUN='ec36221a195c4a5e8b0f23994545ca6d'


def test_original_trace_has_bound_tab_but_no_historical_url_or_redirect_chain():
    events=json.loads((ROOT/'outputs'/f'{RUN}.json').read_text())
    assert events[203]['diagnostic']['state']=='PAGE_BOUND'
    assert events[207]['diagnostic']['state']=='SETUP_NAVIGATION_STARTED'
    assert events[208]['diagnostic']['redirect'] is False
    assert events[213]['diagnostic']['state']=='NAVIGATION_COMPLETED'
    assert events[215]['diagnostic']['code']=='WHATSAPP_DESTINATION_OPENED'
    assert events[264]['diagnostic']['path_match'] is True
    assert events[264]['diagnostic']['setup_identity_detected'] is False
    assert 'url' not in events[208]['diagnostic']


async def test_destination_opened_requires_route_and_actual_form():
    browser,tools=setup_tools()
    data=await tools.open()
    assert data['destination_ready'] is True
    assert data['page_state']=='SETUP_CONFIRMED'
    assert tools.form_identity is not None
    assert tools.navigation_evidence['requested_entry_url']==SETUP_URL
    assert tools.navigation_evidence['final_route']=='INTENDED_WHATSAPP_ROUTE'
    assert any(f['kind']=='WHATSAPP_DESTINATION_OPENED' for f in browser.trace.step_facts)


async def test_dashboard_redirect_is_precise_and_never_retried():
    browser,tools=setup_tools();create=browser.browser.context.new_page
    async def page(url):
        current=await create(url)
        async def redirected(destination):
            current.actions.append(('navigate',destination))
            current.location=SETUP_URL.split('/campaign/')[0]+'/'
        current.goto=redirected
        return current
    browser.browser.context.new_page=page
    with pytest.raises(SetupStop,match='SETUP_REDIRECTED_TO_DASHBOARD'):await tools.open()
    assert browser.setup_navigation_count==1
    assert not any(f['kind']=='WHATSAPP_DESTINATION_OPENED' for f in browser.trace.step_facts)
    assert tools.browser_operations==[]


async def test_correct_route_without_form_does_not_claim_arrival_or_allow_field_actions():
    browser,tools=setup_tools();create=browser.browser.context.new_page
    async def page(url):
        current=await create(url);evaluate=current.evaluate
        async def missing(source):
            data=await evaluate(source)
            if isinstance(data,dict) and 'fields' in data:
                data.update(form_recognized=False,page_identity=False,setup_active=False)
            return data
        current.evaluate=missing
        return current
    browser.browser.context.new_page=page
    data=await tools.open()
    assert not data['destination_ready']
    assert not any(f['kind']=='WHATSAPP_DESTINATION_OPENED' for f in browser.trace.step_facts)
    ref=data['fields'][0]['reference']
    result=await tools.fill_text(ref)
    assert result['error']=='SETUP_PAGE1_FORM_NOT_READY' and tools.browser_operations==[]


async def test_independent_cdp_url_detects_sdk_target_mismatch():
    browser,tools=setup_tools();await tools.open()
    send=browser.guard.send
    async def mismatch(method,params=None,session=None):
        data=await send(method,params,session)
        if method=='Page.getFrameTree' and session==browser.setup_binding.session:
            data['frameTree']['frame']['url']=SETUP_URL+'/other'
        return data
    browser.guard.send=mismatch
    with pytest.raises(SetupStop,match='SETUP_NAVIGATION_TARGET_URL_MISMATCH'):await tools.destination_check(probe=True)


async def test_document_and_same_document_events_are_target_scoped_and_sanitized():
    browser,tools=setup_tools();await tools.open()
    guard=browser.guard;owner=guard.session_states[browser.setup_binding.session]
    guard.frame_event({'method':'Page.navigatedWithinDocument','sessionId':owner.session,
        'params':{'frameId':owner.frame_id,'url':SETUP_URL.split('/campaign/')[0]+'/?PRIVATE_SECRET=1'}})
    assert tools.navigation_evidence['events'][-1]['route']=='TENANT_DASHBOARD'
    assert tools.navigation_evidence['events'][-1]['kind']=='SAME_DOCUMENT_NAVIGATION'
    assert 'PRIVATE_SECRET' not in json.dumps(tools.navigation_evidence)
    count=len(tools.navigation_evidence['events'])
    source=guard.session_states[guard.primary_session]
    guard.frame_event({'method':'Page.navigatedWithinDocument','sessionId':source.session,
        'params':{'frameId':source.frame_id,'url':SETUP_URL}})
    assert len(tools.navigation_evidence['events'])==count


async def test_same_document_redirect_during_form_binding_never_claims_arrival():
    browser,tools=setup_tools();send=browser.guard.send;changed=False
    async def redirect_during_binding(method,params=None,session=None):
        nonlocal changed
        if method=='DOM.getDocument' and browser.setup_binding is not None and not changed:
            changed=True
            browser.page.location=SETUP_URL.split('/campaign/')[0]+'/'
            tools.navigation_observed('SAME_DOCUMENT_NAVIGATION',browser.setup_binding.session,browser.page.location)
        return await send(method,params,session)
    browser.guard.send=redirect_during_binding
    with pytest.raises(SetupStop,match='SETUP_REDIRECTED_TO_DASHBOARD'):await tools.open()
    assert not tools.destination_ready
    assert not any(f['kind']=='WHATSAPP_DESTINATION_OPENED' for f in browser.trace.step_facts)


async def test_owned_cleanup_does_not_erase_last_live_navigation_evidence():
    browser,tools=setup_tools();await tools.open()
    before=json.loads(json.dumps(tools.navigation_evidence))
    browser.close_started=True
    tools.navigation_observed('TARGET_URL_CHANGED',browser.setup_binding.session,'')
    assert tools.navigation_evidence==before


@contextmanager
def navigation_server(mode):
    from evals.local_setup_agent_acceptance import fixture_html
    route='/tenant/admin/index.php/campaign/whatsapp_campaign/create/whatsapp/regular'
    dashboard='/tenant/admin/index.php/'
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.server.paths.append(self.path)
            if self.path==route and mode=='http_dashboard':
                self.send_response(302);self.send_header('Location',dashboard);self.end_headers();return
            html=fixture_html('normal') if self.path==route and mode in {'valid','delayed'} else '<html><body><h1>Local dashboard</h1></body></html>'
            if self.path==route and mode=='history_dashboard':html=html.replace('</body>',f'<script>history.replaceState(null,"",{json.dumps(dashboard)})</script></body>')
            if self.path==route and mode=='client_dashboard':html=html.replace('</body>',f'<script>location.replace({json.dumps(dashboard)})</script></body>')
            if self.path==route and mode=='delayed':
                html=html.replace('<form id="local_form"','<form style="display:none" id="local_form"')
                html=html.replace('</body>','<script>setTimeout(()=>document.getElementById("local_form").style.display="",400)</script></body>')
            body=html.encode();self.send_response(200);self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
        def do_POST(self):self.server.posts+=1;self.send_error(405)
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    server.paths=[];server.posts=0;server.origin='http://127.0.0.1:'+str(server.server_port)
    server.entry=server.origin+route;server.dashboard=server.origin+dashboard
    worker=Thread(target=lambda:server.serve_forever(poll_interval=.05),daemon=True);worker.start()
    try:yield server
    finally:server.shutdown();server.server_close();worker.join(2)


@pytest.mark.skipif(os.getenv('RUN_REAL_WHATSAPP_NAVIGATION')!='1',reason='Explicit local Chromium navigation acceptance')
@pytest.mark.parametrize('mode',['http_dashboard','client_dashboard','history_dashboard','wrong_body','valid','valid_fragment','delayed','late_history'])
async def test_actual_chromium_navigation_and_form_readiness(mode):
    import asyncio
    from app.config import Config,OriginPolicy
    from app.trace import Trace
    from browser.stagehand_client import StagehandClient
    from browser.protected_tabs import create_protected_tab
    from evals.local_browser_baseline import settled
    from modules.whatsapp.tools import SetupTools
    trace=Trace()
    with navigation_server('valid' if mode in {'late_history','valid_fragment'} else mode) as server:
        browser=StagehandClient(Config(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=True,login_url='about:blank',
            expected_host='127.0.0.1',max_actions=150,timeout_seconds=90),OriginPolicy({server.origin}),trace,fixture=True)
        try:
            await browser.start();source=browser.page
            await source.goto('about:blank');await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding;browser.login_page=source;browser.task_phase='setup'
            tools=SetupTools.for_fixture(browser,server.entry,authorized_mutations=True)
            tools.navigation_observed('REQUESTED_SETUP_NAVIGATION',binding.session,server.entry)
            await page.goto(server.entry+('#setup' if mode=='valid_fragment' else ''));await page.wait_for_load_state('domcontentloaded')
            # A client navigation can still be finishing after goto returns.
            if mode=='client_dashboard':
                async with asyncio.timeout(3):
                    while await page.url()!=server.dashboard:await asyncio.sleep(.02)
            await settled(browser,binding.session)
            if mode in {'http_dashboard','client_dashboard','history_dashboard'}:
                with pytest.raises(SetupStop,match='SETUP_REDIRECTED_TO_DASHBOARD'):await tools.open()
                assert tools.navigation_evidence['final_route']=='TENANT_DASHBOARD'
                assert not tools.destination_ready and not tools.browser_operations
                events=tools.navigation_evidence['events']
                if mode=='http_dashboard':assert any(e['http_redirect_request'] for e in events)
                if mode=='history_dashboard':assert any(e['kind']=='SAME_DOCUMENT_NAVIGATION' for e in events)
            else:
                data=await tools.open()
                if mode=='wrong_body':assert not data['destination_ready']
                else:
                    assert data['destination_ready'] and data['action_form_identity_bound']
                    if mode=='late_history':
                        await page.evaluate('history.replaceState(null,"",'+json.dumps(server.dashboard)+')')
                        result=await tools.fill_text(data['fields'][0]['reference'])
                        assert result['error']=='SETUP_REDIRECTED_TO_DASHBOARD'
                        assert not tools.browser_operations and not tools.destination_ready
            assert server.paths.count(server.entry.removeprefix(server.origin))==1
            assert browser.page is page and browser.login_page is source
            assert await source.url()=='about:blank'
            assert {p.page_id for p in await browser.browser.context.pages()}=={source.page_id,page.page_id}
            assert all(e['setup_target_alias']==browser.guard.target_aliases[binding.target]
                for e in tools.navigation_evidence['events'])
            assert server.posts==trace.openai_api_calls==browser.login_submissions==0
            report={'case':mode,'navigation':tools.navigation_evidence,'no_field_operations':not tools.browser_operations,
                'provider_requests':0,'posts':server.posts}
        finally:await browser.close()
        assert trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        report['cleanup']=trace.cleanup_result
        (ROOT/'outputs/phase-b-navigation-repair-20261113'/f'local-{mode}.json').write_text(json.dumps(report,indent=2))
