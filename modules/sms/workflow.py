"""SMS campaign Doer, independent Observer, and controller-owned transitions."""

import asyncio
import hashlib
import json
from urllib.parse import urlsplit

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict

from app.browser_mcp_client import StepTools, open_step
from app.config import ROOT
from browser.protected_tabs import TabProtectionError
from deep_agents.models import openai_model
from deep_agents.permissions import PermissionMiddleware, model_failure_details, restrict_harness
from modules.sms.contract import DUPLICATE_CHECK_UNAVAILABLE
from modules.sms.executor import CAPABILITIES, STAGES, SMSExecutor, SMSObserver
from modules.sms.preflight import verify_future_schedule
from modules.sms.skill import TASK_PROMPT, load_skill
from modules.whatsapp.browser_baseline import BaselineStop
from modules.whatsapp.contract import V2Stop
from modules.whatsapp.unattended import claim_write


class SMSStageReport(BaseModel):
    model_config = ConfigDict(extra='forbid')

    complete: bool


def observations_for(stage):
    return {'observe_sms_stage', 'inspect_sms_tag'} if stage == 'setup' else {'observe_sms_stage'}


async def run_stage_doer(model, executor, client, trace, budget, *, skill=None, read_only=False):
    skill = skill or load_skill()
    registered = client.agent_tools(image_content=False)
    fields = [field for field in STAGES[executor.stage] if any(
        (executor.stage, action, field) in CAPABILITIES for action in client.tools)]
    restrict_harness(model)
    permissions = PermissionMiddleware({tool.name for tool in registered} | {'SMSStageReport'},
                                       trace, budget, False, max_tool_calls=len(STAGES[executor.stage]) + 4)
    agent = create_deep_agent(model=model, tools=registered, backend=StateBackend(), subagents=[],
        middleware=[permissions], response_format=ToolStrategy(SMSStageReport, handle_errors=False),
        checkpointer=InMemorySaver(), name='sms_task_doer', system_prompt=(
            'You operate one approved SMS campaign stage through fixed-value private MCP tools. '
            'Choose a tool and field for each requested field, in the given field order. The tools supply all values. '
            'Do not invent parameters or advance stages. Stop on uncertain writes. '
            'For a safe unresolved binding, use the supported semantic and DOM observations before declaring stuck. '
            'In Setup, inspect_sms_tag separates the initial combobox binding from popup and exact-option verification. '
            'Return complete only when all requested operations succeeded. The separate Observer certifies actual state. '
            'The controller establishes authorization and may explicitly accept unavailable duplicate evidence. '
            'Scheduling timezone is checked on Schedule before any date or time entry. Skills grant no permissions.\n'
            + ('The current session is read-only while campaign name reuse is being reconciled. Use the two scoped '
               'observation approaches and inspect_sms_tag to investigate Add tags, then stop. '
               'No field or transition writes are available.\n' if read_only else '')
            + 'SMS stage context ' + json.dumps({'stage': executor.stage, 'fields_in_order': fields,
                                                  'arrival': executor.last_observer_snapshot}) + '\n'
            + skill.content))
    try:
        with tracing_context(enabled=False):
            result = await agent.ainvoke({'messages': [{'role': 'user', 'content': TASK_PROMPT}]},
                {'configurable': {'thread_id': f'{trace.run_id}-sms-{executor.stage}-{fields[0] if fields else "readonly"}'},
                 'recursion_limit': 24})
    except V2Stop:
        raise
    except Exception as error:
        diagnostic = next((row for row in reversed(trace.model_diagnostics) if row.get('role') == 'doer'), {})
        detail = model_failure_details(error, diagnostic)
        raise V2Stop(detail['code'], owner='model', unsafe=executor.terminal) from None
    report = result.get('structured_response')
    if report is None:
        raise V2Stop('MISSING_STRUCTURED_REPORT', owner='model', unsafe=executor.terminal)
    return report.complete


def record_loaded_skill(run, directory, trace, evidence):
    path = directory / 'loaded-SKILL.md'
    path.write_bytes(run.skill.content.encode('utf-8'))
    evidence['loaded_skill'] = {'version': run.skill.metadata['version'], 'sha256': run.skill.digest,
                                'source': run.skill.metadata['source'], 'path': path.name}
    trace.strategy_version = run.skill.digest
    trace.agent_event('sms_task_skill_loaded', role='doer', **evidence['loaded_skill'], task_prompt=TASK_PROMPT)
    evidence['task_doer_invoked'] = True


async def read_stage(executor, *, settle=False):
    observer = SMSObserver(executor)
    scope = StepTools('observer', observer.stage, frozenset({'read_sms_stage'}))
    async with open_step(observer, scope, independent_observer=True) as client:
        for attempt in range(8 if settle else 1):
            facts = await client.read('read_sms_stage')
            if facts['stage_visible'] or not settle or attempt == 7:
                return facts
            await asyncio.sleep(.25)


