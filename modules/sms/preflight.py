"""Check the approved SMS workflow prerequisites on the protected Login tab."""

import asyncio
from datetime import datetime, timezone
from time import monotonic
from types import SimpleNamespace
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from langchain_core.tools import StructuredTool

from app.browser_mcp import EmptyArguments
from app.browser_mcp_client import StepTools, open_step
from browser.discovery import login_page_context
from browser.inspection import inspection_hold
from browser.operator_console import OperatorConsole
from browser.protected_tabs import TabProtectionError
from modules.sms.contract import CAMPAIGN_NAME, CREATE_URL, DUPLICATE_CHECK_UNAVAILABLE, SCHEDULE, TIMEZONE
from modules.whatsapp.browser_baseline import BaselineStop, reject_security_negatives
from modules.whatsapp.campaign_list_diagnostic import CampaignListDiagnostic, safe_request_path
from modules.whatsapp.contract import V2Stop
from modules.whatsapp.mcp_steps import login_document
from modules.whatsapp.observation import V2_LABEL_HELPERS
from modules.whatsapp.reconciliation import reconcile_campaign


SETUP_DESCRIPTION = r'''
const describeSmsSetup = () => {
 const visible = node => node.getClientRects().length > 0 &&
   getComputedStyle(node).visibility === 'visible' && !node.closest('[aria-hidden=true],[inert]');
 const label = node => fieldLabel(node, 'dom').replace(/[\s:*]+$/g, '').toLowerCase();
 const names = [...document.querySelectorAll('input')].filter(node =>
   node.type === 'text' && visible(node) && label(node) === 'campaign name');
 const cues = [...document.querySelectorAll('h1,h2,h3,h4,[role=heading],[aria-current],[aria-selected],li,button,a')];
 const text = node => (node.innerText || '').trim().toLowerCase();
 const active = node => [node,node.parentElement].filter(Boolean).some(element =>
   ['step','page','true'].includes(element.getAttribute('aria-current')) ||
   element.getAttribute('aria-selected') === 'true' ||
   [...element.classList].some(cls => /(^|[-_])(active|current|selected)([-_]|$)/i.test(cls)));
 const setupMarker = cues.some(node => visible(node) && /^(?:sms )?(?:campaign )?setup$/.test(text(node)) &&
   (active(node) || node.matches('h1,h2,h3,h4,[role=heading]')));
 const laterStep = cues.some(node => visible(node) && /^(audience|content|schedule|preview)$/.test(text(node)) && active(node));
 const scope = names.length === 1 ? names[0].form : null;
 const setupForm = !!scope && [...scope.querySelectorAll('button,input[type=submit],[role=button],label,mat-label')].some(node =>
   visible(node) && /^(next step|add tags?|tags?)$/.test((node.value || text(node)).trim().toLowerCase()));
 return {campaign_name_count: names.length, setup_view: !laterStep && (setupMarker || setupForm),
   setup_marker_visible: setupMarker, setup_form_signature: setupForm, later_step_active: laterStep,
   control: names.length === 1 ? names[0] : null};
};'''


SETUP_OBSERVATION = r'''(() => {
''' + V2_LABEL_HELPERS + SETUP_DESCRIPTION + r'''
 const {control, ...observation} = describeSmsSetup();
 return observation;
})()'''


def verify_future_schedule(now=None):
    try:
        scheduled = datetime.fromisoformat(SCHEDULE).astimezone(ZoneInfo(TIMEZONE))
    except ZoneInfoNotFoundError:
        raise V2Stop('TIMEZONE_DATA_UNAVAILABLE', owner='environment') from None
    if scheduled <= (now if now is not None else datetime.now(timezone.utc)):
        raise V2Stop('SMS_SCHEDULE_NOT_IN_FUTURE', owner='authorization')
    return scheduled


def setup_status(observation):
    if observation.get('approved_route_verified') is False:
        return 'SMS_SETUP_PROTECTED_ROUTE_UNVERIFIED'
    if observation['campaign_name_count'] != 1:
        return 'SMS_SETUP_CAMPAIGN_NAME_NOT_UNIQUE'
    if observation['setup_view'] is not True:
        return 'SMS_SETUP_VIEW_UNVERIFIED'
    return 'SMS_SETUP_VERIFIED'


