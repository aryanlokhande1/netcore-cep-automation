from types import SimpleNamespace

import pytest

from app.config import ConfigurationError
from app.state import Observation


def test_human_assisted_cli_has_no_campaign_payload_or_schedule_arguments():
    from app.cli import build_parser

    try:
        args = build_parser().parse_args([
            "whatsapp-human-assisted",
            "--authorized-live",
            "--authorized-inference",
            "--authorized-candidate-evaluation",
        ])
    except SystemExit as error:
        pytest.fail(f"payload-free diagnostic command is unavailable: {error}")

    assert args.command == "whatsapp-human-assisted"
    assert args.operator_review_seconds == 300
    assert not hasattr(args, "payload")
    assert not hasattr(args, "schedule")


def test_human_assisted_prompt_uses_300_second_idle_timeout_with_bounded_deadline():
    from modules.whatsapp.workflow import human_assisted_prompt_limits

    assert human_assisted_prompt_limits(deadline=900, now=0) == (300, 600)
    assert human_assisted_prompt_limits(deadline=120, now=0) == (120, 120)


@pytest.mark.parametrize(
    "permission",
    ["authorized_live", "authorized_inference", "authorized_candidate_evaluation"],
)
def test_human_assisted_preflight_requires_each_explicit_permission(permission):
    from modules.whatsapp.workflow import validate_human_assisted_authorization

    args = SimpleNamespace(
        authorized_live=True,
        authorized_inference=True,
        authorized_candidate_evaluation=True,
    )
    setattr(args, permission, False)

    with pytest.raises(ConfigurationError):
        validate_human_assisted_authorization(args, configured_request_cap=12)


def test_human_assisted_budgets_respect_all_fixed_caps_and_lower_configuration():
    from modules.whatsapp.workflow import validate_human_assisted_authorization

    args = SimpleNamespace(
        authorized_live=True,
        authorized_inference=True,
        authorized_candidate_evaluation=True,
    )

    assert validate_human_assisted_authorization(args, configured_request_cap=24) == {
        "login": 6,
        "task": 4,
        "observer": 2,
        "total": 12,
    }
    assert validate_human_assisted_authorization(args, configured_request_cap=5) == {
        "login": 3,
        "task": 2,
        "observer": 0,
        "total": 5,
    }


def test_human_assisted_browser_configuration_forces_headed_and_bounds_session():
    from app.config import Config
    from modules.whatsapp.workflow import human_assisted_browser_config

    configured = human_assisted_browser_config(
        Config("chromium", headless=True),
        session_seconds=240,
        evidence_file="private-evidence.json",
    )

    assert configured.headless is False
    assert configured.timeout_seconds == 240
    assert configured.max_actions == 300
    assert configured.evidence_file == "private-evidence.json"


def test_human_assisted_remaining_session_budget_is_measured_from_session_start():
    from modules.whatsapp.workflow import human_assisted_remaining_seconds

    assert human_assisted_remaining_seconds(900, started=100, now=130) == 870
    assert human_assisted_remaining_seconds(900, started=100, now=1000) == 0


@pytest.mark.parametrize(
    "actions,started,expected_code",
    [
        (30, 0, "BROWSER_ACTION_BUDGET_EXHAUSTED"),
        (0, -901, "BROWSER_SESSION_DEADLINE_EXCEEDED"),
    ],
)
def test_browser_budget_timeout_reports_the_exhausted_budget(actions, started, expected_code):
    from app.config import Config, OriginPolicy
    from app.trace import Trace
    from browser.stagehand_client import BrowserBudgetExceeded, StagehandClient
    from modules.whatsapp.workflow import diagnostic_failure_metadata

    trace = Trace()
    trace.actions = actions
    browser = StagehandClient(
        Config("unused", max_actions=30, timeout_seconds=900),
        OriginPolicy(set()),
        trace,
    )
    browser.started = started

    with pytest.raises(BrowserBudgetExceeded) as caught:
        browser.budget()

    assert caught.value.code == expected_code
    assert caught.value.owner == "browser"
    failure = diagnostic_failure_metadata(caught.value, "setup_handoff_protection")
    assert failure["code"] == expected_code
    assert failure["budget_kind"] == caught.value.budget_kind
    assert failure["actions_used"] == actions
    assert failure["actions_limit"] == 30


def test_timeout_metadata_identifies_the_browser_command_or_observation_stage():
    from modules.whatsapp.workflow import diagnostic_failure_metadata

    command_timeout = TimeoutError()
    command_timeout.guard_method = "DOM.describeNode"
    command_failure = diagnostic_failure_metadata(command_timeout, "setup_observation")
    observation_failure = diagnostic_failure_metadata(TimeoutError(), "setup_observation")

    assert command_failure["code"] == "BROWSER_COMMAND_TIMEOUT"
    assert command_failure["operation"] == "DOM.describeNode"
    assert observation_failure["code"] == "SETUP_OBSERVATION_TIMEOUT"
    assert observation_failure["failing_stage"] == "setup_observation"


