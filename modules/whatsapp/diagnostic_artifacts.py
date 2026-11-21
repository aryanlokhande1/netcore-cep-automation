import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4


ARTIFACT_NAMES = (
    "report.md",
    "live-terminal.log",
    "terminal-events.jsonl",
    "diagnostic-session-safety.json",
    "candidate-bindings.json",
    "test-results.txt",
    "operator-decisions.jsonl",
    "agent-trace.json",
    "login-verification.json",
    "dashboard-candidates.json",
    "tenant-evidence.json",
    "resource-evidence.json",
    "shadow-scope.json",
    "whatsapp-doer-actions.json",
    "whatsapp-page-observations.json",
    "langgraph-transitions.json",
    "skill-usage.json",
    "teacher-review.json",
    "model-usage.json",
    "source-changes.diff",
    "evidence-manifest.json",
)

_GRAPH_EVENT_KINDS = {
    "graph_started",
    "graph_node_started",
    "graph_node_finished",
    "graph_route",
    "graph_route_selected",
    "graph_failed",
    "workflow_finished",
}
_ACTION_EVENT_KINDS = {
    "tool_invocation",
    "tool_result",
    "browser_action_started",
    "browser_action",
    "browser_action_rejected",
    "readonly_whatsapp_navigation",
}
_VALUE_KEYS = {"observed_value", "raw_value", "value"}


def _strip_values(value):
    if isinstance(value, dict):
        return {
            key: _strip_values(item)
            for key, item in value.items()
            if str(key).lower() not in _VALUE_KEYS
        }
    if isinstance(value, (tuple, list)):
        return [_strip_values(item) for item in value]
    return value


def _json_text(value, trace):
    return json.dumps(trace.redact_agent(_strip_values(value)), indent=2, sort_keys=True, ensure_ascii=True) + "\n"


