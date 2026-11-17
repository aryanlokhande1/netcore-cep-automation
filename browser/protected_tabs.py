"""Explicit same-context tab binding; no target adoption or navigation before protection."""
import asyncio
from dataclasses import dataclass
import json
from uuid import uuid4
from urllib.parse import urlsplit

class TabProtectionError(PermissionError):
    def __init__(self,code):self.code=code;super().__init__(code)

@dataclass(frozen=True)
class ProtectedTab:
    guard:object
    target:str
    session:str
    generation:int
    source_target:str
    source_session:str
    source_generation:int
    source_document_generation:int
    blank_document_generation:int
    blank_frame_id:str
    blank_loader_id:str|None


def bound_tab_observable(guard,binding):
    if binding.guard is not guard or not guard.primary_observable():return False
    source=guard.session_states.get(binding.source_session)
    owner=guard.session_states.get(binding.session)
    return bool(source and owner and source.target==binding.source_target and source.generation==binding.source_generation
        and source.document_generation==binding.source_document_generation and guard.primary_session==source.session
        and guard.current_sessions.get(binding.target)==binding.session and owner.generation==binding.generation
        and binding.session in guard.explicit_page_roots and guard.session_observable(binding.session))


async def marker_matches(page,guard,session):
    # DOM attributes are visible across isolated worlds. Only our random blank/local
    # marker is inspected; no page text, account, cookies or state is retrieved.
    key='data-netcore-tab-binding-'+uuid4().hex;value=uuid4().hex
    written=False
    try:
        await page.evaluate('(() => {document.documentElement.setAttribute(KEY,VALUE);return true})()'.replace('KEY',json.dumps(key)).replace('VALUE',json.dumps(value)))
        written=True
        response=await guard.send('Runtime.evaluate',{'expression':
            'document.documentElement.getAttribute('+json.dumps(key)+')==='+json.dumps(value),'returnByValue':True},session)
        return response.get('result',{}).get('value') is True
    finally:
        if written:await page.evaluate('(() => {document.documentElement.removeAttribute('+json.dumps(key)+');return true})()')


def url_category(url):
    if url=='about:blank':return 'ABOUT_BLANK'
    if not url:return 'EMPTY'
    scheme=urlsplit(url).scheme
    if scheme=='chrome-extension':return 'LOCAL_EXTENSION_DOCUMENT'
    if scheme=='about':return 'OTHER_ABOUT_DOCUMENT'
    return 'UNEXPECTED_DOCUMENT'


def tab_diagnostic(browser,state,**facts):
    browser.tab_binding_stage=state
    browser.trace.lifecycle('TAB_BINDING',{'state':state,**facts})


async def create_protected_tab(browser):
    try:return await _create_protected_tab(browser)
    except Exception as error:
        stage=getattr(browser,'tab_binding_stage','SOURCE_VALIDATION')
        code=error.code if isinstance(error,TabProtectionError) else 'TAB_READINESS_TIMEOUT' if isinstance(error,TimeoutError) else 'TAB_BINDING_OPERATION_FAILED'
        tab_diagnostic(browser,'TAB_BINDING_FAILED',stage=stage,code=code)
        raise