def test_only_expired_controller_scope_is_reported_as_session_timeout():
    from modules.whatsapp.workflow import human_assisted_timeout_metadata

    inner_timeout = human_assisted_timeout_metadata(
        TimeoutError(), "setup_observation", session_deadline_expired=False
    )
    session_timeout = human_assisted_timeout_metadata(
        TimeoutError(), "setup_observation", session_deadline_expired=True
    )

    assert inner_timeout["code"] == "SETUP_OBSERVATION_TIMEOUT"
    assert session_timeout["code"] == "HUMAN_ASSISTED_SESSION_TIMEOUT"


def test_resource_review_summary_omits_observed_private_values():
    import json
    from modules.whatsapp.workflow import human_assisted_resource_summary

    summary = human_assisted_resource_summary({
        "status": "AVAILABLE",
        "digest": "contract-digest",
        "meaning": "Observed resource values are not authentication proof.",
        "options": [{
            "option": 1,
            "resource": "resource-1",
            "key": "tenant",
            "observed_value": "PRIVATE-ACCOUNT",
            "value_sha256": "value-digest",
        }],
    })

    assert summary["options"][0] == {
        "option": 1,
        "resource": "resource-1",
        "key": "tenant",
        "value_sha256": "value-digest",
    }
    assert "PRIVATE-ACCOUNT" not in json.dumps(summary)


async def test_navigation_review_can_bind_a_visible_link_missed_by_classifier():
    from modules.whatsapp.workflow import human_assisted_navigation_candidates

    class Review:
        async def operator_candidates(self, kind):
            return {
                "navigation": [],
                "sample": [
                    {"reference": "visible-link", "kind": "sample", "visible": True,
                     "element_type": "a", "role": "link"},
                    {"reference": "visible-button", "kind": "sample", "visible": True,
                     "element_type": "button", "role": "button"},
                    {"reference": "hidden-link", "kind": "sample", "visible": False,
                     "element_type": "a", "role": "link"},
                ]
            }[kind]

    candidates, used_fallback = await human_assisted_navigation_candidates(Review())

    assert [candidate["reference"] for candidate in candidates] == ["visible-link"]
    assert used_fallback is True


def test_unverified_route_requires_separate_consent_and_preserves_false_authentication():
    from modules.whatsapp.workflow import human_assisted_route

    pending = Observation(
        expected_host=True,
        dashboard=True,
        tenant_matches=True,
        readonly_access=False,
        evidence_configured=False,
    )

    assert human_assisted_route(pending, diagnostic_consent=False, session_safe=True) == "BLOCKED"
    assert human_assisted_route(pending, diagnostic_consent=True, session_safe=True) == "DIAGNOSTIC"
    assert human_assisted_route(pending, diagnostic_consent=True, session_safe=False) == "BLOCKED"


def test_diagnostic_consent_prompt_defaults_to_no():
    from modules.whatsapp.workflow import request_diagnostic_consent

    class Console:
        def __init__(self):
            self.default = None

        def choose(self, _prompt_id, _choices, _timeout, _maximum, *, default=None):
            self.default = default
            return default

    console = Console()

    assert request_diagnostic_consent(console, timeout_seconds=30, maximum_seconds=120) is False
    assert console.default == "no"


@pytest.mark.parametrize(
    "predicate",
    ["expected_host", "dashboard", "tenant_matches", "readonly_access", "evidence_configured"],
)
def test_each_production_login_predicate_is_required_for_verified_route(predicate):
    from modules.whatsapp.workflow import human_assisted_route

    verified = Observation(
        expected_host=True,
        dashboard=True,
        tenant_matches=True,
        readonly_access=True,
        evidence_configured=True,
    )
    assert human_assisted_route(verified, diagnostic_consent=False, session_safe=True) == "VERIFIED"

    unverified = verified.model_copy(update={predicate: False})
    expected_route = "BLOCKED" if predicate == "expected_host" else "DIAGNOSTIC"
    assert human_assisted_route(unverified, diagnostic_consent=True, session_safe=True) == expected_route


@pytest.mark.parametrize(
    "negative",
    ["credential_form", "challenge", "rejected", "expired", "account_restricted", "login_entry"],
)
def test_security_or_reauthentication_state_blocks_even_consented_diagnostic(negative):
    from modules.whatsapp.workflow import human_assisted_route

    pending = Observation(expected_host=True)
    unsafe = pending.model_copy(update={negative: True})

    assert human_assisted_route(unsafe, diagnostic_consent=True, session_safe=True) == "BLOCKED"


def test_trace_streams_only_the_redacted_agent_event():
    import json
    from app.trace import Trace

    trace = Trace(roles=("doer", "observer"))
    trace.enable_agent_trace(private_values=("PRIVATE-ACCOUNT",))
    streamed = []
    trace.agent_event_sink = streamed.append

    trace.agent_event(
        "tool_result",
        role="doer",
        result="PRIVATE-ACCOUNT",
        tool="browser_action",
    )

    assert len(streamed) == 1
    assert streamed[0]["kind"] == "tool_result"
    assert streamed[0]["result"] == "[private]"
    assert "PRIVATE-ACCOUNT" not in json.dumps(streamed)


