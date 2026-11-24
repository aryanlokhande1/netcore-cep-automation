"""Trusted WhatsApp step scopes and evidence-backed candidate artifacts."""

import difflib
import hashlib
import json
from pathlib import Path
from types import MappingProxyType

from app.browser_mcp_client import StepTools, open_step
from modules.whatsapp.contract import STAGES, V2Stop, v2_action_arguments


_OBSERVE = frozenset({'observe', 'dom', 'inspect', 'wait', 'scroll', 'verify', 'readback'})
_PICKER = frozenset({'click', 'options', 'search', 'select'})
_DOER = MappingProxyType({
    'setup': _OBSERVE | _PICKER | {'type'},
    'audience': _OBSERVE | _PICKER,
    'content': _OBSERVE | _PICKER | {'type'},
    'schedule': _OBSERVE | _PICKER | {'type'},
    'preview': _OBSERVE,
})


def doer_step(tools):
    operations = _DOER[tools.stage] & v2_action_arguments(tools.action_schema()).keys()
    if tools.read_only and tools.stage == 'setup':
        operations |= {'navigate'} & v2_action_arguments(tools.action_schema()).keys()
    if tools.stage == 'setup' and getattr(tools, 'mcp_ga_scope', False) and not tools.read_only:
        operations |= {'reveal_ga_advanced'}
    return StepTools('doer', tools.stage, frozenset(operations))


OBSERVER_STEPS = MappingProxyType({stage: StepTools('observer', stage, frozenset({'observe', 'readback'}))
                                  for stage in STAGES})
GA_TEACHER_STEP = StepTools('teacher', 'setup', frozenset({'trace', 'inspect_ga_setup', 'view_ga_setup'}))
LOGIN_STEP = StepTools('doer', 'login', frozenset({'navigate_approved','inspect_page','submit_login','verify_dashboard'}))

def ga_strategy_sha256():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


async def mcp_ga_switch(tools, held, *, retry_procedures=(), select_action=None):
    """Open the reviewed disclosure, resolve the scoped control, and verify its boolean."""
    from modules.whatsapp.page1_fields import controls_for

    trace = tools.browser.trace

    async def readback(checkpoint):
        trace.agent_phase = 'observer'
        async with open_step(tools, StepTools('observer', 'setup', frozenset({'readback'}))) as client:
            facts = await client.read('independent_readback')
        actual = facts.get('ga_readback', {}).get('ga_tracking')
        trace.agent_event('mcp_ga_boolean_readback', role='observer', checkpoint=checkpoint, actual=actual,
                          expected_state=tools.payload.ga_tracking, evidence_ref=facts.get('evidence_ref'),
                          stage_visible=facts.get('stage_visible'))
        return actual if facts.get('stage_visible') is True else None

    tools.mcp_ga_scope = True
    tools.mcp_ga_switch_only = True
    # Campaign Name identifies the correct Setup form when there are multiple forms.
    tools.ga_setup_fields = ('campaign_name', 'ga_tracking')
    trace.agent_phase = 'task_doer'
    async with open_step(tools, StepTools('doer', 'setup', frozenset({'dom', 'reveal_ga_advanced'}))) as client:
        initial = await client.read('browser_dom')
        if initial.get('stage_visible') is not True:
            raise V2Stop('MCP_GA_SETUP_NOT_VISIBLE', owner='recognition')
        if tools.ga_advanced_selector:
            await client.read('reveal_ga_advanced')

    view = await held.reveal_viewport()
    trace.agent_event('mcp_ga_viewport', role='controller', **view)
    methods = ['observe', 'dom']
    if retry_procedures:
        # Follow the validated evidence-driven ordering, keeping fixed safety and readback gates.
        preferred = [step['action'] for step in retry_procedures if step['action'] in methods]
        if preferred:
            methods = list(dict.fromkeys(preferred + methods))
        trace.agent_event('mcp_ga_candidate_applied', role='controller',
                          actions=[step['action'] for step in retry_procedures], observation_order=methods)

    failures = []
    controls = []
    for method in methods:
        trace.agent_phase = 'task_doer'
        async with open_step(tools, StepTools('doer', 'setup', frozenset({method}))) as client:
            observed = await client.read('browser_' + method)
        if observed.get('ok') is not True or observed.get('stage_visible') is not True:
            raise V2Stop(observed.get('error') or 'MCP_GA_SETUP_NOT_VISIBLE', owner='recognition')
        controls = controls_for(tools, 'ga_tracking')
        actual = await readback('BEFORE')
        code = ('MATCH' if actual is tools.payload.ga_tracking else 'BOUND' if type(actual) is bool and len(controls) == 1
                else 'GA_CONTROL_OR_BOOLEAN_NOT_ESTABLISHED')
        trace.agent_event('mcp_ga_approach', role='observer', method=method, code=code,
                          evidence_ref=observed.get('evidence_ref'), control_count=len(controls))
        if code != 'GA_CONTROL_OR_BOOLEAN_NOT_ESTABLISHED':
            break
        failures.append(method)
    else:
        return {'status': 'STUCK', 'failed_approaches': failures, 'actual': actual}

    if code == 'MATCH':
        tools.read_only = True
        actual = await readback('AFTER')
        if actual is not tools.payload.ga_tracking:
            raise V2Stop('MCP_GA_BOOLEAN_READBACK_MISMATCH', owner='recognition')
        return {'status': 'MCP_GA_VERIFIED', 'actual': actual, 'written': False}
    reference = controls[0]['reference']
    trace.agent_phase = 'task_doer'
    async with open_step(tools, StepTools('doer', 'setup', frozenset({'type'}),
                                         ga_tracking_reference=reference)) as client:
        if select_action is None:
            result = await client.read('browser_type', {'field': 'ga_tracking', 'reference': reference})
        else:
            result = await select_action(client, reference)
    tools.read_only = True
    if result.get('ok') is not True:
        raise V2Stop(result.get('error') or 'MCP_GA_TOGGLE_FAILED',
                     owner=result.get('owner') or 'tool', unsafe=not result.get('retry_safe', False))
    actual = await readback('AFTER')
    if actual is not tools.payload.ga_tracking:
        raise V2Stop('MCP_GA_BOOLEAN_READBACK_MISMATCH', owner='recognition', unsafe=True)
    return {'status': 'MCP_GA_VERIFIED', 'actual': actual, 'written': True}