def verified_stage(facts):
    if not facts['stage_visible']:
        return False
    stage = facts['stage']
    if stage == 'preview':
        preview = facts['preview']
        visible = {name: state for name, state in preview.items() if name != 'campaign_id' and state['displayed']}
        return bool(visible.get('campaign_name', {}).get('matched')) and all(state['matched'] for state in visible.values())
    return set(facts['fields']) == STAGES[stage].keys() and all(facts['fields'].values())


def preview_claim_path(outputs, payload):
    key = hashlib.sha256((urlsplit(payload.create_url).netloc + '\0' + payload.tenant + '\0'
                          + payload.campaign_name).encode()).hexdigest()
    return outputs / 'sms-preview' / 'preview-attempts' / (key + '.json')


def preview_claim(outputs, payload, digest, trace):
    verify_future_schedule()
    path = preview_claim_path(outputs, payload)
    claim_write(path, {'action': 'Preview', 'payload_sha256': digest, 'run_id': trace.run_id,
                       'status': 'PREVIEW_ATTEMPT_CLAIMED'}, 'PRIOR_SMS_PREVIEW_REQUIRES_READ_ONLY_RECONCILIATION')


async def execute_workflow(executor, preflight, *, stage_doer, before_preview):
    automated_status = preflight.get('name_check', {}).get('status')
    automatic = automated_status == 'CAMPAIGN_NAME_UNUSED'
    operator = preflight.get('operator_name_check', {})
    approval = preflight.get('payload_approval', {})
    uncertainty = (automated_status in DUPLICATE_CHECK_UNAVAILABLE and approval.get('accepted') is True
                   and approval.get('duplicate_uncertainty_accepted') is True
                   and approval.get('automated_duplicate_check') == automated_status
                   and approval.get('tenant') == executor.payload.tenant and approval.get('campaign_name') == executor.payload.campaign_name)
    if (preflight.get('status') != 'SMS_PREFLIGHT_VERIFIED' or automated_status == 'CAMPAIGN_NAME_ALREADY_USED' or not (
            automatic or uncertainty or (operator.get('status') == 'CAMPAIGN_NAME_OPERATOR_CONFIRMED_UNUSED'
                          and operator.get('source') == 'OPERATOR_ATTESTATION' and operator.get('working_view_attested') is True))):
        raise V2Stop('SMS_LIVE_PREFLIGHT_REQUIRED', owner='authorization')
    if not executor.early_progression_approved:
        raise V2Stop('SMS_POSSIBLY_PERSISTING_PROGRESSION_NOT_APPROVED', owner='authorization')
    if not (automatic or uncertainty) and (operator.get('exact_name') != executor.payload.campaign_name or operator.get('tenant') != executor.payload.tenant):
        raise V2Stop('SMS_OPERATOR_NAME_EVIDENCE_SCOPE_MISMATCH', owner='authorization')
    verify_future_schedule()
    trace = executor.browser.trace
    results = {}
    executor.last_readbacks = results
    executor.last_verified_stage = None
    executor.last_visible_stage = None
    recorded = set()

    def record(facts):
        stage = facts['stage']
        results[stage] = facts
        for action_stage, action, field, event in executor.actions:
            if event not in recorded and action_stage == stage and facts['stage_visible'] and facts['fields'].get(field):
                trace.agent_event('sms_task_readback', role='observer', stage=stage, field=field, action=action,
                                  action_ref=event, status='VERIFIED')
                recorded.add(event)
        trace.agent_event('sms_stage_readback', role='observer', **facts)

    async def act(operations):
        stage = executor.stage
        operations = operations | observations_for(stage)
        trace.agent_phase = 'task_doer'
        trace.agent_event('sms_task_stage_invoked', role='doer', stage=stage, operations=sorted(operations))
        try:
            async with open_step(executor, StepTools('doer', stage, operations), task_doer=True) as client:
                done = await stage_doer(executor, client)
        except (V2Stop, BaselineStop, TabProtectionError) as stop:
            trace.agent_event('sms_task_stage_failed', role='doer', stage=stage, code=stop.code)
            try:
                record(await read_stage(executor))
            except (V2Stop, BaselineStop, TabProtectionError) as unavailable:
                trace.agent_event('sms_stage_readback_unavailable', role='observer', stage=stage, code=unavailable.code)
            raise
        trace.agent_phase = 'observer'
        facts = await read_stage(executor)
        record(facts)
        return done, facts

    arrived = await read_stage(executor, settle=True)
    for stage, fields in STAGES.items():
        trace.agent_stage = stage
        if not arrived['stage_visible']:
            record(arrived)
            raise V2Stop('SMS_DESTINATION_STAGE_UNVERIFIED', owner='observer', unsafe=executor.terminal)
        executor.last_visible_stage = stage
        trace.agent_event('sms_stage_arrived', role='observer', stage=stage, status='VISIBLE')
        executor.terminal = False
        if stage == 'preview':
            if not verified_stage(arrived):
                raise V2Stop('SMS_PREVIEW_READBACK_UNVERIFIED', owner='observer', unsafe=True)
            results[stage] = arrived
            executor.last_verified_stage = 'preview'
            trace.agent_event('sms_task_outcome', role='observer', stage=stage, status='SMS_PREVIEW_VERIFIED',
                              campaign_id=arrived['preview'].get('campaign_id'), retry_safe=False)
            return results
        if stage == 'schedule':
            ready = arrived
            if not ready['fields'].get('send_type'):
                done, ready = await act(frozenset({'set_sms_field'}))
                if not done or not ready['fields'].get('send_type') or executor.terminal:
                    raise V2Stop('SMS_SEND_LATER_READBACK_UNVERIFIED', owner='observer', unsafe=executor.terminal)
            zone = ready['timezone']
            status = ('SMS_TENANT_TIMEZONE_VERIFIED' if zone['matched'] else 'SMS_SCHEDULING_TIMEZONE_MISMATCH'
                      if zone['displayed_iana'] else 'SMS_SCHEDULING_TIMEZONE_UNVERIFIED')
            preflight['timezone_status'] = status
            preflight['timezone_evidence'] = zone
            trace.agent_event('sms_scheduling_timezone_check', role='observer', stage=stage, status=status, **zone)
            if not zone['matched']:
                results[stage] = ready
                raise V2Stop(status, owner='observer')
            done, facts = await act(frozenset({'apply_sms_schedule'}))
        else:
            operations = frozenset(action for candidate, action, field in CAPABILITIES if candidate == stage and field in fields)
            done, facts = await act(operations)
        if not done or not verified_stage(facts) or executor.terminal:
            approaches = {approach for observed_stage, approach in executor.observations if observed_stage == stage}
            if not executor.terminal and approaches == {'semantic', 'dom'}:
                raise V2Stop('SMS_STUCK_AFTER_TWO_VALID_APPROACHES', owner='recognition')
            raise V2Stop('SMS_STAGE_READBACK_NOT_VERIFIED', owner='observer', unsafe=executor.terminal)
        executor.last_verified_stage = stage
        print(f'{stage.title()} verified.', flush=True)
        trace.agent_phase = 'controller'
        await executor.advance(facts, before_preview=before_preview)
        arrived = await read_stage(executor, settle=True)


