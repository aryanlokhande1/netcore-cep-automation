"""Bounded visual inspection of a blocked Setup task. No resume or browser actions."""
import asyncio
import sys
from time import monotonic
from browser.protected_tabs import bound_tab_observable


def sanitized_field_state(data):
    """Retain structural evidence only, never values, selectors or page text."""
    from modules.whatsapp.contract import ORDER
    flags=('discovered','selector_unique','ownership_verified','same_form','visible','enabled',
        'selected','has_other_tags','expanded','unobscured','option_visible','option_enabled')
    fields={}
    for field in ORDER:
        source=data.get('fields',{}).get(field,{})
        fields[field]={key:source[key] for key in flags if type(source.get(key)) is bool}
        fields[field]['kind']=source.get('kind') if source.get('kind') in {'TEXT_INPUT','NATIVE_SELECT','OWNED_COMBOBOX','UNSUPPORTED'} else 'UNRESOLVED'
        count=source.get('exact_option_count')
        if type(count) is int and 0<=count<=2000:fields[field]['exact_option_count']=count
    result={'fields':fields,**{key:data.get(key) is True for key in
        ('form_recognized','setup_active','page_identity','observation_complete','same_form')}}
    result['identity_basis']=data.get('identity_basis') if data.get('identity_basis') in {
        'UNESTABLISHED','WHATSAPP_TEXT_AND_SETUP','SCOPED_PAGE1_FORM_SIGNATURE'} else 'UNREPORTED_BY_OBSERVER'
    result['identity_diagnostics']={k:v for k,v in (data.get('identity_diagnostics') or {}).items()
        if k in {'whatsapp_text_observed','setup_active','form_signature','same_form_field_count','distinct_field_controls'} and type(v) in {bool,int}}
    result['active_step']=data.get('active_step') if data.get('active_step') in {'SETUP','AUDIENCE','CONTENT','SCHEDULE','UNKNOWN'} else 'UNREPORTED'
    result['observation_counts']={k:v for k,v in (data.get('observation_counts') or {}).items()
        if k in {'nodes_inspected','visible_controls','open_shadow_hosts_observed','traversal_limit_reached'} and type(v) in {bool,int}}
    return result


