"""Actual local Chromium rendering/readiness cases. No Login or model calls."""
import asyncio
import json
from app.config import Config,OriginPolicy
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server
from modules.login.discovery import DOM_DISCOVERY,POSITIVE_DOM


async def local_rendering_acceptance(executable,*,headless=True,compare=False):
    trace=Trace();trace.reasoner_mode='REAL_LOCAL_RENDERING_NO_PROVIDER'
    report={'scope':'REAL_LOCAL_CHROMIUM_ONLY','model_requests':0,'netcore_requests':0,'cases':[]}
    with fixture_server() as server:
        browser=StagehandClient(Config(str(executable),headless=headless,login_url=server.base+'/',
            expected_host='127.0.0.1',max_actions=300,timeout_seconds=120,
            login_poll_seconds=.03,login_stable_seconds=.08,login_ready_seconds=2,login_ready_max_seconds=3),
            OriginPolicy({server.base}),trace,fixture=True)
        browser.rendering_diagnostics_enabled=True
        try:
            await browser.start();await browser.navigate(server.base+'/')
            await browser.page.evaluate('''(() => {
                document.body.innerHTML='<main id="app" style="display:contents"><h1 id="heading">Local shell</h1><button>Local action</button><span id="tenant">LOCAL_TENANT</span></main>';
                document.querySelector('#app').style.display='contents';
                return true;
            })()''')
            settings={'origins':[server.base],'account':'LOCAL_TENANT','dashboard_selector':'#app',
                'tenant_selector':'#tenant','dashboard_frame':[],'tenant_frame':[],
                'dashboard_shadow':[],'tenant_shadow':[]}
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            actual=await browser.page.evaluate('''(() => {const e=document.querySelector('#app'),b=e.querySelector('button'),r=b.getBoundingClientRect();return {
                display:getComputedStyle(e).display,own_rects:e.getClientRects().length,
                child_area:r.width*r.height,child_visibility:getComputedStyle(b).visibility};})()''')
            positive=await browser.page.evaluate(POSITIVE_DOM.replace('SETTINGS',json.dumps(settings)))
            report['cases'].append({'case':'display_contents_shell','browser_geometry':actual,
                'candidate_detected':any(c['selector']=='#app' for c in dom['dashboard_candidates']),
                'independent_dashboard':positive['dashboard'],'passed':positive['dashboard']})
            if compare:
                from browser.diagnostics import ScreenshotDiagnostics
                report['render_comparison']=await ScreenshotDiagnostics(browser).compare(dom,{'inaccessible_shadow_roots':0,'closed_root_ownership':[]},enabled=True)
            await browser.page.evaluate('''(() => {
                const main=document.querySelector('#app');main.style.display='block';main.style.opacity='0';
                return true;
            })()''')
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            report['cases'].append({'case':'opacity_zero_ancestor','passed':not dom['dashboard_candidates'] and not dom['tenant_candidates']})
            await browser.page.evaluate('''(() => {const e=document.createElement('input');e.autocomplete='one-time-code';document.querySelector('#app').append(e);return true;})()''')
            security=await browser.readiness_sample()
            report['cases'].append({'case':'conservative_security_negative_retained','passed':security.get('challenge') is True})
            await browser.page.evaluate('''(() => {document.querySelector('#app').style.opacity='1';document.querySelector('#app').style.transform='scale(0)';return true;})()''')
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            report['cases'].append({'case':'zero_transformed_area','passed':not dom['dashboard_candidates'] and not dom['tenant_candidates']})
            await browser.page.evaluate('''(() => {
                document.body.innerHTML='<div id="slot-host"><button id="slotted">LOCAL_TENANT</button></div>';
                const s=document.querySelector('#slot-host').attachShadow({mode:'open'});s.innerHTML='<main><slot></slot></main>';
                s.querySelector('main').style.opacity='0';return true;})()''')
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            geometry=await browser.page.evaluate('''(() => {const e=document.querySelector('#slotted');return {assigned_slot:!!e.assignedSlot,slot_parent_opacity:getComputedStyle(e.assignedSlot.parentElement).opacity,check_visibility:e.checkVisibility({checkOpacity:true})};})()''')
            report['cases'].append({'case':'slotted_composed_opacity','browser_geometry':geometry,
                'passed':not any(c['selector']=='#slotted' for c in dom['tenant_candidates'])})
            await browser.page.evaluate('''(() => {
                document.body.innerHTML='<main id="hidden" style="display:none"><h1>Hidden</h1><button>Hidden</button></main><div role="dialog"><nav><button>Portal action</button></nav><h1>Transient</h1></div><div id="host"></div>';
                document.querySelector('#hidden').style.display='none';
                const s=document.querySelector('#host').attachShadow({mode:'open'});s.innerHTML='<main id="shadow-app"><button>Visible action</button></main>';return true;
            })()''')
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            report['cases'].append({'case':'hidden_portal_open_shadow','passed':
                not any(c['selector']=='#hidden' for c in dom['dashboard_candidates']) and
                any(c['selector']=='#shadow-app' and c['shadow_selectors'] for c in dom['dashboard_candidates']) and
                bool(dom['dom_diagnostics']['rejections']['dashboard'].get('TRANSIENT_OR_HIDDEN_SCOPE'))})
            mismatch=await browser.page.evaluate(POSITIVE_DOM.replace('SETTINGS',json.dumps({**settings,'account':'OTHER_TENANT','dashboard_selector':'#shadow-app','dashboard_shadow':['#host']})))
            report['cases'].append({'case':'exact_tenant_not_invented','passed':not mismatch['tenant_matches']})
            await browser.page.evaluate('''(() => {document.body.innerHTML='<div id="closed-host"></div>';const root=document.querySelector('#closed-host').attachShadow({mode:'closed'});root.append(document.createElement('span'));return true;})()''')
            from browser.metadata_channel import metadata_channel
            from browser.shadow_inventory import shadow_inventory
            from browser.diagnostics import closed_host_render_state,ScreenshotDiagnostics
            session=browser.guard.primary_session;owner=browser.guard.session_states[session]
            async def host_observer(backend):return await closed_host_render_state(browser,backend)
            async with metadata_channel(browser,session) as reader:
                inventory=await shadow_inventory(reader,session,browser.policy.origins,primary_frame=owner.frame_id,host_observer=host_observer)
            dom=await browser.page.evaluate(DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings)))
            closed_comparison=await ScreenshotDiagnostics(browser).compare(dom,inventory,enabled=True)
            roots=inventory.get('closed_root_ownership',[])
            report['cases'].append({'case':'closed_host_geometry_no_exemption','ownership':roots,
                'passed':inventory['inaccessible_shadow_roots']==1 and roots[0].get('host_rendering',{}).get('status')=='OBSERVED' and
                    roots[0]['security_coverage']=='UNKNOWN_NO_EXEMPTION' and closed_comparison['screenshot_status']=='SKIPPED_PRIVACY_INACCESSIBLE_SCOPE'})
            await browser.page.evaluate('''(() => {
                document.body.innerHTML='<div id="filler"></div><main id="late" style="display:none"><button>Hydrated</button></main>';
                document.querySelector('#filler').append(...Array.from({length:900},()=>document.createElement('div')));
                document.querySelector('#late').style.display='none';
                setTimeout(()=>document.querySelector('#late').style.display='block',100);return true;
            })()''')
            result=await browser.wait_for_application_ready()
            report['cases'].append({'case':'deep_delayed_hydration','state':result,'passed':result['state']=='APPLICATION_READY_AUTH_UNVERIFIED' and result.get('scan_budget',0)>512})
            # Current-document identity must be renewed after actual navigation.
            await browser.navigate(server.base+'/');sample=await browser.readiness_sample()
            report['cases'].append({'case':'navigation_scan_reset','passed':sample['scan_budget']==512})
            report['passed']=all(c['passed'] for c in report['cases']) and not browser.guard.failed
        except Exception as error:report.update(passed=False,exception_class=type(error).__name__)
        finally:
            await browser.close();report['cleanup']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
    return report