async def finish_preview_workflow(run, observer, evidence, settings, budget, task_limit, *, review, directory):
    browser = observer.browser
    trace = browser.trace
    executor = SMSExecutor(browser, observer.binding, run.payload)
    budget.role_limits['doer'] += task_limit
    if budget.role_limits['doer'] - budget.role_requests['doer'] < 15 and budget.role_limits['teacher'] > 1:
        budget.role_limits['doer'] += 1
        budget.role_limits['teacher'] = budget.teacher_request_reserve = 1
        budget.teacher_reserve_input, budget.teacher_reserve_output = settings.teacher_input, settings.teacher_output
    model = openai_model('doer', settings, trace, budget)

    async def do_stage(current, client):
        if not evidence.get('task_doer_invoked'):
            record_loaded_skill(run, directory, trace, evidence)
        return await run_stage_doer(model, current, client, trace, budget, skill=run.skill)

    try:
        if preview_claim_path(ROOT / 'outputs', run.payload).exists():
            raise V2Stop('PRIOR_SMS_PREVIEW_REQUIRES_READ_ONLY_RECONCILIATION', owner='uncertain_write', unsafe=True)
        await run.approve_payload(observer, evidence)
        executor.early_progression_approved = True
        executor.payload_approval_ref = evidence['payload_approval']['evidence_ref']
        evidence['stage_readbacks'] = await execute_workflow(executor, evidence, stage_doer=do_stage,
            before_preview=lambda: preview_claim(ROOT / 'outputs', run.payload, run.digest, trace))
        evidence['status'] = 'SMS_PREVIEW_VERIFIED'
        evidence['campaign_id'] = evidence['stage_readbacks']['preview']['preview']['campaign_id']
    except (V2Stop, BaselineStop, TabProtectionError) as stop:
        evidence['stage_readbacks'] = getattr(executor, 'last_readbacks', {})
        evidence['status'] = stop.code
        evidence['message'] = {'SMS_PAYLOAD_DECLINED': 'SMS payload declined.',
                               'SMS_PAYLOAD_APPROVAL_TIMEOUT': 'SMS payload approval timed out.'}.get(stop.code,
                                                                                                    f'SMS stopped at {stop.code}.')
        evidence['failure'] = {'code': stop.code, 'owner': getattr(stop, 'owner', 'browser'),
                               'unsafe': executor.terminal or getattr(stop, 'unsafe', False)}
        trace.agent_event('sms_task_outcome', role='controller', stage=executor.stage, status='BLOCKED',
                          code=stop.code, owner=evidence['failure']['owner'], retry_safe=not evidence['failure']['unsafe'])
    evidence['last_verified_step'] = ('SMS_' + executor.last_verified_stage.upper() + '_VERIFIED'
                                     if getattr(executor, 'last_verified_stage', None) else evidence['last_verified_step'])
    if getattr(executor, 'last_visible_stage', None) == 'schedule' and executor.last_verified_stage != 'schedule':
        evidence['last_verified_step'] = 'SMS_SCHEDULE_VISIBLE'
    evidence['preview_dispatched'] = executor.preview_dispatched
    evidence['campaign_mutations_dispatched'] = executor.mutation_count
    if evidence.get('payload_approval', {}).get('accepted'):
        review_observer = executor
        tag = None
        if executor.stage == 'setup':
            review_observer, tag = await read_tag(executor)
            evidence['tag_readback'] = tag
        evidence['teacher_review'] = await review({**evidence['stage_readbacks'], **({'tag': tag} if tag else {})},
                                                 observer=review_observer, workflow=True, tag_focus=tag)
    if evidence['status'] in {'SMS_PAYLOAD_DECLINED', 'SMS_PAYLOAD_APPROVAL_TIMEOUT'}:
        return evidence
    try:
        approved_document = await executor.protect()
        async def protect_review():
            if await executor.protect() != approved_document:
                raise V2Stop('SMS_REVIEW_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        message = 'SMS Preview verified' if evidence['status'] == 'SMS_PREVIEW_VERIFIED' else f"SMS stopped at {evidence['status']}"
        evidence['review_status'] = await run.hold(observer, message, protect=protect_review)
    except (V2Stop, BaselineStop, TabProtectionError) as stop:
        evidence['review_status'] = stop.code
    trace.agent_event('sms_setup_review', role='operator', status=evidence['review_status'])
    return evidence


async def read_tag(executor):
    observer = SMSObserver(executor, tag_investigation=True)
    trace = executor.browser.trace
    trace.agent_phase = 'observer'
    try:
        scope = StepTools('observer', 'setup', frozenset({'inspect_sms_tag'}))
        async with open_step(observer, scope, independent_observer=True) as client:
            tag = await client.read('inspect_sms_tag')
    except V2Stop as stop:
        tag = {'complete': False, 'error': stop.code, 'diagnostic': getattr(stop, 'diagnostic', None)}
    tag['approaches'] = sorted(approach for stage, approach in executor.observations if stage == 'setup')
    trace.agent_event('sms_tag_investigation_readback', role='observer', stage='setup', **tag)
    return observer, tag


async def investigate_tag_readonly(run, setup, evidence, settings, budget, task_limit, *, review, directory):
    browser, trace = setup.browser, setup.browser.trace
    executor = SMSExecutor(browser, setup.binding, run.payload)
    evidence.update(mode='TAG_INVESTIGATION_READ_ONLY', campaign_mutations_dispatched=0, preview_dispatched=False)
    trace.agent_stage = 'setup'
    evidence['setup_readback'] = await read_stage(executor)
    budget.role_limits['doer'] += min(4, task_limit)
    model = openai_model('doer', settings, trace, budget)
    try:
        trace.agent_phase = 'task_doer'
        scope = StepTools('doer', 'setup', frozenset(observations_for('setup')))
        async with open_step(executor, scope, task_doer=True) as client:
            record_loaded_skill(run, directory, trace, evidence)
            await run_stage_doer(model, executor, client, trace, budget, skill=run.skill, read_only=True)
    except V2Stop as stop:
        evidence['readonly_doer_stop'] = stop.code

    observer, tag = await read_tag(executor)
    evidence['tag_readback'] = tag
    evidence['teacher_review'] = await review({'setup': evidence['setup_readback'], 'tag': tag}, observer=observer,
                                             tag_focus=tag)
    evidence['status'] = 'SMS_TAG_INVESTIGATION_COMPLETE'
    label = 'Setup Tag control bound' if tag.get('binding_kind') else 'Setup Tag control remains unbound'
    evidence['message'] = label
    try:
        document = await executor.protect()
        async def protect_review():
            if await executor.protect() != document:
                raise V2Stop('SMS_REVIEW_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        evidence['review_status'] = await run.hold(setup, label, protect=protect_review)
    except (V2Stop, BaselineStop, TabProtectionError) as stop:
        evidence['review_status'] = stop.code
    trace.agent_event('sms_setup_review', role='operator', status=evidence['review_status'])
    return evidence
