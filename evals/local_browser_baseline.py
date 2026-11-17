"""Small actual-Chromium baseline acceptance; loopback only and no model calls."""
import asyncio
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from threading import Thread
from time import monotonic

from app.config import Config, OriginPolicy, ROOT
from app.trace import Trace
from browser.protected_tabs import create_protected_tab, bound_tab_observable
from browser.stagehand_client import StagehandClient
from modules.whatsapp.contract import VALUES


def fixture_html(mode):
    # Deliberately no HTML/ARIA form and no WhatsApp heading. These represent
    # the assumptions under investigation, not claims about Netcore's live DOM.
    challenge = '<input autocomplete="one-time-code" aria-label="Verification code">' if mode == 'challenge' else ''
    step = 'Audience' if mode == 'wrong_step' else 'Setup'
    return '''<!doctype html><meta charset="utf-8"><title>Local browser baseline</title>
    <style>body{font:16px sans-serif;margin:24px}label{display:block;margin:16px}
    input,select,button{padding:8px}#choices[hidden]{display:none}</style>
    <h1>Create campaign</h1><h2 aria-current="step">__STEP__</h2>
    <section aria-label="Campaign settings" id="settings">
      <label for="campaign">Campaign name</label><input id="campaign" type="text">
      <label for="waba">WABA ID</label><select id="waba"><option value="__WABA__" selected>__WABA__</option></select>
      <label for="business">Business number</label><select id="business"><option value="" selected>Select</option>
        <option value="approved-local-business">__BUSINESS__</option></select>
      <label for="tags">Add tags</label><input id="tags" type="text" role="combobox" aria-controls="choices" aria-expanded="false">
      <div id="choices" role="listbox" hidden><div id="existing" role="option" data-existing="true" aria-selected="false">test</div></div>
      <button id="save" type="button">Save</button>
    </section>CHALLENGE
    <script>
      history.replaceState(null,'',location.pathname+'#setup');
      if('__MODE__'==='valid'){
        const label=document.querySelector('label[for=campaign]'),input=document.getElementById('campaign');
        const host=document.createElement('div');host.id='campaign_host';label.before(host);
        const root=host.attachShadow({mode:'open'});root.append(label,input);
      }
      window.__fixtureCounts={input:0,change:0,save:0};
      document.addEventListener('input',()=>window.__fixtureCounts.input++);
      document.addEventListener('change',()=>window.__fixtureCounts.change++);
      document.getElementById('save').addEventListener('click',()=>window.__fixtureCounts.save++);
      const tags=document.getElementById('tags'),choices=document.getElementById('choices');
      tags.addEventListener('click',()=>{choices.hidden=false;tags.setAttribute('aria-expanded','true')});
      document.getElementById('existing').addEventListener('click',()=>{
        tags.value='test';document.getElementById('existing').setAttribute('aria-selected','true');
        tags.dispatchEvent(new Event('change',{bubbles:true}));
      });
    </script>'''.replace('__MODE__',mode).replace('__STEP__',step).replace('__WABA__',VALUES['waba']).replace('__BUSINESS__',VALUES['business_number']).replace('CHALLENGE',challenge)


@contextmanager
def fixture_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path != '/regular':
                self.send_error(404); return
            body=fixture_html(self.server.mode).encode('utf-8')
            self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8')
            self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        def do_POST(self):
            self.server.posts+=1; self.send_error(405)
        def log_message(self,*args):pass
    service=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    service.mode='valid'; service.posts=0
    worker=Thread(target=lambda:service.serve_forever(poll_interval=.05),daemon=True); worker.start()
    try:yield service,'http://127.0.0.1:'+str(service.server_port)
    finally:service.shutdown();service.server_close();worker.join(2)


async def settled(browser,session):
    async with asyncio.timeout(5):
        while True:
            browser.guard.lifecycle_changed.clear(); await browser.guard.ensure_observable()
            owner=browser.guard.session_states[session]
            frame=(await browser.guard.send('Page.getFrameTree',session=session))['frameTree']['frame']
            if frame.get('id')==owner.frame_id and (frame.get('loaderId') or None)==owner.loader_id:return
            await browser.guard.lifecycle_changed.wait()


def reference(discovery,field):
    matches=[c['reference'] for c in discovery.get('controls',[]) if c.get('field_hint')==field]
    if len(matches)!=1:raise AssertionError('LOCAL_CONTROL_NOT_UNIQUE')
    return matches[0]


async def facts(page):
    return await page.evaluate('''(values=>({
      campaign_matches:(document.getElementById('campaign_host')?.shadowRoot.getElementById('campaign')||document.getElementById('campaign')).value===values.campaign_name,
      waba_matches:document.getElementById('waba').value===values.waba,
      business_matches:document.getElementById('business').selectedOptions[0]?.textContent===values.business_number,
      tag_matches:document.getElementById('tags').value===values.tags,
      counts:{...window.__fixtureCounts}
    }))(VALUES)'''.replace('VALUES',json.dumps(VALUES)))


async def refused(operation):
    try:
        result=await operation
        return (not result.get('ok',True) or result.get('status') in {'BLOCKED','FAIL'}),result.get('code') or result.get('error')
    except Exception as error:
        return bool(getattr(error,'code',None)),getattr(error,'code',type(error).__name__)


