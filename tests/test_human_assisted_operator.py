import importlib
import json

import pytest

from browser.discovery import ReadOnlyDiscovery
from browser.evidence_diagnostics import EvidenceFailure, operator_event
from browser.evidence_review import EvidenceReview
from test_login_review_path_audit import observed_scalar


def operator_console_type():
    try:
        return importlib.import_module("browser.operator_console").OperatorConsole
    except ModuleNotFoundError:
        pytest.fail("bounded operator console has not been implemented")


class Clock:
    def __init__(self):
        self.value = 0.0

    def __call__(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


def test_operator_console_rejects_unlisted_terminal_input():
    clock = Clock()
    keys = ["9", "\n", "1", "\n"]

    def read_key():
        return keys.pop(0) if keys else None

    console = operator_console_type()(read_key=read_key, clock=clock, sleep=clock.sleep)

    selected = console.choose(
        "inspect_dashboard",
        {"inspect": "Inspect dashboard candidates", "abort": "Abort"},
        timeout_seconds=5,
        maximum_seconds=10,
    )

    assert selected == "inspect"
    assert console.events[-1]["choice"] == "inspect"
    assert console.events[-1]["invalid_attempts"] == 1


def test_operator_console_logs_direct_number_input_and_validates_consumed_choice():
    from io import StringIO

    clock = Clock()
    keys = iter(["3", "\n"])
    output = StringIO()
    console = operator_console_type()(read_key=keys.__next__, clock=clock, sleep=clock.sleep, output=output)

    selected = console.choose(
        "human_assisted_login_review",
        {"dashboard": "Inspect dashboard candidates", "tenant": "Inspect account candidates",
         "navigation": "Inspect WhatsApp navigation"},
        timeout_seconds=300,
        maximum_seconds=300,
        default="navigation",
    )

    assert selected == "navigation"
    assert [event["event"] for event in console.events] == [
        "prompt_open",
        "input_received",
        "input_received",
        "input_valid",
    ]
    assert console.events[1]["input_kind"] == "DIGIT"
    assert console.events[2]["input_kind"] == "ENTER"
    assert console.events[-1]["valid"] is True
    assert console.events[-1]["choice"] == "navigation"
    assert "TYPE YOUR SELECTION HERE AND PRESS ENTER." in output.getvalue()
    assert "Timeout: 300s" in output.getvalue()


def test_operator_console_logs_invalid_input_and_timeout():
    clock = Clock()
    keys = ["9", "\n"]

    def read_key():
        return keys.pop(0) if keys else None

    console = operator_console_type()(read_key=read_key, clock=clock, sleep=clock.sleep)

    assert console.choose(
        "human_assisted_login_review",
        {"dashboard": "Inspect dashboard candidates", "abort": "Abort"},
        timeout_seconds=1,
        maximum_seconds=2,
        default="abort",
    ) == "abort"

    assert any(event["event"] == "input_invalid" and event["valid"] is False for event in console.events)
    assert console.events[-1]["event"] == "timeout"
    assert console.events[-1]["status"] == "TIMED_OUT_DEFAULT"


def test_operator_console_extends_timeout_on_activity_but_respects_hard_cap():
    clock = Clock()
    sent = set()

    def read_key():
        if clock.value >= 1.0 and "first" not in sent:
            sent.add("first")
            return "x"
        if clock.value >= 2.2 and "second" not in sent:
            sent.add("second")
            return "x"
        return None

    console = operator_console_type()(read_key=read_key, clock=clock, sleep=clock.sleep)

    selected = console.choose(
        "whatsapp_diagnostic",
        {"yes": "Run diagnostic", "no": "Do not run diagnostic"},
        timeout_seconds=2,
        maximum_seconds=3,
        default="no",
    )

    assert selected == "no"
    assert clock.value == pytest.approx(3.0, abs=0.11)
    assert console.events[-1]["choice"] == "no"
    assert console.events[-1]["status"] == "TIMED_OUT_DEFAULT"
    assert console.events[-1]["timeout_extensions"] >= 1


async def candidate_fixture(tmp_path, hidden_after_prepare=False, change_document_after_scan=False):
    browser, discovery, review = await observed_scalar(tmp_path)
    original_evaluate = browser.page.evaluate
    dom_calls = 0
    owner = browser.guard.session_states[browser.guard.primary_session]

    async def evaluate(expression):
        nonlocal dom_calls
        result = await original_evaluate(expression)
        if "const dashboard_candidates=" not in expression:
            return result
        dom_calls += 1
        dashboard_visible = not (hidden_after_prepare and dom_calls > 3)
        result["_render_targets"] = [
            {
                "element_ref": "element-1",
                "selector": "#content",
                "frame_selectors": [],
                "shadow_selectors": [],
            },
            {
                "element_ref": "element-2",
                "selector": "#tenant",
                "frame_selectors": [],
                "shadow_selectors": [],
            },
        ]
        result["dom_diagnostics"] = {
            "semantic_samples": [
                {
                    "element_ref": "element-1",
                    "element_type": "main",
                    "document_ref": "document-1",
                    "root_ref": "accessible-root-1",
                    "role": "main",
                    "visible": dashboard_visible,
                    "selector_eligible": True,
                    "frame_depth": 0,
                    "shadow_depth": 0,
                    "potential_dashboard": True,
                    "potential_navigation": False,
                    "expected_account_match": False,
                    "rendering": {"visible": dashboard_visible},
                    "collector_gate": "ELIGIBLE_PRECLASSIFICATION",
                    "scope_exclusions": [],
                    "composed_shell_exception": False,
                    "cross_shadow_controls": False,
                },
                {
                    "element_ref": "element-2",
                    "element_type": "span",
                    "document_ref": "document-1",
                    "root_ref": "accessible-root-1",
                    "role": "OTHER_OR_ABSENT",
                    "visible": True,
                    "selector_eligible": True,
                    "frame_depth": 0,
                    "shadow_depth": 0,
                    "potential_dashboard": False,
                    "potential_navigation": False,
                    "expected_account_match": True,
                    "rendering": {"visible": True},
                    "collector_gate": "ELIGIBLE_PRECLASSIFICATION",
                    "scope_exclusions": [],
                    "composed_shell_exception": False,
                    "cross_shadow_controls": False,
                },
            ],
            "rejections": {"dashboard": {}, "tenant": {}, "navigation": {}},
        }
        if change_document_after_scan and dom_calls == 3:
            owner.document_generation += 1
        return result

    browser.page.evaluate = evaluate
    await discovery.prepare()
    return browser, discovery, review


async def test_evidence_review_binds_only_current_visible_dom_candidate(tmp_path):
    assert hasattr(ReadOnlyDiscovery, "operator_candidates"), "discovery must expose bounded candidate references"
    assert hasattr(ReadOnlyDiscovery, "bind_candidate"), "discovery must bind a fresh candidate reference"
    assert hasattr(EvidenceReview, "operator_candidates")
    assert hasattr(EvidenceReview, "bind_candidate")

    _, discovery, review = await candidate_fixture(tmp_path)
    candidates = await review.operator_candidates("dashboard")

    assert len(candidates) == 1
    assert candidates[0]["visible"] is True
    bound = await review.bind_candidate(candidates[0]["reference"])

    assert bound["state"] == "CURRENT_VISIBLE_DOM_REFERENCE"
    assert bound["kind"] == "dashboard"
    assert bound["reference"] == candidates[0]["reference"]
    assert "selector" not in bound


async def test_evidence_review_rejects_candidate_after_document_change(tmp_path):
    assert hasattr(EvidenceReview, "operator_candidates"), "candidate references are not implemented"
    assert hasattr(EvidenceReview, "bind_candidate"), "candidate binding is not implemented"

    browser, _, review = await candidate_fixture(tmp_path)
    candidate = (await review.operator_candidates("dashboard"))[0]
    browser.guard.session_states[browser.guard.primary_session].document_generation += 1

    with pytest.raises(EvidenceFailure, match="CANDIDATE_REFERENCE_STALE"):
        await review.bind_candidate(candidate["reference"])


async def test_evidence_review_rejects_candidate_that_is_no_longer_visible(tmp_path):
    assert hasattr(EvidenceReview, "operator_candidates"), "candidate references are not implemented"
    assert hasattr(EvidenceReview, "bind_candidate"), "candidate binding is not implemented"

    _, _, review = await candidate_fixture(tmp_path, hidden_after_prepare=True)
    candidate = (await review.operator_candidates("dashboard"))[0]

    with pytest.raises(EvidenceFailure, match="CANDIDATE_NOT_VISIBLE"):
        await review.bind_candidate(candidate["reference"])


async def test_evidence_review_does_not_issue_candidates_after_document_replacement(tmp_path):
    _, _, review = await candidate_fixture(tmp_path, change_document_after_scan=True)

    with pytest.raises(EvidenceFailure, match="CANDIDATE_REFERENCE_STALE"):
        await review.operator_candidates("dashboard")


def test_operator_decision_events_do_not_copy_candidate_labels_or_values():
    console = operator_console_type()(
        read_key=iter(["1", "\n"]).__next__,
        clock=Clock(),
        sleep=Clock().sleep,
    )
    private_label = "PRIVATE-ACCOUNT-VALUE-DO-NOT-LOG"

    assert console.choose(
        "review_candidate",
        {"inspect": private_label, "abort": "Abort"},
        timeout_seconds=5,
        maximum_seconds=10,
    ) == "inspect"

    serialized = json.dumps(console.events)
    assert private_label not in serialized
    assert console.events[-1]["prompt_id"] == "review_candidate"
    assert console.events[-1]["choice"] == "inspect"


def test_operator_event_drops_untrusted_value_shaped_choice():
    event = operator_event(
        None,
        "DECISION",
        {"prompt_id": "review_candidate", "choice": "private_account_value"},
    )

    assert "choice" not in event


def test_operator_event_drops_unregistered_prompt_identifiers():
    event = operator_event(
        None,
        "DECISION",
        {"prompt_id": "private_account_value", "choice": "inspect_dashboard"},
    )

    assert "prompt_id" not in event
