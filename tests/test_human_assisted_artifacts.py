import json

from app.trace import Trace


EXPECTED_ARTIFACTS = {
    "terminal-events.jsonl",
    "diagnostic-session-safety.json",
    "candidate-bindings.json",
    "test-results.txt",
    "report.md",
    "live-terminal.log",
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
}


def _trace():
    trace = Trace(roles=("doer", "observer"))
    trace.enable_agent_trace(private_values=("PRIVATE-ACCOUNT",))
    return trace


def _result(**updates):
    result = {
        "run_id": "test-run",
        "status": "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC",
        "authentication_verified": False,
        "login_verification": "NOT_VERIFIED",
        "login_gate_status": {"expected_host": "PASS"},
        "login_submissions": 1,
        "diagnostic_consent": True,
        "task_doer_invoked": True,
        "task_doer_status": "STUCK",
        "task_tool_event_count": 1,
        "login_doer_usage": {"requests": 1, "input_tokens": None, "output_tokens": None, "total_tokens": None},
        "task_doer_usage": {"requests": 1, "input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        "selected_skill_sha256": "skill-digest",
        "selected_skill_approved": False,
        "teacher_status": "NOT_INVOKED_DIAGNOSTIC_ONLY",
        "provider_requests": None,
        "provider_dispatch_ready_requests": 2,
        "token_usage": None,
        "model_diagnostics": [],
        "operator_decisions": [],
        "failure": None,
        "cleanup": "CLOSED_AND_PROFILE_REMOVED",
    }
    result.update(updates)
    return result


def test_artifact_writer_creates_exact_diagnostic_artifact_set(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts

    trace = _trace()
    trace.agent_event("graph_started", role="controller")
    directory = write_human_assisted_artifacts(
        tmp_path,
        trace=trace,
        result=_result(),
    )

    assert {path.name for path in directory.iterdir()} == EXPECTED_ARTIFACTS


def test_artifact_writer_preserves_unavailable_states_and_redacts_private_values(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts

    trace = _trace()
    trace.agent_event("operator_decision", role="operator", choice="inspect_tenant")
    trace.agent_event("resource_evidence", role="operator", private_value="PRIVATE-ACCOUNT")
    directory = write_human_assisted_artifacts(
        tmp_path,
        trace=trace,
        result=_result(operator_decisions=[{"choice": "inspect_tenant"}]),
        resource_evidence={"options": [{"key": "tenant", "value_sha256": "digest"}]},
    )

    assert "PRIVATE-ACCOUNT" not in "\n".join(
        path.read_text(encoding="utf-8", errors="replace") for path in directory.iterdir()
    )
    assert "NOT_CAPTURED" in (directory / "shadow-scope.json").read_text(encoding="utf-8")
    assert "NOT_INVOKED_DIAGNOSTIC_ONLY" in (directory / "teacher-review.json").read_text(encoding="utf-8")
    usage = json.loads((directory / "model-usage.json").read_text(encoding="utf-8"))
    assert usage["provider_requests"] is None
    assert usage["token_usage"] is None
    assert "observed_value" not in (directory / "resource-evidence.json").read_text(encoding="utf-8")


def test_outcome_requires_observed_whatsapp_page_not_model_claim():
    from modules.whatsapp.diagnostic_artifacts import classify_human_assisted_outcome

    partial = _result()
    assert classify_human_assisted_outcome(partial, []) == "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC_PARTIAL"

    observed = [{
        "kind": "whatsapp_diagnostic_observation",
        "result": {"whatsapp_page_observed": True},
    }]
    assert classify_human_assisted_outcome(partial, observed) == "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC_SUCCEEDED"

    verified = _result(
        status="VERIFIED_READ_ONLY_WHATSAPP_DOER",
        authentication_verified=True,
    )
    assert classify_human_assisted_outcome(verified, observed) == "VERIFIED_AUTHENTICATION_AND_WHATSAPP_DOER"


def test_operator_review_timeout_is_not_reported_as_security_block(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts

    directory = write_human_assisted_artifacts(
        tmp_path,
        trace=_trace(),
        result=_result(
            status="BLOCKED_SECURITY_OR_SCOPE",
            diagnostic_consent=False,
            task_doer_invoked=False,
            operator_aborted=True,
        ),
    )

    report = (directory / "report.md").read_text(encoding="utf-8")
    observations = json.loads((directory / "whatsapp-page-observations.json").read_text(encoding="utf-8"))
    assert "Outcome: **BLOCKED_OPERATOR_REVIEW**" in report
    assert all(stage["status"] == "NOT_REACHED" for stage in observations["stages"])


def test_live_terminal_event_is_persisted_after_redaction(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import append_live_terminal_event

    path = tmp_path / "live-terminal.log"
    append_live_terminal_event(
        path,
        {"timestamp": "2026-11-18T18:00:00+00:00", "phase": "login", "kind": "status",
         "account": "PRIVATE-ACCOUNT"},
        _trace(),
    )

    content = path.read_text(encoding="utf-8")
    assert "PRIVATE-ACCOUNT" not in content
    assert "2026-11-18T18:00:00+00:00" in content


def test_report_distinguishes_observed_whatsapp_landing_from_unverified_stages(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts

    trace = _trace()
    trace.agent_event(
        "whatsapp_diagnostic_observation",
        role="doer",
        result={"whatsapp_page_observed": True, "controls": []},
    )
    directory = write_human_assisted_artifacts(
        tmp_path,
        trace=trace,
        result=_result(),
    )

    observations = json.loads(
        (directory / "whatsapp-page-observations.json").read_text(encoding="utf-8")
    )
    assert observations["stages"] == [
        {"stage": "landing_page", "status": "OBSERVED"},
        {"stage": "setup", "status": "UNVERIFIED"},
        {"stage": "audience", "status": "UNVERIFIED"},
        {"stage": "content", "status": "UNVERIFIED"},
        {"stage": "schedule", "status": "UNVERIFIED"},
    ]
    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "Setup: `UNVERIFIED`" in report


def test_report_includes_sanitized_failure_code_and_failing_stage(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts

    failure = {
        "code": "CONTROL_IDENTITY_NOT_UNIQUE",
        "owner": "recognition",
        "failing_stage": "setup_observation",
        "exception_class": "SetupStop",
    }
    directory = write_human_assisted_artifacts(
        tmp_path,
        trace=_trace(),
        result=_result(
            status="BLOCKED_WHATSAPP_TOOLS",
            task_doer_invoked=False,
            failure=failure,
        ),
    )

    report = (directory / "report.md").read_text(encoding="utf-8")
    assert "Failure code: `CONTROL_IDENTITY_NOT_UNIQUE`" in report
    assert "Failure stage: `setup_observation`" in report
    assert "Failure owner: `recognition`" in report


def test_diagnostic_failure_metadata_preserves_codes_without_raw_error_text():
    from modules.whatsapp.contract import SetupStop
    from modules.whatsapp.workflow import diagnostic_failure_metadata

    failure = diagnostic_failure_metadata(
        SetupStop("CONTROL_IDENTITY_NOT_UNIQUE"),
        "setup_observation",
    )
    assert failure == {
        "code": "CONTROL_IDENTITY_NOT_UNIQUE",
        "owner": "recognition",
        "failing_stage": "setup_observation",
        "exception_class": "SetupStop",
    }

    unclassified = diagnostic_failure_metadata(
        RuntimeError("private credential-like detail"),
        "task_doer_invocation",
    )
    assert unclassified["code"] == "WORKFLOW_EXCEPTION"
    assert "private credential-like detail" not in repr(unclassified)