async def accept_browser_baseline(executable,*,output=None):
    from modules.whatsapp.browser_baseline import BrowserBaseline
    report={'scope':'LOCAL_BROWSER_BASELINE_ONLY','headless':False,'live_netcore':False,
        'paid_inference':0,'credential_access':False,'passed':False,'cases':[]}
    browser=None;trace=Trace();started=monotonic()
    with fixture_server() as (server,origin):
        destination=origin+'/regular'
        browser=StagehandClient(Config(str(executable),headless=False,login_url='about:blank',
            expected_host='127.0.0.1',timeout_seconds=120,max_actions=200),OriginPolicy({origin}),trace,fixture=True)
        try:
            await browser.start();original=browser.page;context=browser.browser.context;process=browser.process
            await original.goto('about:blank');await settled(browser,browser.guard.primary_session)
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_binding=binding
            async def reset(mode):
                server.mode=mode;await page.goto(destination);await page.wait_for_load_state('domcontentloaded',timeout=5000)
                await settled(browser,binding.session)
                return BrowserBaseline(browser,destination)

            baseline=await reset('valid');discovered=await baseline.discover()
            campaign=reference(discovered,'campaign_name');before=await facts(page)
            denied,denial=await refused(baseline.fill_campaign(campaign,authorized=False))
            unchanged=before==await facts(page)
            campaign_result=await baseline.fill_campaign(campaign,authorized=True)
            discovered=await baseline.discover()
            mapping={field:reference(discovered,field) for field in ('waba','business_number','tags')}
            remaining=await baseline.complete_remaining(mapping,authorized=True)
            actual=await facts(page)
            same_url=await page.url();await page.locator('#save').click(timeout=2000)
            after_forbidden=await facts(page)
            legacy=discovered.get('legacy_identity')
            report['cases'].append({'case':'campaign_then_four_fields_without_legacy_form_identity',
                'passed':denied and unchanged and campaign_result.get('status')=='CAMPAIGN_NAME_VERIFIED'
                    and remaining.get('status')=='FOUR_FIELDS_VERIFIED_UNSAVED'
                    and all(actual[k] for k in ('campaign_matches','waba_matches','business_matches','tag_matches'))
                    and after_forbidden['counts']['save']==0 and await page.url()==same_url,
                'unapproved_write_refused':denied,'unapproved_stop_code':denial,'unapproved_page_unchanged':unchanged,
                'legacy_identity':legacy,'campaign_verified':campaign_result.get('status')=='CAMPAIGN_NAME_VERIFIED',
                'campaign_in_accessible_open_root':discovered.get('open_roots_inspected')==1,
                'four_fields_verified':remaining.get('status')=='FOUR_FIELDS_VERIFIED_UNSAVED',
                'save_handler_blocked':after_forbidden['counts']['save']==0,
                'no_navigation_after_forbidden_click':await page.url()==same_url})

            baseline=await reset('wrong_step')
            try:
                discovered=await baseline.discover()
                step_refused,step_code=await refused(baseline.fill_campaign(reference(discovered,'campaign_name'),authorized=True))
            except Exception as error:step_refused,step_code=bool(getattr(error,'code',None)),getattr(error,'code',type(error).__name__)
            step_unchanged=(await facts(page))['counts']['input']==0
            baseline=await reset('valid');discovered=await baseline.discover();campaign=reference(discovered,'campaign_name')
            await page.evaluate("history.replaceState(null,'','/unexpected#setup')")
            route_refused,route_code=await refused(baseline.fill_campaign(campaign,authorized=True))
            route_unchanged=(await facts(page))['counts']['input']==0
            report['cases'].append({'case':'wrong_step_and_route_never_write','passed':step_refused and route_refused and step_unchanged and route_unchanged,
                'wrong_step_stop_code':step_code,'wrong_route_stop_code':route_code,'no_field_writes':step_unchanged and route_unchanged})

            baseline=await reset('challenge')
            try:
                discovered=await baseline.discover()
                challenge_refused,challenge_code=await refused(baseline.fill_campaign(reference(discovered,'campaign_name'),authorized=True))
            except Exception as error:challenge_refused,challenge_code=bool(getattr(error,'code',None)),getattr(error,'code',type(error).__name__)
            actual=await facts(page)
            report['cases'].append({'case':'challenge_never_writes','passed':challenge_refused and actual['counts']['input']==0,
                'stop_code':challenge_code,'no_field_writes':actual['counts']['input']==0})
            report['checks']={'guard_healthy':not browser.guard.failed,'correct_tab_protected':bound_tab_observable(browser.guard,binding),
                'original_retained':await original.evaluate('document.documentElement!==null') is True,
                'same_context':browser.browser.context is context,'same_owned_process':browser.process is process,
                'exactly_two_pages':len(await context.pages())==2,'no_automatic_login_submission':browser.login_submissions==0,
                'no_paid_requests':trace.openai_api_calls==0,'no_fixture_posts':server.posts==0}
            report['passed']=all(c['passed'] for c in report['cases']) and all(report['checks'].values())
        except Exception as error:
            report.update(error_category=type(error).__name__,error_code=getattr(error,'code',None))
        finally:
            try:await browser.close()
            except Exception:report['cleanup_exception']=True
            report['cleanup_result']=trace.cleanup_result
            report['passed']=bool(report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED')
            report['seconds']=round(monotonic()-started,3)
            Path(output or ROOT/'outputs/real-local-browser-baseline.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