def login_document(browser):
    guard = browser.guard
    if not guard or not guard.primary_observable() or guard.failed or guard.blocking_hosts:
        raise V2Stop('MCP_LOGIN_OWNED_TARGET_UNAVAILABLE', owner='browser', unsafe=True)
    owner = guard.session_states[guard.primary_session]
    return owner.target, owner.session, owner.generation


def write_strategy(saved, parent, lesson, registry, failure_field):
    folder = registry / 'candidates' / saved['version']
    source = '# WhatsApp strategy\n\n' + '\n'.join(
        f'- Use `browser_{step.action}` for `{step.field or failure_field}` on the owned Setup document, '
        'scoped to Advanced > Google Analytics tracking. Use fresh references. '
        'Verify with `independent_readback` against the approved value before advancing.'
        for step in lesson.instructions) + '\n'
    previous = registry / 'candidates' / str(parent.metadata.get('version')) / 'strategy.md'
    base = previous.read_text(encoding='utf-8') if previous.is_file() else ''
    digest = hashlib.sha256(source.encode('utf-8')).hexdigest()
    (folder / 'strategy.md').write_bytes(source.encode('utf-8'))
    (folder / 'strategy.diff').write_text(''.join(difflib.unified_diff(
        base.splitlines(keepends=True), source.splitlines(keepends=True),
        fromfile='parent/strategy.md', tofile='candidate/strategy.md')), encoding='utf-8')
    (folder / 'strategy-provenance.json').write_text(json.dumps({
        'skill_sha256': saved['content_sha256'], 'strategy_sha256': digest, 'parent_skill_sha256': parent.digest,
        'parent_strategy_sha256': hashlib.sha256(base.encode('utf-8')).hexdigest(),
        'evidence_refs': lesson.evidence_refs, 'approved': False,
    }, indent=2), encoding='utf-8')
    return digest


def require_retry_candidate(snapshot, expected_skill, expected_strategy, folder):
    path = folder / 'strategy.md'
    content = path.read_bytes() if path.is_file() else None
    if (snapshot.approved or snapshot.digest != expected_skill or content is None
            or hashlib.sha256(content).hexdigest() != expected_strategy):
        raise V2Stop('MCP_RETRY_CANDIDATE_DIGEST_MISMATCH', owner='authorization', unsafe=True)
    return content.decode('utf-8')


def retry_candidate(ctx, state, authorization):
    if (not authorization.candidate_evaluation or not authorization.fresh_iterations
            or state['status'] != 'STUCK' or (state.get('failure') or {}).get('unsafe')
            or ctx.get('save_may_have_landed') or not ctx.get('candidate_result') or not ctx.get('strategy_sha256')):
        return {}
    return {'candidate': ctx['candidate_result']['version'],
            'candidate_sha256': ctx['candidate_result']['content_sha256'],
            'strategy_sha256': ctx['strategy_sha256']}
