"""Actual Stagehand/Chromium safety acceptance on static local about fixtures only.

No dotenv, credentials, network fixture server, Netcore or model. The caller supplies
the production snapshot reader so DOM scanning and closed-root inventory are real.
"""
import asyncio
import json
from pathlib import Path
from time import monotonic
from importlib.metadata import version
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab,bound_tab_observable
from modules.whatsapp.safety import safety_diagnostic,NEGATIVES


FIXTURE = r'''(kind => {
 document.body.replaceChildren();
 const create=(parent,mode='open')=>{
   const host=document.createElement('div');
   host.style.cssText='display:block;min-height:8px';parent.append(host);
   return host.attachShadow({mode});
 };
 if(kind==='fourteen_open'){
   for(let i=0;i<14;i++){
     const root=create(document.body);const span=document.createElement('span');
     span.textContent='Local fixture';root.append(span);
   }
 }else if(kind==='closed_password'){
   const root=create(document.body,'closed');const input=document.createElement('input');
   input.type='password';root.append(input);
 }else if(kind==='nested_closed'){
   const outer=create(document.body);const root=create(outer,'closed');
   const input=document.createElement('input');input.type='password';root.append(input);
 }else{
   const outer=create(document.body);const inner=create(outer);
   const element=document.createElement(kind==='credential_form'||kind==='challenge'||kind==='mixed_negative_closed'?'input':'div');
   if(kind==='credential_form'||kind==='mixed_negative_closed')element.type='password';
   else if(kind==='challenge')element.autocomplete='one-time-code';
   else{
     element.setAttribute('role','alert');
     element.textContent={rejected:'Invalid credentials',expired:'Session expired',account_restricted:'Account locked'}[kind];
   }
   inner.append(element);
   if(kind==='mixed_negative_closed'){
     const closed=create(document.body,'closed');closed.append(document.createElement('span'));
   }
 }
 return true;
})(KIND)'''


def sanitized_error_types(error):
    """Only class/OS codes; no exception text, command params or raw protocol data."""
    pending=[error];out=[];seen=set()
    while pending and len(out)<8:
        current=pending.pop(0)
        if id(current) in seen:continue
        seen.add(id(current))
        out.append({'type':type(current).__name__,'errno':getattr(current,'errno',None),
            'winerror':getattr(current,'winerror',None)})
        pending.extend(getattr(current,'exceptions',()))
        if current.__cause__ is not None:pending.append(current.__cause__)
        elif current.__context__ is not None:pending.append(current.__context__)
    return out


async def local_shadow_acceptance(executable,*,snapshot_reader,headless=False,output=None):
    executable=Path(executable)
    if not executable.is_file():raise ValueError('Installed Chromium executable required')
    trace=Trace();trace.reasoner_mode='LOCAL_SHADOW_SAFETY_ACCEPTANCE_NO_MODEL'
    config=Config(str(executable),headless=headless,login_url='about:blank',expected_host='127.0.0.1',
        timeout_seconds=180,max_actions=100)
    browser=StagehandClient(config,OriginPolicy({'http://127.0.0.1:9000'}),trace,fixture=True)
    report={'scope':'LOCAL_ABOUT_SHADOW_FIXTURES_ONLY','headless':headless,'paid_inference':0,
        'credential_access':False,'netcore_navigation_attempts':0,'passed':False,'cases':[]}
    started=monotonic();stage='BROWSER_START'
    try:
        await browser.start();report['browser_started']=True
        report['chromium_product']=(await browser.guard.send('Browser.getVersion'))['product']
        report['stagehand_version']=version('stagehand');stage='PROTECTED_ABOUT_TABS'
        original=browser.page;context=browser.browser.context;process=browser.process
        await original.goto('about:blank')
        async with asyncio.timeout(5):
            while True:
                browser.guard.lifecycle_changed.clear()
                await browser.guard.ensure_observable()
                source=browser.guard.session_states[browser.guard.primary_session]
                tree=await browser.guard.send('Page.getFrameTree',session=source.session)
                frame=tree['frameTree']['frame']
                if (frame.get('url')=='about:blank' and frame.get('id')==source.frame_id
                    and (frame.get('loaderId') or None)==source.loader_id):break
                await browser.guard.lifecycle_changed.wait()
        page,binding=await create_protected_tab(browser)
        browser.page=page;browser.setup_binding=binding
        for name in ['fourteen_open',*NEGATIVES,'closed_password','nested_closed','mixed_negative_closed']:
            stage='FIXTURE_'+name.upper()
            await browser.guard.ensure_observable()
            if not bound_tab_observable(browser.guard,binding):raise RuntimeError('LOCAL_TAB_PROTECTION_LOST')
            await page.evaluate(FIXTURE.replace('KIND',json.dumps(name)))
            snapshot=await snapshot_reader(browser)
            diagnostic=safety_diagnostic(snapshot)
            flags=diagnostic.get('negative_flags',{})
            counts=diagnostic.get('scope_reason_counts',{})
            if name=='fourteen_open':
                expected=(diagnostic['code'] is None and not any(flags.values()) and snapshot['negative_scope_complete'] is True
                    and counts.get('open_shadow_roots_inspected')==14 and counts.get('inaccessible_shadow_roots')==0
                    and counts.get('remaining_coverage_gaps')==0)
            elif name in NEGATIVES:
                expected=(diagnostic['code']=='AUTHENTICATION_OR_SECURITY_STOP' and flags.get(name) is True and snapshot['negative_scope_complete'] is True
                    and counts.get('open_shadow_roots_inspected')==2 and counts.get('inaccessible_shadow_roots')==0)
            elif name=='mixed_negative_closed':
                expected=(diagnostic['code']=='AUTHENTICATION_OR_SECURITY_STOP' and flags.get('credential_form') is True and snapshot['negative_scope_complete'] is False
                    and counts.get('inaccessible_shadow_roots')==1 and counts.get('remaining_coverage_gaps',0)>0)
            else:
                expected=(diagnostic['code']=='SAFETY_OBSERVATION_INCOMPLETE' and not any(flags.values()) and snapshot['negative_scope_complete'] is False
                    and counts.get('inaccessible_shadow_roots')==1 and counts.get('remaining_coverage_gaps',0)>0)
            report['cases'].append({'fixture':name,'passed':expected,'code':diagnostic['code'],
                'negative_flags':flags,'negative_scope_complete':snapshot.get('negative_scope_complete'),
                'scope_reason_counts':counts})
        stage='FINAL_OWNERSHIP_CHECK';pages=await context.pages()
        checks={'all_fixtures_passed':all(c['passed'] for c in report['cases']),
            'same_owned_process':browser.process is process,'same_context':browser.browser.context is context,
            'exactly_two_same_context_pages':len(pages)==2 and {p.page_id for p in pages}=={original.page_id,page.page_id},
            'original_available':await original.evaluate('document.documentElement!==null') is True,
            'setup_tab_protected':bound_tab_observable(browser.guard,binding),'guard_healthy':not browser.guard.failed,
            'no_premature_close':not browser.close_started and process.status_now()=='ALIVE',
            'zero_login_submissions':browser.login_submissions==0,'zero_model_calls':trace.model_calls==trace.openai_api_calls==0}
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
        report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        path=Path(output or ROOT/'outputs'/'real-local-shadow-safety.json')
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