async def _create_protected_tab(browser):
    guard=browser.guard;context=browser.browser.context;original=browser.page
    await guard.ensure_observable()
    source=guard.session_states.get(guard.primary_session)
    if not source or not guard.session_observable(source.session):raise TabProtectionError('SOURCE_TARGET_UNAVAILABLE')
    source_state=(source.target,source.session,source.generation,source.document_generation)
    if not await marker_matches(original,guard,source.session):raise TabProtectionError('SOURCE_PAGE_BINDING_UNPROVEN')
    pages=await context.pages()
    known_page_ids={p.page_id for p in pages}
    if original.page_id not in known_page_ids:raise TabProtectionError('SOURCE_CONTEXT_MEMBERSHIP_UNPROVEN')
    before=await guard.send('Target.getTargets')
    targets={t['targetId']:t for t in before['targetInfos'] if t['type']=='page'}
    if source.target not in targets:raise TabProtectionError('SOURCE_TARGET_UNAVAILABLE')
    source_context=targets[source.target].get('browserContextId')
    # Installed Stagehand uses its own extension blank when no URL is supplied.
    # Request the local about document explicitly; never accept the SDK bootstrap
    # (or another extension/origin) as the authorized blank page.
    page=await asyncio.wait_for(context.new_page('about:blank'),5)
    returned_url=await page.url()
    tab_diagnostic(browser,'TAB_CREATED',returned_new_reference=page.page_id not in known_page_ids,
        url_category=url_category(returned_url))
    if page.page_id in known_page_ids:raise TabProtectionError('SDK_RETURNED_EXISTING_PAGE')
    after_pages=await context.pages()
    if not {original.page_id,page.page_id}<={p.page_id for p in after_pages}:raise TabProtectionError('SAME_CONTEXT_MEMBERSHIP_UNPROVEN')
    attached_recorded=fetch_recorded=False
    candidate_state=None
    async with asyncio.timeout(5):
        while True:
            # Clear before reading the predicate. Signals are notifications only;
            # exact context, generation, interception and frame facts prove readiness.
            guard.lifecycle_changed.clear()
            await guard.ensure_observable()
            current=await guard.send('Target.getTargets')
            new=[t for t in current['targetInfos'] if t['type']=='page' and t['targetId'] not in targets]
            if len(new)>1:raise TabProtectionError('AMBIGUOUS_NEW_PAGE_TARGET')
            if new:
                target=new[0]
                if target.get('browserContextId')!=source_context:raise TabProtectionError('NEW_TARGET_CONTEXT_OR_DOCUMENT_MISMATCH')
                if url_category(target.get('url',''))=='UNEXPECTED_DOCUMENT':raise TabProtectionError('NEW_TARGET_CONTEXT_OR_DOCUMENT_MISMATCH')
                owner=guard.session_states.get(guard.current_sessions.get(target['targetId']))
                if owner:
                    state=(owner.target,owner.session,owner.generation)
                    if candidate_state is None:candidate_state=state
                    elif candidate_state!=state:raise TabProtectionError('NEW_TARGET_GENERATION_CHANGED')
                    if not owner.attached or owner.destroyed or owner.crashed:raise TabProtectionError('NEW_TARGET_PROTECTION_UNAVAILABLE')
                    if not attached_recorded:
                        tab_diagnostic(browser,'TARGET_ATTACHED',generation=owner.generation);attached_recorded=True
                    if owner.intercepting and not fetch_recorded:
                        tab_diagnostic(browser,'FETCH_PROTECTED',generation=owner.generation);fetch_recorded=True
                    if guard.session_observable(owner.session):
                        tree=await guard.send('Page.getFrameTree',session=owner.session)
                        frame=tree.get('frameTree',{}).get('frame',{})
                        coherent=(frame.get('id')==owner.frame_id and (frame.get('loaderId') or None)==owner.loader_id)
                        if (coherent and frame.get('url')=='about:blank' and target.get('url')=='about:blank'
                                and await page.url()=='about:blank'):
                            break
            await guard.lifecycle_changed.wait()
    if source_state!=(source.target,source.session,source.generation,source.document_generation):raise TabProtectionError('SOURCE_DOCUMENT_CHANGED_DURING_TAB_CREATION')
    blank_state=(owner.document_generation,owner.frame_id,owner.loader_id)
    if not await marker_matches(page,guard,owner.session):raise TabProtectionError('NEW_PAGE_TARGET_BINDING_UNPROVEN')
    if blank_state!=(owner.document_generation,owner.frame_id,owner.loader_id) or await page.url()!='about:blank':raise TabProtectionError('NEW_PAGE_CHANGED_BEFORE_ADMISSION')
    binding=ProtectedTab(guard,owner.target,owner.session,owner.generation,*source_state,*blank_state)
    # Admission is explicit and generation-bound. The original primary stays primary;
    # no automatic recovery, primary swap, extra context, process or profile occurs.
    guard.admit_protected_tab(binding)
    if not bound_tab_observable(guard,binding):raise TabProtectionError('NEW_TARGET_PROTECTION_UNAVAILABLE')
    tab_diagnostic(browser,'PAGE_BOUND',generation=binding.generation,source_retained=True)
    await context.set_active_page(page)
    await guard.send('Target.activateTarget',{'targetId':owner.target})
    if not bound_tab_observable(guard,binding) or blank_state!=(owner.document_generation,owner.frame_id,owner.loader_id) or await page.url()!='about:blank':raise TabProtectionError('NEW_TARGET_PROTECTION_UNAVAILABLE')
    return page,binding