async def capture_setup_failure(browser,tools,directory=None,*,usage=None,failure=None):
    """Bounded pre-cleanup evidence, even after the normal action budget closes."""
    import json
    from pathlib import Path
    from app.config import ROOT
    from browser.observed_control import protected_document
    from modules.whatsapp.destination import classify_setup_page
    from modules.whatsapp.observation import observe_setup
    from modules.whatsapp.learning_loop import redacted_capture
    evidence={'capture_phase':'BEFORE_BROWSER_CLEANUP','dom_status':'UNAVAILABLE',
        'screenshot':{'status':'SCREENSHOT_SKIPPED_PRIVACY_OR_TRANSPORT','remote_image_supplied':False},
        'execution':tools.execution_summary() if tools else None,'usage':usage,'failure':failure,
        'navigation':tools.navigation_evidence if tools else None}
    async def protect():
        if not tools or not protected_for_inspection(browser):raise RuntimeError('CAPTURE_PROTECTION_UNAVAILABLE')
        classify_setup_page(await browser.page.url(),tools.destination)
        return protected_document(browser)
    try:
        async with asyncio.timeout(6):
            await protect()
            directory=Path(directory or ROOT/'outputs'/('whatsapp-page1-'+browser.trace.run_id))
            directory.mkdir(parents=True,exist_ok=True)
            before=await protect()
            data=await observe_setup(browser)
            if before!=await protect():raise RuntimeError('CAPTURE_DOCUMENT_CHANGED')
            evidence.update(dom_status='FRESH_SANITIZED_STRUCTURE',dom=sanitized_field_state(data))
            evidence['snapshot']={'observer':'observe_setup','phase':'FRESH_FAILURE_CAPTURE',
                'observed_at':round(monotonic()-tools.started,6),
                'after_tool_snapshot':tools.snapshot,
                'last_tool_action_form_identity_bound':tools.form_identity is not None}
            evidence['screenshot']=await redacted_capture(tools,directory,'failure',protect=protect)
            # Full-frame redaction contains no diagnostic visual content.
            evidence['screenshot']['visual_content_available']=False
            (directory/'failure-evidence.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
            evidence['artifact_directory']=str(directory.relative_to(ROOT)) if directory.is_relative_to(ROOT) else str(directory)
    except Exception:
        evidence['capture_gap']='PROTECTION_TRANSPORT_DEADLINE_OR_STORAGE_UNAVAILABLE'
    browser.trace.lifecycle('WHATSAPP',{'state':'FAILURE_EVIDENCE_CAPTURED','dom_status':evidence['dom_status'],
        'screenshot_status':evidence['screenshot']['status']})
    return evidence


def protected_for_inspection(browser):
    guard=browser.guard
    return bool(guard and not guard.failed and not guard.closing and not browser.close_started
        and not guard.blocking_hosts and browser.setup_binding is not None
        and bound_tab_observable(guard,browser.setup_binding))


async def inspect_failed_setup(browser,result,limits,*,hold=None):
    from browser.inspection import inspection_hold
    state={'status':'NOT_ENTERED','read_only':True,'resume_allowed':False,'stop_reason':result['setup_stop_reason']}
    result['failure_inspection']=state
    if browser.task_phase!='setup' or result.get('poc_checkpoint')!='HUMAN_CONFIRMED_FOR_POC':
        state['status']='SKIPPED_NOT_SETUP_PREFLIGHT';return
    if not protected_for_inspection(browser):state['status']='SKIPPED_PROTECTION_UNAVAILABLE';return
    if hold is None and not sys.stdin.isatty():state['status']='SKIPPED_NONINTERACTIVE';return
    remaining=int(browser.config.timeout_seconds-(monotonic()-browser.started))
    seconds=min(limits.hold_seconds,remaining)
    if seconds<1:state['status']='SKIPPED_SESSION_DEADLINE';return
    # Zero action allowance closes all automation, including reads routed through
    # ordinary tools. Passive CDP interception remains active throughout the hold.
    browser.begin_phase('inspection',min(seconds+10,310),0)
    state.update(status='ENTERED',seconds=seconds)
    browser.trace.lifecycle('WHATSAPP',{'state':'FAILURE_INSPECTION_STARTED','code':result['setup_stop_reason']})
    print('Setup stopped: phase='+result.get('setup_failure_phase','UNRECORDED')+' reason='+result['setup_stop_reason']+'. Read-only failure inspection for up to '+str(seconds)+
        ' seconds. Inspect the page and sanitized predicate diagnostic in this terminal; do not interact with the page. Enter closes. No login or Setup action will resume.',flush=True)
    if result.get('setup_failure_diagnostic'):
        import json
        print(json.dumps(result['setup_failure_diagnostic']),flush=True)
    async def protection_watch():
        while True:
            browser.guard.lifecycle_changed.clear()
            if not protected_for_inspection(browser):return
            await browser.guard.lifecycle_changed.wait()
    operator=asyncio.create_task((hold or inspection_hold)(seconds))
    watcher=asyncio.create_task(protection_watch())
    try:
        done,_=await asyncio.wait({operator,watcher},timeout=seconds,return_when=asyncio.FIRST_COMPLETED)
        if watcher in done or not protected_for_inspection(browser):state['status']='CLOSED_PROTECTION_LOST'
        elif operator in done:
            try:operator.result();state['status']='OPERATOR_CLOSED'
            except TimeoutError:state['status']='DEADLINE_REACHED'
            except Exception:state['status']='HOLD_UNAVAILABLE'
        else:state['status']='DEADLINE_REACHED'
    except (KeyboardInterrupt,asyncio.CancelledError):
        state['status']='OPERATOR_CANCELLED';result['error']='OPERATOR_CANCELLED'
    finally:
        for task in (operator,watcher):
            if not task.done():task.cancel()
        await asyncio.gather(operator,watcher,return_exceptions=True)
        browser.trace.lifecycle('WHATSAPP',{'state':'FAILURE_INSPECTION_COMPLETED','code':state['status']})
