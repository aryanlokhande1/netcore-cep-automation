"""Bounded production Teacher analysis of observed SMS preflight evidence."""

import asyncio
import base64
import difflib
import hashlib
import json
from typing import Literal

from langchain_core.tools import StructuredTool
from langsmith import tracing_context
from pydantic import BaseModel, ConfigDict, Field

from deep_agents.models import openai_model
from deep_agents.teacher import create_teacher
from app.browser_mcp import EmptyArguments
from app.config import ROOT


class Lesson(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action_ref: str
    readback_ref: str


class SMSPreflightReview(BaseModel):
    model_config = ConfigDict(extra='forbid')

    diagnosis: str = Field(description='Evidence-backed diagnosis of the remaining checks')
    evidence_refs: list[str] = Field(description='IDs of the supplied events that support the diagnosis')
    setup_state: Literal['verified', 'unverified', 'unobserved']
    classification: Literal['CODE_FIX_REQUIRED', 'MODEL_STRATEGY', 'UI_EVIDENCE_GAP', 'NO_CHANGE']
    lessons: list[Lesson] = Field(description='Pairs of an executed task action and its later independent verified readback')


def ordered_evidence(trace, observation):
    kinds = {'login_verification', 'sms_schedule_in_future', 'campaign_reconciliation_result',
             'sms_campaigns_evidence_saved', 'mcp_observer_failure', 'sms_setup_preflight',
             'sms_setup_diagnostic_read', 'sms_setup_diagnostic_unavailable', 'sms_scheduling_timezone_check',
             'sms_task_action', 'sms_task_readback', 'sms_task_action_blocked', 'sms_task_outcome',
             'sms_doer_approach', 'sms_task_skill_loaded',
             'sms_exact_name_operator_result', 'sms_next_step_persistence_scope', 'sms_stage_readback',
             'sms_tag_investigation_readback', 'sms_tag_action_checks', 'sms_doer_tag_inspection',
             'sms_mcp_catalog', 'sms_mcp_result'}
    keys = {'id', 'kind', 'status', 'verification', 'category', 'operation', 'phase', 'campaign_name_count',
            'setup_view', 'approved_route_verified', 'timezone_values', 'table_count', 'visible_rows',
            'search_count', 'search_trigger_count', 'load_finished', 'empty_state_visible', 'code', 'stage', 'role',
            'action', 'field', 'action_ref', 'retry_safe', 'source', 'working_view_attested', 'displayed_iana',
            'labeled_control_count', 'operator_approved', 'native_form_submission', 'server_persistence', 'destination',
            'stage_visible', 'scope_available', 'fields', 'approach', 'bound_controls', 'observed_approved_labels', 'sha256',
            'controls', 'nearby_controls', 'native_binding_found', 'binding_kind', 'owned_exact_option_visible',
            'selected_tag_matches_payload', 'exact_label_ax_roles', 'anchor_count', 'binding_checks',
            'tool_names', 'tool', 'ok', 'diagnostic', 'already_matching', 'needs_independent_readback',
            'container_tag', 'marker_tag', 'complete'}
    events = [item['agent_execution'] for item in trace.events if 'agent_execution' in item]
    events = [{key: item[key] for key in keys & item.keys()} for item in events if item['kind'] in kinds][-48:]
    setup_reads = [item for item in events if item['kind'] in {'sms_setup_diagnostic_read', 'sms_setup_preflight'}]
    latest = setup_reads[-1] if setup_reads else None
    setup = ('unobserved' if latest is None else 'verified' if latest.get('approved_route_verified') is True
             and latest.get('setup_view') is True and latest.get('campaign_name_count') == 1 else 'unverified')
    recoveries = []
    if setup == 'verified':
        successful = next(item for item in reversed(setup_reads)
                          if item.get('approved_route_verified') is True and item.get('setup_view') is True)
        for event in events[:events.index(successful)]:
            if event['kind'] == 'mcp_observer_failure' and event.get('operation') == 'verify_sms_setup':
                recoveries.append({'earlier_failure': event['id'], 'superseded_by_success': successful['id']})
    return trace.redact_agent({'chronological_events': events, 'scoped_dom': observation, 'current_setup_state': setup,
                               'recovered_checks': recoveries})


def evidence_index(evidence):
    """Point at the already supplied evidence without duplicating it in model history."""
    events = evidence['chronological_events']
    return {
        'current_setup_state': evidence['current_setup_state'],
        'binding_checks': evidence['scoped_dom'].get('tag', {}).get('binding_checks', {}),
        'mcp_results': [{key: item[key] for key in ('id', 'role', 'tool', 'code', 'ok') if key in item}
                        for item in events if item['kind'] == 'sms_mcp_result'][-12:],
        'scoped_observer_refs': [item['id'] for item in events if item['kind'] == 'sms_tag_investigation_readback'],
    }


def validate_lessons(lessons, events, capabilities):
    index = {item['id']: (i, item) for i, item in enumerate(events)}
    steps = {}
    for lesson in lessons:
        action_index, action = index.get(lesson.action_ref, (-1, {}))
        read_index, readback = index.get(lesson.readback_ref, (-1, {}))
        key = tuple(action.get(name) for name in ('stage', 'action', 'field'))
        if (action_index < 0 or read_index <= action_index or key not in capabilities or key in steps
                or action.get('kind') != 'sms_task_action' or action.get('role') != 'doer' or action.get('status') != 'EXECUTED'
                or readback.get('kind') != 'sms_task_readback' or readback.get('role') != 'observer'
                or readback.get('status') != 'VERIFIED' or readback.get('action_ref') != lesson.action_ref
                or key != tuple(readback.get(name) for name in ('stage', 'action', 'field'))):
            raise ValueError('SMS_LESSON_EXECUTION_EVIDENCE_INVALID')
        steps[key] = lesson
    return steps


def save_candidate(trace, task_digest, steps, *, root=None, skill=None):
    if not steps:
        return None
    baseline = ROOT / 'skills' / 'sms' / 'SKILL.md'
    parent = skill.content.encode('utf-8') if skill else baseline.read_bytes()
    rules = ['\n## Observed procedures\n\n']
    retained = []
    for stage, action, field in steps:
        procedure = {'use_sms_template': 'Require one exact name and ID, scroll and hover its card, then use only that card’s menu. ',
                     'apply_sms_schedule': 'Require verified Send later and timezone evidence, fill date and time, and apply. '
                     }.get(action, 'Use the current unique enabled control and the fixed approved value. ')
        rule = (f'- On independently verified `{stage}`, use `{action}` with `field={field}`. The executor binds the exact '
                     f'current enabled control. {procedure}After its scope closes, require a matching '
                     '`read_sms_stage` result before advancing. Stop on uncertain writes.\n')
        if rule.encode('utf-8') not in parent:
            rules.append(rule)
            retained.append(steps[stage, action, field])
    if not retained:
        return None
    content = parent + ''.join(rules).encode()
    digest = hashlib.sha256(content).hexdigest()
    version = hashlib.sha256((task_digest + trace.run_id + digest).encode()).hexdigest()
    folder = (root or ROOT / 'outputs' / 'skill_history' / 'sms' / 'candidates') / version
    folder.mkdir(parents=True, exist_ok=False)
    diff = ''.join(difflib.unified_diff(parent.decode().splitlines(keepends=True), content.decode().splitlines(keepends=True),
                                        fromfile='parent/SKILL.md', tofile='candidate/SKILL.md'))
    provenance = {'version': version, 'run_id': trace.run_id, 'content_sha256': digest,
                  'parent_sha256': hashlib.sha256(parent).hexdigest(), 'task_sha256': task_digest, 'approved': False,
                  'validation': 'VERIFIED_ACTION_READBACK', 'lessons': [lesson.model_dump() for lesson in retained]}
    (folder / 'SKILL.md').write_bytes(content)
    (folder / 'strategy.diff').write_text(diff, encoding='utf-8')
    (folder / 'version.json').write_text(json.dumps(provenance, indent=2) + '\n', encoding='utf-8')
    return {**provenance, 'diff': str(folder / 'strategy.diff')}


async def review_preflight(trace, settings, budget, directory, observation, *, observer=None, task_digest=None, skill=None,
                           tag_focus=None):
    from modules.sms.executor import CAPABILITIES
    from modules.sms.vision import sms_screenshot

    trace.agent_phase = 'teacher'
    evidence = ordered_evidence(trace, observation)
    if tag_focus is not None:
        evidence['review_focus'] = ('Diagnose the Add tags binding. The campaign-name action was already verified and does '
                                    'not resolve this blocker. Compare the scoped DOM, nearby controls, accessibility roles, '
                                    'and the crop if supplied. Identify an actionable widget and how an existing tag '
                                    'can be selected and independently read back, or state the exact missing capability. '
                                    'Use the binding_checks and actual MCP results to identify the failed predicate. '
                                    'An initially closed popup or absent option does not disprove initial binding. '
                                    'Distinguish a proposed operation from a verified one.')
    events = evidence['chronological_events']
    refs = {item['id'] for item in events}
    trace.agent_event('sms_teacher_evidence_prepared', role='teacher', evidence_refs=sorted(refs),
                      scoped_dom=bool(observation), recovered_checks=evidence['recovered_checks'])
    screenshot_status = 'NOT_REQUESTED'

    async def view():
        nonlocal screenshot_status
        if screenshot_status != 'NOT_REQUESTED':
            return [{'type': 'text', 'text': 'SMS screenshot already requested'}], {}
        screenshot_status = 'SMS_SAFE_CROP_UNAVAILABLE'
        try:
            async with asyncio.timeout(8):
                screenshot = await sms_screenshot(observer)
        except Exception:
            screenshot = None
        if not screenshot:
            denials = [event['agent_execution'] for event in trace.events
                       if event.get('agent_execution', {}).get('kind') == 'sms_teacher_screenshot_unavailable']
            details = ({name: denials[-1][name] for name in ('code', 'complete', 'unknown_roots', 'denied_roots')
                        if name in denials[-1]} if denials else {})
            return [{'type': 'text', 'text': json.dumps({'status': screenshot_status, **details})}], {}
        screenshot_status = 'CAPTURED_REDACTED'
        (directory / 'sms-teacher-crop-redacted.png').write_bytes(screenshot)
        trace.agent_event('sms_teacher_screenshot', role='teacher', status=screenshot_status,
                          image_sha256=hashlib.sha256(screenshot).hexdigest())
        return [{'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,' + base64.b64encode(screenshot).decode(),
                                                 'detail': 'low'}}], {}

    async def read_evidence():
        return evidence_index(evidence)

    tools = []
    can_view = bool(observer and observation and budget.role_limits.get('teacher', 0) > 1)
    if observer and observation and budget.role_limits.get('teacher', 0) > 1:
        tools = [StructuredTool.from_function(coroutine=read_evidence, name='read_sms_review_evidence', args_schema=EmptyArguments,
                 description='Index the supplied trace and scoped DOM/accessibility by event ID and Tag predicate.'),
                 StructuredTool.from_function(coroutine=view, name='view_sms_stage', args_schema=EmptyArguments,
                 description='Request one focused, privacy-masked stage image only for an unresolved layout question.',
                 response_format='content_and_artifact')]
    trace.agent_event('sms_teacher_catalog', role='teacher', stage=getattr(observer, 'stage', None),
                      tool_names=[tool.name for tool in tools], supplied_evidence=['redacted_trace', 'scoped_dom', 'accessibility'])
    try:
        model = openai_model('teacher', settings, trace, budget)
        required_observation = None
        if tag_focus is not None and can_view:
            required_observation = 'read_sms_review_evidence' if tag_focus.get('binding_kind') else 'view_sms_stage'
        agent, _ = create_teacher(model, trace, budget, schema=SMSPreflightReview, observation_tools=tools,
            first_observation=required_observation, vision_observation=required_observation or 'view_sms_stage', prompt_override=(
            'Review the live SMS evidence in chronological order. Scoped DOM is already supplied. '
            'Use current_setup_state and recovered_checks: an earlier failed route check does not negate a later verified read. '
            'Distinguish operator confirmation, exact-name availability, Setup visibility, and tenant timezone. '
            'Cite supplied event IDs only. Do not infer a blank page cause, name availability, or timezone without evidence. '
            'Lessons are optional and cite only actual task Doer actions and later matching independent verified readbacks. '
            'Use CODE_FIX_REQUIRED for evidenced deterministic contract, binder, or executor defects. '
            'For a code defect return no lessons, identify the exact missing or broken contract, and cite actual evidence. '
            'Return no lessons when no task actions ran. Do not request another live attempt. '
            'For Tag investigations, distinguish an accessible Add tags label from the actionable input or button, '
            'including its DOM type and any existing-option ownership evidence.'))
        with tracing_context(enabled=False):
            async with asyncio.timeout(45):
                result = await agent.ainvoke({'messages': [{'role': 'user', 'content': json.dumps(evidence)}]},
                    {'configurable': {'thread_id': trace.run_id + '-sms-teacher'}, 'recursion_limit': 6})
        response = SMSPreflightReview.model_validate(result['structured_response'])
        if not response.evidence_refs or not set(response.evidence_refs) <= refs or response.setup_state != evidence['current_setup_state']:
            assessment = {'status': 'ANALYSIS_REJECTED', 'error': 'TEACHER_EVIDENCE_INVALID'}
        else:
            assessment = {'status': 'ANALYZED', **trace.redact_agent(response.model_dump())}
            try:
                steps = validate_lessons(response.lessons, events, CAPABILITIES)
                if tag_focus is not None:
                    steps = {key: lesson for key, lesson in steps.items() if key == ('setup', 'set_sms_field', 'tag')}
            except ValueError:
                assessment['candidate_status'] = 'SMS_LESSON_EXECUTION_EVIDENCE_INVALID'
            else:
                if task_digest and steps and response.classification == 'MODEL_STRATEGY':
                    assessment['candidate'] = save_candidate(trace, task_digest, steps, skill=skill)
    except Exception as error:
        assessment = {'status': 'ANALYSIS_UNAVAILABLE', 'error': type(error).__name__}
    trace.teacher_result = assessment['status']
    assessment.setdefault('candidate_status', 'UNAPPROVED_CANDIDATE' if assessment.get('candidate') else
                          'NO_SUPPORTED_LESSON' if task_digest else 'NO_TASK_DOER_EXECUTION_EVIDENCE')
    assessment['screenshot_status'] = screenshot_status
    (directory / 'sms-teacher-review.json').write_text(json.dumps(assessment, indent=2) + '\n', encoding='utf-8')
    trace.agent_event('sms_teacher_result', role='teacher', **assessment)
    return assessment