def _atomic_write(path, content):
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    try:
        with temporary.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        temporary.replace(path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _event_page_observed(events):
    for event in events:
        result = event.get("result")
        if not isinstance(result, dict):
            continue
        if event.get("kind") == "whatsapp_diagnostic_observation" and result.get("whatsapp_page_observed") is True:
            return True
        if (event.get("kind") == "dom_observation" and result.get("stage") == "setup"
                and result.get("stage_visible") is True):
            return True
    return False


def _stage_statuses(page_observed, result):
    if page_observed:
        statuses = {
            "landing_page": "OBSERVED",
            "setup": "UNVERIFIED",
            "audience": "UNVERIFIED",
            "content": "UNVERIFIED",
            "schedule": "UNVERIFIED",
        }
    elif result.get("operator_aborted") is True or result.get("status") == "BLOCKED_OPERATOR_REVIEW":
        statuses = {stage: "NOT_REACHED" for stage in ("landing_page", "setup", "audience", "content", "schedule")}
    elif result.get("status") in {"BLOCKED_SECURITY_OR_SCOPE", "BLOCKED_WHATSAPP_TOOLS"}:
        statuses = {stage: "BLOCKED" for stage in ("landing_page", "setup", "audience", "content", "schedule")}
    else:
        statuses = {stage: "NOT_REACHED" for stage in ("landing_page", "setup", "audience", "content", "schedule")}
    if 'pre_cleanup_evidence' in result:
        evaluation = result.get('evaluation') or {}
        if evaluation.get('status'):
            statuses['setup'] = 'VERIFIED' if evaluation['status'] == 'COMPLETE' else evaluation['status']
        for stage in ('audience', 'content', 'schedule'):
            statuses[stage] = 'NOT_REACHED'
    return [{"stage": stage, "status": status} for stage, status in statuses.items()]


def classify_human_assisted_outcome(result, events):
    failure = result.get("failure") or {}
    status = result.get("status")
    page_observed = _event_page_observed(events)
    if failure.get("unsafe") is True or failure.get('owner')=='authorization':
        return "BLOCKED_SECURITY_OR_SCOPE"
    if any(event.get('kind')=='operator_run_rejected' and
           event.get('code') in {'CURRENT_AUTHORIZED_DOCUMENT_NOT_PROVEN','DIAGNOSTIC_SECURITY_STATE_UNRESOLVED'}
           for event in events):
        return 'BLOCKED_SECURITY_OR_SCOPE'
    if status == "BLOCKED_WHATSAPP_TOOLS" or failure.get("owner") in {"tool", "model"}:
        return "BLOCKED_WHATSAPP_TOOLS"
    if status == "BLOCKED_AUTHENTICATION_EVIDENCE":
        return "BLOCKED_AUTHENTICATION_EVIDENCE"
    if status in {'SETUP_WRITE_DECLINED', 'SETUP_WRITE_APPROVAL_TIMEOUT',
                  'OPERATOR_TENANT_DECLINED', 'OPERATOR_TENANT_CONFIRMATION_TIMEOUT'}:
        return status
    if status in {'PAGE1_AUDIENCE_VISIBLE', 'PAGE1_NEXT_STEP_DECLINED',
                  'PAGE1_NEXT_STEP_APPROVAL_TIMEOUT', 'PAGE1_TRANSITION_UNRESOLVED'}:
        return status
    if result.get('operator_confirmed') is True and (result.get('evaluation') or {}).get('status') == 'COMPLETE':
        return 'OPERATOR_CONFIRMED_WHATSAPP_SETUP_COMPLETE'
    if status == "BLOCKED_OPERATOR_REVIEW" or result.get("operator_aborted") is True:
        return "BLOCKED_OPERATOR_REVIEW"
    if status == "BLOCKED_SECURITY_OR_SCOPE":
        return "BLOCKED_SECURITY_OR_SCOPE"
    if status in {'STUCK','INCOMPLETE','BLOCKED'}:
        return status
    if result.get("authentication_verified") is True:
        if (result.get("evaluation") or {}).get("status") == "COMPLETE":
            return "VERIFIED_AUTHENTICATION_MANUAL_FORM_TEST"
        if result.get("task_doer_invoked") is True and page_observed:
            return "VERIFIED_AUTHENTICATION_AND_WHATSAPP_DOER"
        return "VERIFIED_AUTHENTICATION_ONLY"
    if result.get("task_doer_invoked") is True:
        if result.get("diagnostic_consent") is True and page_observed:
            return "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC_SUCCEEDED"
        return "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC_PARTIAL"
    return "BLOCKED_AUTHENTICATION_EVIDENCE"


def _write_json(directory, name, value, trace):
    _atomic_write(directory / name, _json_text(value, trace))


def append_live_terminal_event(path, event, trace):
    event=trace.redact_agent(_strip_values(event))
    facts=event.get('diagnostic') if event.get('kind')=='browser_diagnostic' else event
    facts=facts if isinstance(facts,dict) else {}
    keys=('state','status','node','source','destination','route','reason','code','tool','action',
        'verification','gate_status','failed_predicates','diagnostic_session_safe','choice','valid',
        'input_kind','accepted','authentication_verified','diagnostic_consent','cleanup','usage',
        'duration_ms','reference','stale','visible_owned_window_count','headed_configured',
        'unexpected','missing','stage_visible','actionable_fields','unresolved_fields',
        'verified_fields','failed_approaches','prior_input_reserved','prior_output_reserved',
        'input_bound_with_margin','max_output_tokens','teacher_input_reserve','teacher_output_reserve',
        'input_limit','output_limit','input_over_limit','output_over_limit',
        'budget_input_reserved','budget_output_reserved','budget_input_limit','budget_output_limit',
        'budget_input_over_limit','budget_output_over_limit',
        'user_message_count','other_message_count','login_output_cap','task_output_cap','requests',
        'actions','read_only','source_version','content_sha256','evidence_ref','version',
        'scope_reason_counts','changed_properties')
    meaningful=not (event.get('kind')=='browser_diagnostic' and event.get('component')=='GUARD'
        and facts.get('state') in {'COMMAND_STARTED','COMMAND_COMPLETED','REQUEST_PAUSED','FRAME_TREE_SNAPSHOT'})
    line = "[{timestamp}][{run}][{source}] {phase} {kind} {event}\n".format(
        timestamp=event.get("timestamp", "TIME_UNAVAILABLE"),
        run=event.get('run_id','RUN_UNAVAILABLE'),
        source=event.get('id','SOURCE_UNAVAILABLE'),
        phase=str(event.get("phase", "event")).upper(),
        kind=str(event.get("kind", "event")).upper(),
        event=json.dumps({**({'component':event['component']} if 'component' in event else {}),
            **{key:facts[key] for key in keys if key in facts}},sort_keys=True,ensure_ascii=True),
    )
    if meaningful:
        with Path(path).open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(line)
            stream.flush()
    with Path(path).with_name('terminal-events.jsonl').open('a',encoding='utf-8',newline='\n') as stream:
        stream.write(json.dumps(event,sort_keys=True,ensure_ascii=True)+'\n')
        stream.flush()
    return line if meaningful else None


def _candidate_rows(candidate_evidence, kind):
    if not isinstance(candidate_evidence, dict):
        return []
    rows = candidate_evidence.get(kind, [])
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _model_request_summaries(trace):
    fields = (
        "invocation_id",
        "role",
        "stage",
        "status",
        "usage_recorded",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cached_input_tokens",
        "reasoning_tokens",
        "duration_ms",
        "failure",
    )
    return [
        {key: record.get(key) for key in fields if key in record}
        for record in trace.model_diagnostics
        if record.get("stage") == "MODEL_CALL"
    ]


def _failure_report_lines(result):
    failure = result.get("failure")
    if not isinstance(failure, dict):
        return []
    code = failure.get("code")
    if not isinstance(code, str) or not re.fullmatch(r"[A-Z][A-Z0-9_]{0,79}", code):
        code = "DIAGNOSTIC_EXECUTION_BLOCKED"
    stage = failure.get("failing_stage")
    if not isinstance(stage, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", stage):
        stage = "workflow"
    owner = failure.get("owner")
    if owner not in {
        "authentication", "authorization", "browser", "controller",
        "model", "recognition", "tool", "uncertain_write",
    }:
        owner = "controller"
    exception_class = failure.get("exception_class")
    if not isinstance(exception_class, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", exception_class):
        exception_class = "RuntimeError"
    lines = [
        "",
        "## Failure",
        f"- Failure code: `{code}`",
        f"- Exception class: `{exception_class}`",
        f"- Failure stage: `{stage}`",
        f"- Failure owner: `{owner}`",
    ]
    operation = failure.get("operation")
    if isinstance(operation, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+", operation
    ):
        lines.append(f"- Timed-out operation: `{operation}`")
    budget_kind = failure.get("budget_kind")
    if budget_kind in {"ACTION_LIMIT", "SESSION_DEADLINE", "REVIEW_DEADLINE"}:
        lines.append(f"- Exhausted browser budget: `{budget_kind}`")
    used = failure.get("actions_used")
    limit = failure.get("actions_limit")
    if type(used) is int and type(limit) is int and used >= 0 and limit > 0:
        lines.append(f"- Browser actions: `{used}/{limit}`")
    return lines


def save_setup_evidence(directory, trace, tools, evaluation, failure, *, screenshot=None):
    observation = tools.last_observation or {}
    record = {
        'status': 'CAPTURED' if observation or tools.events else 'NO_BROWSER_EVIDENCE',
        'observation': {key: observation[key] for key in (
            'stage', 'stage_visible', 'field_evidence', 'unresolved_fields', 'scope_basis',
            'recognition_status', 'binding_failures', 'ga_row_evidence', 'evidence_ref',
            'audience_picker_evidence', 'audience_section_evidence') if key in observation},
        'last_independent_readback': getattr(tools, 'last_independent_readback', {}),
        'attempts': [{key: event[key] for key in (
            'id', 'trace_ref', 'field', 'action', 'approach', 'attempted', 'valid_approach',
            'ok', 'error', 'owner', 'verification') if key in event} for event in tools.events[-24:]],
        'evaluation': evaluation,
        'failure': failure,
        'screenshots': screenshot or 'NOT_AUTHORIZED_IN_HUMAN_ASSISTED_MODE',
    }
    name = 'setup-evidence-before-cleanup.json'
    _write_json(directory, name, record, trace)
    trace.agent_event('setup_evidence_saved', role='observer', status=record['status'],
                      evidence_ref=name)
    return {'path': name, 'status': record['status'], 'observation': record['observation'],
            'last_independent_readback': record['last_independent_readback'],
            'screenshots': record['screenshots']}


def show_setup_candidate(directory, trace, context):
    import difflib
    from learning.skill_storage.registry import whatsapp_history

    verified_fields = context.get('evaluation', {}).get('verified_fields', [])
    saved = context.get('candidate_result')
    if not saved:
        reason = context.get('candidate_error')
        if not reason:
            if context['review_kind'] != 'recoverable_strategy':
                reason = context['review_kind'].upper()
            elif trace.teacher_result != 'ANALYZED':
                reason = trace.teacher_result
            else:
                reason = 'NO_ELIGIBLE_NEW_BROWSER_CORRECTION'
        review = {'status': 'NO_CANDIDATE', 'reason': reason, 'verified_fields': verified_fields,
                  'next_action': 'Review the saved evidence before selecting a strategy for a new run.'}
        print('SETUP_CANDIDATE ' + json.dumps(review), flush=True)
        return review
    candidate_path = whatsapp_history() / 'candidates' / saved['version'] / 'SKILL.md'
    content = candidate_path.read_bytes()
    if hashlib.sha256(content).hexdigest() != saved['content_sha256']:
        raise ValueError('Saved candidate digest changed before display')
    candidate = content.decode('utf-8')
    diff = ''.join(difflib.unified_diff(context['skill'].content.splitlines(keepends=True),
        candidate.splitlines(keepends=True), fromfile='loaded-task-skill.md', tofile='candidate/SKILL.md'))
    diff_path = directory / 'candidate.diff'
    _atomic_write(diff_path, diff)
    review = {**saved, 'strategy_path': str(candidate_path), 'diff_path': str(diff_path),
        'report_path': str(directory / 'report.md'), 'verified_fields': verified_fields,
        'next_action': 'Review the complete candidate and diff. Start a separate run only after explicitly selecting the version and SHA-256.'}
    print('SETUP_CANDIDATE ' + json.dumps(review), flush=True)
    print(candidate, end='' if candidate.endswith('\n') else '\n', flush=True)
    print(diff, end='' if diff.endswith('\n') else '\n', flush=True)
    trace.agent_event('setup_candidate_displayed', role='teacher', status=saved['status'],
                      version=saved['version'], content_sha256=saved['content_sha256'])
    return review


def write_human_assisted_artifacts(
    output_root,
    *,
    trace,
    result,
    operator_decisions=None,
    candidate_evidence=None,
    resource_evidence=None,
    shadow_scope=None,
    page_observations=None,
    terminal_log="",
    source_diff=None,
):
    run_id=result.get("run_id")
    if not isinstance(run_id, str) or not re.fullmatch(r"[A-Za-z0-9-]{1,128}", run_id):
        raise ValueError("Invalid human-assisted run identifier")
    directory = Path(output_root) / f"login-whatsapp-{run_id}"
    directory.mkdir(parents=True, exist_ok=True)
    events = [event["agent_execution"] for event in trace.events if "agent_execution" in event]
    outcome = classify_human_assisted_outcome(result, events)
    # Live events were sanitized before append. Never re-redact the entire file:
    # redact_agent intentionally bounds each string to 12000 characters.
    if not (directory / 'live-terminal.log').exists():
        _atomic_write(directory / 'live-terminal.log', ''.join(
            trace.redact_agent(line)+'\n' for line in terminal_log.splitlines()))
    if not (directory/'terminal-events.jsonl').exists():
        _atomic_write(directory/'terminal-events.jsonl',''.join(
            json.dumps(trace.redact_agent(_strip_values(event)),sort_keys=True)+'\n' for event in events))
    _write_json(directory,'diagnostic-session-safety.json',{
        'latest':result.get('diagnostic_session_safety'),
        'events':[e for e in events if e.get('kind')=='diagnostic_session_safety'],
        'authentication_is_separate':True},trace)
    _write_json(directory,'candidate-bindings.json',{
        'bindings':result.get('candidate_bindings'),
        'events':[e for e in events if e.get('kind') in {'operator_candidate_bound','operator_candidate_rejected'}]},trace)
    if not (directory/'test-results.txt').exists():
        _atomic_write(directory/'test-results.txt','NOT_CAPTURED: attach the separately preserved engineering validation results.\n')
    decisions = operator_decisions if operator_decisions is not None else result.get("operator_decisions", [])
    decision_lines = "".join(
        json.dumps(trace.redact_agent(_strip_values(event)), sort_keys=True, ensure_ascii=True) + "\n"
        for event in decisions if isinstance(event, dict)
    )
    _atomic_write(directory / "operator-decisions.jsonl", decision_lines)
    _write_json(directory, "agent-trace.json", {
        "run_id": run_id,
        "events": events,
        "model_diagnostics": trace.model_diagnostics,
        "boundary_diagnostics": trace.boundary_diagnostics,
    }, trace)
    _write_json(directory, "login-verification.json", {
        'operator_confirmed': result.get('operator_confirmed') is True,
        'last_verified_step': result.get('last_verified_step', 'NOT_ESTABLISHED'),
        "status": result.get("login_verification", "NOT_OBSERVED"),
        "authentication_verified": result.get("authentication_verified") is True,
        "gates": result.get("login_gate_status") or {},
        "credential_submissions": result.get("login_submissions"),
        "evidence_configuration": result.get("evidence_configuration", "NOT_CAPTURED"),
    }, trace)
    _write_json(directory, "dashboard-candidates.json", {
        "status": "CAPTURED" if _candidate_rows(candidate_evidence, "dashboard") else "NOT_CAPTURED",
        "candidates": _candidate_rows(candidate_evidence, "dashboard"),
    }, trace)
    _write_json(directory, "tenant-evidence.json", {
        "status": "CAPTURED" if _candidate_rows(candidate_evidence, "tenant") else "NOT_CAPTURED",
        "candidates": _candidate_rows(candidate_evidence, "tenant"),
        "login_gates": result.get("login_gate_status") or {},
    }, trace)
    safe_resources = _strip_values(resource_evidence) if resource_evidence is not None else {
        "status": "NOT_CAPTURED",
        "options": [],
    }
    _write_json(directory, "resource-evidence.json", safe_resources, trace)
    _write_json(directory, "shadow-scope.json", shadow_scope if shadow_scope is not None else {
        "status": "NOT_CAPTURED",
        "reason": "No current shadow-scope observation was recorded.",
    }, trace)
    task_events=[event for event in events if event.get('phase')=='task_doer' and event.get('kind') in _ACTION_EVENT_KINDS]
    _write_json(directory, "whatsapp-doer-actions.json", {
        "status": "CAPTURED" if task_events else "NOT_INVOKED" if not result.get('task_doer_invoked') else "NOT_CAPTURED",
        "events": task_events,
        "tool_event_count": result.get("task_tool_event_count"),
    }, trace)
    observed_page = _event_page_observed(events)
    observation_record = dict(page_observations) if isinstance(page_observations, dict) else {
        "status": "CAPTURED" if observed_page else "NOT_CAPTURED",
        "whatsapp_page_observed": observed_page,
    }
    observation_record.setdefault("stages", _stage_statuses(observed_page, result))
    _write_json(directory, "whatsapp-page-observations.json", observation_record, trace)
    _write_json(directory, "langgraph-transitions.json", {
        "status": "CAPTURED" if any(event.get("kind") in _GRAPH_EVENT_KINDS for event in events) else "NOT_CAPTURED",
        "events": [event for event in events if event.get("kind") in _GRAPH_EVENT_KINDS],
    }, trace)
    _write_json(directory, "skill-usage.json", {
        "selected_skill_sha256": result.get("selected_skill_sha256"),
        "selected_skill_version": result.get("selected_skill_version"),
        "approved": result.get("selected_skill_approved"),
        "status": result.get("selected_skill_status", "NOT_CAPTURED"),
        "loaded_into_task_prompt":any(e.get('kind')=='browser_diagnostic' and
            e.get('component')=='WHATSAPP_V2' and e.get('diagnostic',{}).get('state')=='SKILL_READ' for e in events),
        "task_model_invoked":any(e.get('kind')=='model_invocation' and e.get('phase')=='task_doer' for e in events),
    }, trace)
    _write_json(directory, "teacher-review.json", {
        "status": result.get("teacher_status", "NOT_INVOKED_DIAGNOSTIC_ONLY"),
        "invoked": any(e.get('kind')=='teacher_invocation' for e in events),
        "assessment": result.get('teacher_review'),
        "candidate": result.get('candidate'),
        "dispatch_ready": result.get('teacher_dispatch_ready'),
        "provider_response_returned": result.get('teacher_response_returned'),
        "analysis_returned": result.get('teacher_status') == 'ANALYZED',
        "candidate_written": bool(result.get('candidate')),
        "candidate_review": result.get('candidate_review'),
    }, trace)
    _write_json(directory, "model-usage.json", {
        "provider_requests": result.get("provider_requests"),
        "provider_dispatch_ready_requests": result.get("provider_dispatch_ready_requests"),
        "iteration": trace.iteration,
        "login_doer": result.get("login_doer_usage"),
        "task_doer": result.get("task_doer_usage"),
        "observer": {"status": "NOT_INVOKED_DIAGNOSTIC_ONLY", "usage": None},
        "teacher": {"status": result.get('teacher_status'), "usage": result.get('teacher_usage')},
        "token_usage": result.get("token_usage"),
        "token_reservations": result.get("token_reservations"),
        "usage_available": result.get("token_usage") is not None,
        "model_requests": _model_request_summaries(trace),
    }, trace)
    diff = source_diff if source_diff is not None else (
        "NOT_CAPTURED_BASELINE_UNAVAILABLE\n"
        "The approved worktree already contained uncommitted changes when this task began.\n"
    )
    _atomic_write(directory / "source-changes.diff", ''.join(trace.redact_agent(line)+'\n' for line in diff.splitlines()))
    report = "\n".join((
        "# Human-assisted Login and WhatsApp diagnostic",
        "",
        f"- Run: `{run_id}`",
        f"- Outcome: **{outcome}**",
        f"- Authentication independently verified: `{result.get('authentication_verified') is True}`",
        f"- Login verification: `{result.get('login_verification', 'NOT_OBSERVED')}`",
        f"- Login credential submissions: `{result.get('login_submissions', 'NOT_CAPTURED')}`",
        f"- Diagnostic consent: `{result.get('diagnostic_consent') is True}`",
        f"- WhatsApp Doer invoked: `{result.get('task_doer_invoked') is True}`",
        f"- WhatsApp page observed: `{_event_page_observed(events)}`",
        *[f"- {stage.title().replace('_', ' ')}: `{status}`" for stage, status in (
            (item["stage"], item["status"]) for item in _stage_statuses(_event_page_observed(events), result)
        ) if stage != "landing_page"],
        f"- Teacher: `{result.get('teacher_status', 'NOT_INVOKED_DIAGNOSTIC_ONLY')}`",
        f"- Teacher dispatch-ready requests: `{result.get('teacher_dispatch_ready')}`",
        f"- Written Setup fields: {', '.join(result.get('written_fields') or []) or 'none'}",
        f"- Verified Setup fields: `{', '.join((result.get('evaluation') or {}).get('verified_fields', [])) or 'none'}`",
        f"- Strategy version and SHA-256: `{result.get('selected_skill_version')}` `{result.get('selected_skill_sha256')}`",
        f"- Candidate: `{(result.get('candidate_review') or {}).get('status', 'NO_CANDIDATE')}`",
        f"- Candidate decision: `{(result.get('candidate_review') or {}).get('reason') or (result.get('candidate_review') or {}).get('next_action') or result.get('teacher_status')}`",
        f"- Candidate diff: `{(result.get('candidate_review') or {}).get('diff_path', 'none')}`",
        f"- Cleanup: `{result.get('cleanup', 'NOT_CAPTURED')}`",
        f"- Provider request count: `{result.get('provider_requests')}`",
        f"- Token usage available: `{result.get('token_usage') is not None}`",
        *_failure_report_lines(result),
        "",
        "Missing evidence is marked `NOT_CAPTURED`; no authentication or campaign-write success is inferred from operator consent.",
        "",
    ))
    _atomic_write(directory / "report.md", trace.redact_agent(report))
    manifest = {
        "schema_version": 1,
        "run_id": run_id,
        "outcome": outcome,
        "screenshots": "NOT_CAPTURED",
        "artifacts": {},
    }
    review_files = ('loaded-task-skill.md', 'setup-evidence-before-cleanup.json',
                    'teacher-input-evidence.json', 'teacher-request-evidence.json', 'candidate.diff')
    for name in (*ARTIFACT_NAMES, *(name for name in review_files if (directory / name).is_file())):
        if name == "evidence-manifest.json":
            continue
        content = (directory / name).read_bytes()
        manifest["artifacts"][name] = {
            "sha256": hashlib.sha256(content).hexdigest(),
            "bytes": len(content),
        }
    _write_json(directory, "evidence-manifest.json", manifest, trace)
    return directory