async def require_setup_document(browser, binding, *, inspection=False):
    await binding.require_current(browser)
    await reject_security_negatives(browser)
    context = await login_page_context(browser)
    url = await browser.page.url()
    route_allowed = (url.partition('#')[0] == CREATE_URL if inspection
                     else url in (CREATE_URL, CREATE_URL + '#setup'))
    if not context['primary_document_coherent'] or not browser.policy.permits(url) or not route_allowed:
        raise V2Stop('SMS_SETUP_PROTECTED_ROUTE_UNVERIFIED', owner='browser', unsafe=True)
    owner = browser.guard.session_states[binding.session]
    return (*login_document(browser), owner.document_generation, owner.frame_id, owner.loader_id, url)


class SetupObserver:
    stage = 'sms_setup'

    def __init__(self, browser, binding, document, *, inspection=False):
        self.browser, self.binding, self.document = browser, binding, document
        self.inspection = inspection

    async def protect(self):
        current = await require_setup_document(self.browser, self.binding, inspection=self.inspection)
        if current != self.document:
            raise V2Stop('SMS_SETUP_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        return current

    async def verify(self):
        self.browser.budget()
        facts = await self.browser.page.evaluate(SETUP_OBSERVATION)
        await self.protect()
        url = self.document[-1]
        fragment = url == CREATE_URL + '#setup'
        return {'approved_route_verified': url == CREATE_URL or fragment, 'setup_fragment': fragment, **facts}

    def registered_tools(self):
        return [StructuredTool.from_function(coroutine=self.verify, name='verify_sms_setup',
                description='Read SMS Setup and visible Campaign name evidence on the approved owned route.',
                args_schema=EmptyArguments)]


class Preflight:
    def __init__(self, review_seconds, *, payload=None, teacher=False, preview=False, digest=None,
                 operator_confirmed_unused_name=None, skill=None, iteration=None, tag_investigation=False,
                 reconcile_before_write=False):
        self.review_seconds = review_seconds
        self.payload = payload
        self.teacher = teacher
        self.preview = preview
        self.digest = digest
        self.operator_confirmed_unused_name = operator_confirmed_unused_name
        self.skill, self.iteration = skill, iteration
        self.reconcile_before_write = reconcile_before_write
        self.tag_investigation = tag_investigation
        self.diagnostic = CampaignListDiagnostic(urlsplit(CREATE_URL).hostname)

    async def start(self, browser):
        if self.iteration:
            browser.trace.iteration = {'baseline': 1, 'recovery': 2, 'retention': 3}[self.iteration]
            browser.trace.agent_event('sms_iteration_started', role='controller', label=self.iteration,
                                      selected_skill_sha256=self.skill.digest)
        try:
            async with asyncio.timeout(8):
                await self.diagnostic.start(browser)
        except (RuntimeError, OSError, TimeoutError):
            self.diagnostic.mark('PASSIVE_LISTENER_UNAVAILABLE')
            browser.trace.agent_event('sms_campaigns_monitor', role='observer', status='UNAVAILABLE')

    def operator_seconds(self, browser):
        return min(300, self.review_seconds, int(browser.config.timeout_seconds - (monotonic() - browser.started)))

    def console(self, browser):
        return OperatorConsole(event_sink=lambda event: browser.trace.agent_event('operator_decision', role='operator', **event))

    async def approve_payload(self, observer, evidence):
        browser = observer.browser
        payload = self.payload
        document = await observer.protect()
        seconds = self.operator_seconds(browser)
        summary = (f'{payload.campaign_name} · tag {payload.tag} · {payload.audience_type}: {payload.include_audience} · '
                   f'{payload.channel} / {payload.sender_id} / {payload.custom_domain} · '
                   f'{payload.template} ({payload.template_id}) · {payload.send_type} {payload.schedule_datetime} '
                   f'{payload.timezone}. Next Step may persist campaign state. Preview once.')
        attest = evidence.get('operator_name_attestation_pending') is True
        name_status = evidence['name_check']['status']
        uncertainty = not attest and name_status in DUPLICATE_CHECK_UNAVAILABLE
        if uncertainty:
            summary += ' Duplicate check unavailable. Answering y approves proceeding with that uncertainty.'
        if attest:
            summary += (f' By answering y, you also confirm you already searched the exact name {payload.campaign_name!r} '
                        f'in a working Campaigns view for {payload.tenant} and verified it is unused.')
        decision = await self.console(browser).confirm_sms_payload(summary, seconds) if seconds > 0 else 'timeout'
        if await observer.protect() != document:
            raise V2Stop('SMS_PAYLOAD_APPROVAL_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        approval = {'accepted': decision == 'yes', 'decision': decision, 'tenant': payload.tenant,
                    'campaign_name': payload.campaign_name, 'payload_sha256': self.digest,
                    'scope': {'enter_fixed_fields': True, 'next_step': ['setup', 'audience', 'content'],
                              'next_step_may_persist': True, 'apply_send_later': True, 'preview_max_clicks': 1,
                              'excluded': ['finish_later', 'save_and_publish', 'activate', 'send']},
                    'operator_exact_name_attestation': attest and decision == 'yes',
                    'automated_duplicate_check': name_status,
                    'duplicate_uncertainty_accepted': uncertainty and decision == 'yes'}
        approval_ref = browser.trace.agent_event('sms_payload_approval', role='operator', **approval)
        evidence['payload_approval'] = {**approval, 'evidence_ref': approval_ref}
        if decision != 'yes':
            raise V2Stop('SMS_PAYLOAD_APPROVAL_TIMEOUT' if decision == 'timeout' else 'SMS_PAYLOAD_DECLINED',
                         owner='authorization')
        if attest:
            proof = {'status': 'CAMPAIGN_NAME_OPERATOR_CONFIRMED_UNUSED', 'source': 'OPERATOR_ATTESTATION',
                     'exact_name': payload.campaign_name, 'tenant': payload.tenant, 'working_view_attested': True,
                     'approval_ref': approval_ref}
            browser.trace.agent_event('sms_exact_name_operator_result', role='operator', **proof)
            evidence.update(operator_name_check=proof, name_status=proof['status'])
        if uncertainty:
            evidence['name_check_resolution'] = 'OPERATOR_APPROVED_UNCERTAINTY'
        evidence.pop('operator_name_attestation_pending', None)

    async def save_campaigns_evidence(self, browser, binding, directory, name_check):
        await binding.require_current(browser)
        url = await browser.page.url()
        parts = urlsplit(url)
        if (not browser.policy.permits(url) or parts.scheme != 'https' or parts.netloc != self.diagnostic.host
                or parts.path != name_check['pathname'] or parts.fragment):
            raise V2Stop('SMS_CAMPAIGNS_EVIDENCE_ROUTE_CHANGED', owner='browser', unsafe=True)
        context_before = await login_page_context(browser)
        try:
            async with asyncio.timeout(3):
                tree = await browser.guard.send('Page.getFrameTree', session=binding.session)
        except (RuntimeError, TimeoutError):
            frames = {'status': 'FRAME_TREE_UNAVAILABLE'}
        else:
            root = tree.get('frameTree', {})
            pending = [(root, 0)] if root.get('frame') else []
            rows = []
            while pending and len(rows) < 40:
                node, depth = pending.pop(0)
                frame_url = node.get('frame', {}).get('url', '')
                rows.append({'depth': depth, 'tenant_path': safe_request_path(frame_url, self.diagnostic.host)})
                pending.extend((child, depth + 1) for child in node.get('childFrames', ())[:40])
            frames = {'status': 'OBSERVED' if rows else 'FRAME_TREE_EMPTY', 'frames': rows, 'truncated': bool(pending)}
        await binding.require_current(browser)
        context_after = await login_page_context(browser)
        if not context_after.get('primary_document_coherent') or context_before != context_after:
            raise V2Stop('SMS_CAMPAIGNS_EVIDENCE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        path = directory / 'sms-campaigns-readonly-diagnostic.json'
        self.diagnostic.mark('CAMPAIGNS_READINESS_FAILED', details=name_check)
        self.diagnostic.save(path, frames=frames)
        browser.trace.agent_event('sms_campaigns_evidence_saved', role='observer', path=path.name,
                                  frame_status=frames['status'], request_count=len(self.diagnostic.requests),
                                  console_error_count=len(self.diagnostic.errors))
        return path.name

    async def hold(self, observer, message, *, protect=None):
        browser = observer.browser
        seconds = self.operator_seconds(browser)
        if seconds < 1:
            return 'SMS_REVIEW_SESSION_EXPIRED'
        async def require_review_page():
            current = await require_setup_document(browser, observer.binding, inspection=True)
            if current[:-1] != observer.document[:-1]:
                raise V2Stop('SMS_SETUP_DOCUMENT_CHANGED', owner='browser', unsafe=True)

        protect = protect or require_review_page
        await protect()
        browser.begin_readonly_review(seconds)
        trace = browser.trace
        trace.agent_event('sms_setup_review', role='operator', status='OPEN', seconds=seconds, message=message)
        task = asyncio.create_task(inspection_hold(seconds, prompt=f'{message}. Press Enter to close Chromium.'))
        try:
            while not task.done():
                if not (await asyncio.wait({task}, timeout=2))[0]:
                    await protect()
            await task
            return 'RELEASED'
        except TimeoutError:
            return 'SMS_REVIEW_WINDOW_EXPIRED'
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def __call__(self, browser, binding, directory, *, settings, budget, task_request_limit=0):
        async def review(facts, *, observer=None, workflow=False, tag_focus=None):
            from modules.sms.review import review_preflight

            return await review_preflight(browser.trace, settings, budget, directory, facts, observer=observer,
                                          task_digest=self.digest if workflow else None, skill=self.skill,
                                          tag_focus=tag_focus)

        async def execute(observer, evidence):
            from modules.sms.workflow import finish_preview_workflow, investigate_tag_readonly

            if self.tag_investigation and not (self.reconcile_before_write and evidence['name_status'] == 'CAMPAIGN_NAME_UNUSED'):
                return await investigate_tag_readonly(self, observer, evidence, settings, budget, task_request_limit,
                                                      review=review, directory=directory)
            return await finish_preview_workflow(self, observer, evidence, settings, budget, task_request_limit,
                                                 review=review, directory=directory)

        return await preflight(browser, binding, directory, self, review=review if self.teacher else None,
                               execute=execute if self.preview else None)


async def observe_setup(observer, *, attempts):
    step = StepTools('observer', observer.stage, frozenset({'verify_sms_setup'}))
    async with open_step(observer, step, document_identity=login_document, independent_observer=True) as client:
        for attempt in range(attempts):
            facts = await client.read('verify_sms_setup')
            status = setup_status(facts)
            if status == 'SMS_SETUP_VERIFIED' or attempt + 1 == attempts:
                return status, facts
            await asyncio.sleep(.4)


async def preflight(browser, binding, directory, run, *, review=None, execute=None):
    """Run independent name and Setup checks in the same protected session."""
    trace = browser.trace
    trace.agent_stage = 'sms_preflight'
    trace.agent_phase = 'sms_preflight'
    schedule_status = 'SMS_SCHEDULE_IN_FUTURE'
    try:
        scheduled = verify_future_schedule()
        trace.agent_event('sms_schedule_in_future', role='controller', scheduled=scheduled.isoformat())
    except V2Stop as error:
        schedule_status = error.code
    payload = getattr(run, 'payload', None)
    investigation = getattr(run, 'tag_investigation', False)
    name_check = await reconcile_campaign(browser, binding, payload or SimpleNamespace(campaign_name=CAMPAIGN_NAME),
                                          trace.run_id, None, check_unused=True, announce=False, visible_duplicate=investigation)
    name_status = name_check['status']
    evidence = {'name_check': {'source': 'AUTOMATED_CAMPAIGNS_UI', **name_check}, 'schedule_status': schedule_status,
                'last_verified_step': 'CAMPAIGN_NAME_UNUSED' if name_status == 'CAMPAIGN_NAME_UNUSED'
                else 'OPERATOR_CONFIRMED'}
    if name_status == 'CAMPAIGN_LIST_READINESS_FAILED':
        evidence['campaigns_diagnostic'] = await run.save_campaigns_evidence(browser, binding, directory, name_check)
    evidence['name_status'] = name_status
    if name_status == 'CAMPAIGN_NAME_ALREADY_USED' and not investigation:
        return {**evidence, 'status': name_status, 'message': 'Exact campaign name already exists. Supply a replacement name.'}
    attestation = (execute is not None and payload is not None
                   and getattr(run, 'operator_confirmed_unused_name', None) == payload.campaign_name
                   and (name_status in DUPLICATE_CHECK_UNAVAILABLE or name_status == 'CAMPAIGN_NAME_UNUSED'))
    best_effort = execute is not None and payload is not None and name_status in DUPLICATE_CHECK_UNAVAILABLE
    if name_status != 'CAMPAIGN_NAME_UNUSED' and not (attestation or best_effort or investigation):
        return {**evidence, 'status': 'SMS_EXACT_NAME_CHECK_BLOCKED',
                'message': f"Campaigns search could not verify exact name {(payload.campaign_name if payload else CAMPAIGN_NAME)!r}."}
    if attestation:
        evidence['operator_name_attestation_pending'] = True

    await binding.require_current(browser)
    await reject_security_negatives(browser)
    trace.agent_event('sms_setup_navigation', role='controller', status='APPROVED_ROUTE_REQUESTED')
    await browser.navigate(payload.create_url if payload else CREATE_URL)
    observation = {}
    try:
        observer = SetupObserver(browser, binding, await require_setup_document(browser, binding))
        evidence['last_verified_step'] = 'SMS_APPROVED_ROUTE_VERIFIED'
        status, observation = await observe_setup(observer, attempts=8)
    except (V2Stop, TabProtectionError, BaselineStop) as error:
        details = getattr(error, 'diagnostic', None) or {'category': error.code, 'operation': 'verify_sms_setup'}
        evidence['setup_read_failure'] = details
        trace.agent_event('sms_setup_read_failed', role='observer', **details)
        status = details['category']
        try:
            observer = SetupObserver(browser, binding, await require_setup_document(browser, binding, inspection=True),
                                     inspection=True)
        except (V2Stop, TabProtectionError, BaselineStop) as unsafe:
            trace.agent_event('sms_setup_diagnostic_unavailable', role='observer', code=unsafe.code)
            return {**evidence, 'status': status, 'review_status': unsafe.code}
        try:
            status, observation = await observe_setup(observer, attempts=1)
            trace.agent_event('sms_setup_diagnostic_read', role='observer', status=status, **observation)
        except V2Stop as diagnostic_error:
            details = getattr(diagnostic_error, 'diagnostic', None) or {
                'category': diagnostic_error.code, 'operation': 'verify_sms_setup'}
            evidence['setup_diagnostic_failure'] = details
            trace.agent_event('sms_setup_diagnostic_read_failed', role='observer', **details)
    trace.agent_event('sms_setup_preflight', role='observer', status=status, **observation)
    evidence.update(status=status, setup_evidence=observation)
    if status != 'SMS_SETUP_VERIFIED':
        evidence['message'] = f'SMS Setup verification incomplete ({status})'
    else:
        evidence['last_verified_step'] = 'SMS_SETUP_VERIFIED'
        evidence['timezone_status'] = 'DEFERRED_TO_SCHEDULE'
        evidence['status'] = 'SMS_PREFLIGHT_VERIFIED'
        if schedule_status != 'SMS_SCHEDULE_IN_FUTURE':
            evidence.update(status=schedule_status, message='SMS Setup verified, schedule check blocked')
        else:
            evidence['message'] = 'SMS Setup verified'
    if execute is not None and evidence['status'] == 'SMS_PREFLIGHT_VERIFIED':
        return await execute(observer, evidence)
    if review is not None:
        evidence['teacher_review'] = await review(observation, observer=observer)
    try:
        evidence['review_status'] = await run.hold(observer, evidence['message'])
    except (V2Stop, TabProtectionError, BaselineStop) as error:
        evidence['review_status'] = error.code
    trace.agent_event('sms_setup_review', role='operator', status=evidence['review_status'])
    return evidence
