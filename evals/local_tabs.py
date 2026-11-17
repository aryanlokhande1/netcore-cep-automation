"""Real local Chromium acceptance; only local about documents and SDK bootstrap.

No dotenv, credentials, inference, HTTP fixtures, Netcore or campaign actions.
"""
import asyncio
import json
from time import monotonic
from pathlib import Path
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from browser.protected_tabs import create_protected_tab,bound_tab_observable

async def local_tab_acceptance(executable,*,headless=False,output=None):
    executable=Path(executable)
    if not executable.is_file():raise ValueError('Installed Chromium executable required')
    trace=Trace();trace.reasoner_mode='LOCAL_PROTECTED_TAB_ACCEPTANCE_NO_MODEL'
    # The fixture capability allows only loopback. No fixture server is started or used.
    config=Config(str(executable),headless=headless,login_url='about:blank',expected_host='127.0.0.1',timeout_seconds=90)
    browser=StagehandClient(config,OriginPolicy({'http://127.0.0.1:9000'}),trace,fixture=True)
    report={'scope':'LOCAL_ABOUT_TABS_ONLY','headless':headless,'paid_inference':0,'credential_access':False,
        'netcore_navigation_attempts':0,'passed':False}
    binding=None;started=monotonic()
    try:
        await browser.start();report['browser_started']=True
        original=browser.page;context=browser.browser.context;process=browser.process;profile=browser.profile
        # SDK uses an internal extension bootstrap. The only requested document URL
        # here or in the production creation helper is the explicit local about page.
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
        primary=browser.guard.primary_session
        page,binding=await create_protected_tab(browser)
        checks={
            'same_context':browser.browser.context is context,'same_owned_process':browser.process is process,
            'same_profile':browser.profile==profile,'original_primary_retained':browser.guard.primary_session==primary,
            'new_reference':page.page_id!=original.page_id,'new_target_bound':bound_tab_observable(browser.guard,binding),
            'original_protected':browser.guard.session_observable(primary),
            'no_premature_close':not browser.close_started and process.status_now()=='ALIVE',
            'guard_healthy':not browser.guard.failed,'zero_model_calls':trace.model_calls==trace.openai_api_calls==0}
        pages=await context.pages()
        checks['exactly_two_same_context_pages']=len(pages)==2 and {p.page_id for p in pages}=={original.page_id,page.page_id}
        checks['both_pages_about_blank']=all([await p.url()=='about:blank' for p in pages])
        checks['both_dom_observable']=await original.evaluate('document.documentElement!==null') is True and await page.evaluate('document.documentElement!==null') is True
        # Make the production source/new-target marker proof and Fetch-before-resume
        # ordering reviewable using numeric aliases and measured timing only.
        alias=browser.guard.session_aliases[binding.session]
        events=[e for e in browser.guard.lifecycle if e.get('session')==alias]
        complete=[e for e in events if e['state']=='COMMAND_COMPLETED']
        fetch=next(e for e in complete if e.get('command')=='Fetch.enable')
        resume=next(e for e in complete if e.get('command')=='Runtime.runIfWaitingForDebugger')
        protected=next(e for e in events if e['state']=='TARGET_PROTECTED')
        bound=next(e for e in browser.guard.lifecycle if e['state']=='EXPLICIT_PAGE_ROOT_ADMITTED')
        checks['fetch_before_resume']=fetch['elapsed_ms']<=resume['elapsed_ms']
        checks['protected_before_binding']=protected['elapsed_ms']<=bound['elapsed_ms']
        report['checks']=checks
        report['timing']={'fetch_completed_ms':fetch['elapsed_ms'],'debugger_resume_completed_ms':resume['elapsed_ms'],
            'target_protected_ms':protected['elapsed_ms'],'page_bound_ms':bound['elapsed_ms']}
        report['passed']=all(checks.values())
    except Exception as error:
        report.update(error_category=type(error).__name__,error_code=getattr(error,'code',None))
    finally:
        try:await browser.close()
        except Exception:report['cleanup_exception']=True
        report['cleanup_result']=trace.cleanup_result;report['cleanup_diagnostic']=trace.cleanup_diagnostic
        report['tab_stages']=[e['diagnostic'] for e in trace.events if e.get('diagnostic_stage')=='TAB_BINDING']
        report['guard_lifecycle']=list(browser.guard.lifecycle) if browser.guard else []
        report['duration_seconds']=round(monotonic()-started,3)
        report['passed']=report['passed'] and trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED'
        path=Path(output or ROOT/'outputs'/'real-local-tab-acceptance.json')
        path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report