async def test_graph_executes_login_once_and_keeps_unverified_diagnostic_separate():
    from modules.whatsapp.workflow import build_human_assisted_graph

    calls = {"login": 0, "verified": 0, "diagnostic": 0}

    async def login(_state):
        calls["login"] += 1
        return {
            "route": "DIAGNOSTIC",
            "authentication_verified": False,
            "diagnostic_consent": True,
        }

    async def verified_doer(_state):
        calls["verified"] += 1
        return {}

    async def diagnostic_doer(state):
        calls["diagnostic"] += 1
        assert state["authentication_verified"] is False
        assert state["diagnostic_consent"] is True
        return {"status": "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC"}

    async def blocked(_state):
        return {"status": "BLOCKED"}

    async def finish(state):
        return state

    graph = build_human_assisted_graph(login, verified_doer, diagnostic_doer, blocked, finish)
    result = await graph.ainvoke({})

    assert calls == {"login": 1, "verified": 0, "diagnostic": 1}
    assert result["authentication_verified"] is False
    assert result["status"] == "HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC"
def test_dashboard_handoff_requires_login_only_budget():
    import pytest
    from types import SimpleNamespace
    from app.config import ConfigurationError
    from modules.whatsapp.workflow import validate_human_assisted_authorization

    args = SimpleNamespace(dashboard_handoff_only=True, authorized_live=True,
                           authorized_inference=True, login_api_budget=8, total_api_budget=8,
                           task_api_budget=0)
    assert validate_human_assisted_authorization(args, 8) == {
        'login': 8, 'task': 0, 'observer': 0, 'teacher': 0, 'total': 8}
    args.task_api_budget = 1
    with pytest.raises(ConfigurationError):
        validate_human_assisted_authorization(args, 8)


@pytest.mark.asyncio
@pytest.mark.parametrize('status,cleanup,expected_exit', [
    ('MCP_CAMPAIGN_NAME_WRITE_VERIFIED', 'CLOSED_AND_PROFILE_REMOVED', 0),
    ('MCP_CAMPAIGN_NAME_READBACK_MISMATCH', 'CLOSED_AND_PROFILE_REMOVED', 1),
    ('MCP_CAMPAIGN_NAME_WRITE_VERIFIED', 'CLEANUP_UNCONFIRMED', 1),
])
async def test_mcp_campaign_name_write_cli_exit(status, cleanup, expected_exit, monkeypatch):
    from unittest.mock import AsyncMock
    from app.cli import build_parser, run
    from modules.whatsapp import workflow

    args = build_parser().parse_args(['whatsapp-human-assisted', '--browser-mode', 'mcp',
                                      '--mcp-campaign-name-write', '--manual-form-test',
                                      '--operator-confirmed-login'])
    mocked = AsyncMock(return_value={'status': status, 'cleanup': cleanup})
    monkeypatch.setattr(workflow, 'run_human_assisted', mocked)
    if expected_exit:
        with pytest.raises(SystemExit) as stop:
            await run(args)
        assert stop.value.code == expected_exit
    else:
        await run(args)
    mocked.assert_awaited_once_with(args)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcomes, expected_attempts, expected_exit', [
    (('pass', 'pass', 'pass'), [None, None, None], 0),
    (('candidate-a', 'pass', 'pass'), [None, 'candidate-a', 'candidate-a'], 0),
    (('candidate-a', 'candidate-b', 'pass'), [None, 'candidate-a', 'candidate-b'], 0),
    (('candidate-a', 'blocked'), [None, 'candidate-a'], 1),
    (('blocked',), [None], 1),
])
async def test_mcp_ga_batch_reuses_only_validated_corrections(outcomes, expected_attempts, expected_exit, monkeypatch):
    from app.cli import build_parser, run
    from modules.whatsapp import workflow

    args = build_parser().parse_args(['whatsapp-human-assisted', '--browser-mode', 'mcp', '--mcp-ga-setup',
                                      '--operator-confirmed-login', '--operator-confirmed-runs', '3'])
    seen = []

    async def attempt(runtime):
        inherited = getattr(runtime, 'mcp_ga_retry', None)
        seen.append(inherited and inherited['candidate'])
        outcome = outcomes[len(seen) - 1]
        correction = {'candidate': outcome} if outcome.startswith('candidate-') else None
        return {'status': 'MCP_GA_VERIFIED' if outcome == 'pass' else 'STUCK',
                'cleanup': 'CLOSED_AND_PROFILE_REMOVED', 'mcp_ga_retry': correction}

    monkeypatch.setattr(workflow, 'run_human_assisted', attempt)
    if expected_exit:
        with pytest.raises(SystemExit) as stop:
            await run(args)
        assert stop.value.code == expected_exit
    else:
        await run(args)
    assert seen == expected_attempts
