import json
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_manual_mode_requires_verified_route():
    from modules.whatsapp.workflow import human_assisted_authorization
    args = SimpleNamespace(manual_form_test=True, authorized_live=True, authorized_inference=True)
    assert not human_assisted_authorization(args, verified=False).edits
    assert human_assisted_authorization(args, verified=True).manual_form_test


def test_doer_output_and_reserve_fit_the_whole_request_plan():
    from app.model_settings import ModelSettings
    from modules.whatsapp.workflow import human_assisted_output_budget
    task_output = human_assisted_output_budget(ModelSettings(), {"login": 6, "task": 17, "teacher": 1})
    assert 500 <= task_output <= 1024
    assert 6 * 1536 + 17 * task_output + 2000 <= 21000


def test_reference_teacher_evidence_can_be_compacted():
    from deep_agents.evidence import tokens
    from modules.whatsapp.workflow import compact_human_assisted_teacher_evidence
    artifact = Path("outputs/human-assisted/login-whatsapp-d51093cd386243ee8599067d80e55fc6/teacher-input-evidence.json")
    if not artifact.is_file():
        pytest.skip("reference-run evidence is local")
    full = json.loads(artifact.read_bytes())
    compact = compact_human_assisted_teacher_evidence(full)
    assert compact["execution_facts"] == full["execution_facts"]
    assert compact["skill_sha256"] == full["skill_sha256"]
    assert len(compact["events"]) == 3
    assert tokens(compact) < 4200


def test_inspect_rejection_explains_the_exact_contract():
    from modules.whatsapp.contract import V2ManualAction, v2_schema_rejection
    args = {"action": "inspect", "reference": "opaque", "field": "campaign_name"}
    with pytest.raises(ValueError):
        V2ManualAction.model_validate(args)
    assert v2_schema_rejection(V2ManualAction, args) == {
        "action": "inspect", "reason": "UNSUPPORTED_ACTION_ARGUMENTS",
        "unexpected": ["field"], "missing": [],
    }


@pytest.mark.asyncio
async def test_registered_inspect_rejects_extraneous_field_before_dispatch():
    from app.trace import Trace
    from modules.whatsapp.contract import V2Authorization
    from modules.whatsapp.tools import V2BrowserTools

    trace = Trace()
    trace.enable_agent_trace()
    tools = object.__new__(V2BrowserTools)
    tools.browser = SimpleNamespace(trace=trace)
    tools.read_only = False
    tools.human_assisted_setup = True
    tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
    tool, = tools.registered_tools()
    message = await tool.ainvoke({"name": "browser_action", "id": "local", "type": "tool_call",
                                  "args": {"action": "inspect", "reference": "opaque", "field": "campaign_name"}})
    assert message.status == "error"
    result = json.loads(message.content)
    assert result["action"] == "inspect"
    assert result["unexpected"] == ["field"]
    assert trace.events[-1]["agent_execution"]["browser_interaction"] is False
    tools.read_only = True
    from modules.whatsapp.contract import v2_action_arguments
    assert set(v2_action_arguments(tools.action_schema())) == {
        "observe", "dom", "wait", "inspect", "scroll", "options", "verify",
    }


@pytest.mark.asyncio
async def test_manual_write_rejects_before_touching_the_browser_without_login():
    from modules.whatsapp.contract import V2Authorization, V2Stop
    from modules.whatsapp.tools import V2BrowserTools
    tools = object.__new__(V2BrowserTools)
    tools.read_only = False
    tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
    with pytest.raises(V2Stop, match="MANUAL_SETUP_REQUIRES_VERIFIED_LOGIN"):
        await tools.authorize_write()


def test_controller_and_success_reviews_do_not_create_strategies():
    from modules.whatsapp.workflow import human_assisted_review_kind
    assert human_assisted_review_kind({"failure": {"owner": "controller"}}, {}) == "technical_analysis"
    assert human_assisted_review_kind({"failure": {"owner": "authorization"}}, {}) is None
    assert human_assisted_review_kind({}, {"status": "COMPLETE"}) == "success_review"
    assert human_assisted_review_kind({"failure": {"code": "STUCK", "owner": "recognition"}}, {}) == "recoverable_strategy"


@pytest.mark.asyncio
async def test_real_teacher_request_builder_measures_compact_reference_before_transport(monkeypatch):
    import ast
    from app.model_settings import ModelSettings
    from app.trace import Trace
    from deep_agents.evidence import canonical
    from deep_agents.models import openai_model
    from deep_agents.permissions import InferenceBudget
    from deep_agents.review_contract import EvidenceError
    from deep_agents.teacher import create_teacher
    from deep_agents.telemetry import MeteredChatOpenAI
    from langsmith import tracing_context
    from modules.whatsapp.contract import V2Lesson
    from modules.whatsapp.workflow import compact_human_assisted_teacher_evidence

    class StopBeforeTransport(Exception):
        pass

    artifact = Path("outputs/human-assisted/login-whatsapp-d51093cd386243ee8599067d80e55fc6/teacher-input-evidence.json")
    if not artifact.is_file():
        pytest.skip("reference-run evidence is local")
    full = json.loads(artifact.read_bytes())
    compact = compact_human_assisted_teacher_evidence(full)
    tree = ast.parse(Path("modules/whatsapp/workflow.py").read_text(encoding="utf-8"))
    build_review = next(node for node in tree.body if isinstance(node, ast.AsyncFunctionDef)
                        and node.name == "review_v2_attempt")
    prompt = next(keyword.value.value for node in ast.walk(build_review) if isinstance(node, ast.Call)
                  for keyword in node.keywords if keyword.arg == "prompt_override")

    async def capture(self, messages, *, stop=None, run_manager=None, **kwargs):
        self._get_request_payload(messages, stop=stop, **kwargs)
        raise StopBeforeTransport

    monkeypatch.setattr(MeteredChatOpenAI, "_agenerate", capture)
    settings = ModelSettings(api_key="unused-local-placeholder")

    async def invoke(package, expected):
        trace = Trace()
        trace.enable_agent_trace()
        budget = InferenceBudget(1)
        teacher, _ = create_teacher(openai_model("teacher", settings, trace, budget), trace,
                                    budget, schema=V2Lesson, prompt_override=prompt)
        with tracing_context(enabled=False), pytest.raises(expected):
            await teacher.ainvoke({"messages": [{"role": "user", "content": canonical(package)}]},
                                  {"configurable": {"thread_id": "focused-local-payload-check"}})
        return trace

    rejected = await invoke(full, EvidenceError)
    event = next(row["agent_execution"] for row in rejected.events
                 if row.get("agent_execution", {}).get("kind") == "model_boundary_rejected")
    assert event["failed_predicates"] == ["TEACHER_INPUT_BOUND_EXCEEDS_CAP"]
    assert not rejected.boundary_diagnostics
    admitted = await invoke(compact, StopBeforeTransport)
    boundary, = admitted.boundary_diagnostics
    assert boundary["input_bound_with_margin"] <= settings.teacher_input
    assert boundary["max_output_tokens"] == settings.teacher_output
    print("Compacted Teacher payload input bound:", boundary["input_bound_with_margin"])


def test_output_reserve_rejection_records_reconstructable_operands():
    from deep_agents.permissions import InferenceBudget, TokenPool
    budget = InferenceBudget(token_pool=TokenPool(input_reserved=69127, output_reserved=18432),
                             teacher_reserve_input=8000, teacher_reserve_output=2000)
    operands = budget.token_operands(18000, 1536, role="doer")
    assert operands["prior_output_reserved"] + operands["max_output_tokens"] + operands["teacher_output_reserve"] == 21968
    assert operands["output_limit"] == 21000
    assert operands["output_over_limit"] and not operands["input_over_limit"]
    with pytest.raises(RuntimeError, match="FINAL_TEACHER_TOKEN_RESERVE"):
        budget.reserve_tokens(18000, 1536)
    assert budget.token_pool.output_reserved == 18432


def test_candidate_display_persists_diff_and_checks_exact_bytes(monkeypatch, tmp_path, capsys):
    import hashlib
    import learning.skill_storage.registry as registry
    from app.trace import Trace
    from modules.whatsapp.diagnostic_artifacts import show_setup_candidate
    version = "a" * 32
    content = "# Baseline\n\n- Reviewed corrective step.\n"
    path = tmp_path / "registry" / "candidates" / version / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(content.encode("utf-8"))
    monkeypatch.setattr(registry, "whatsapp_history", lambda: tmp_path / "registry")
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    context = {
        "skill": SimpleNamespace(content="# Baseline\n"),
        "candidate_result": {"version": version, "content_sha256": digest, "status": "UNAPPROVED_PENDING_EXECUTION"},
        "evaluation": {"verified_fields": ["campaign_name"]},
    }
    report = show_setup_candidate(tmp_path, Trace(), context)
    diff = (tmp_path / "candidate.diff").read_text(encoding="utf-8")
    assert report["content_sha256"] == digest
    assert report["verified_fields"] == ["campaign_name"]
    assert "+- Reviewed corrective step." in diff
    output = capsys.readouterr().out
    assert content in output and diff in output and digest in output


@pytest.mark.asyncio
async def test_manual_run_requires_reviewed_evidence_before_startup(tmp_path, monkeypatch):
    import sys
    from app.cli import build_parser
    from app.config import Config, ConfigurationError
    from app.model_settings import ModelSettings
    from modules.whatsapp.workflow import run_human_assisted

    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(Config, 'load', lambda: Config('unused'))
    def reached_runtime():
        pytest.fail('Model settings were loaded before manual Login evidence preflight')
    monkeypatch.setattr(ModelSettings, 'load', reached_runtime)
    args = build_parser().parse_args([
        'whatsapp-human-assisted', '--authorized-live', '--authorized-inference',
        '--authorized-candidate-evaluation', '--manual-form-test',
        '--login-evidence-file', str(tmp_path / 'absent.json'),
    ])
    with pytest.raises(ConfigurationError, match='MISSING_EVIDENCE_CONFIGURATION'):
        await run_human_assisted(args)


def test_setup_output_headroom_retains_teacher_reserve():
    from app.config import ConfigurationError
    from app.model_settings import ModelSettings
    from modules.whatsapp.workflow import human_assisted_output_budget

    caps = {'login': 6, 'task': 17, 'teacher': 1}
    assert human_assisted_output_budget(ModelSettings(cycle_output=47000), caps, writable=True) == 2048
    with pytest.raises(ConfigurationError, match='LEARNING_MAX_OUTPUT_TOKENS'):
        human_assisted_output_budget(ModelSettings(cycle_output=30000), caps, writable=True)


def test_blocked_setup_verdict_retains_verified_fields_without_promoting_completion():
    from modules.whatsapp.workflow import setup_stopped_verdict

    cached = {'fields': {'campaign_name': True, 'waba': True, 'tag': True},
              'stage_visible': True, 'checks': {}, 'evidence_ref': 'readback'}
    tools = SimpleNamespace(stage='setup', last_independent_readback=cached, failed_approaches={},
                            terminal=None, pending_write=False)
    failure = {'code': 'OUTPUT_LIMIT_INCOMPLETE', 'owner': 'controller'}
    verdict = setup_stopped_verdict(tools, failure)
    assert verdict['status'] == 'BLOCKED'
    assert verdict['verified_fields'] == ['campaign_name', 'tag', 'waba']
    assert verdict['failure'] == failure
    assert verdict['readback']['evidence_ref'] == 'readback'


@pytest.mark.parametrize('keys,approved', [('y\r', True), ('n\r', False), ('\r', False), ('maybe\r', False)])
def test_setup_write_prompt_is_single_and_defaults_to_no(keys, approved):
    from io import StringIO
    from browser.operator_console import OperatorConsole
    output = StringIO()
    read = iter(keys)
    console = OperatorConsole(read_key=lambda: next(read), output=output)
    assert console.confirm_setup_write(10) is approved
    assert output.getvalue().count('Write the approved WhatsApp Setup fields? [y/N]') == 1


def test_setup_write_consent_requires_all_login_gates(capsys):
    from unittest.mock import Mock
    from app.state import Observation
    from modules.whatsapp.workflow import prompt_for_verified_setup

    console = Mock()
    console.confirm_setup_write.return_value = True
    observation = Observation(allowed=True, expected_host=True, dashboard=True,
                              tenant_matches=True, evidence_configured=True, readonly_access=False)
    assert not prompt_for_verified_setup(console, observation, 'authorized-tenant', 10)
    console.confirm_setup_write.assert_not_called()
    observation.readonly_access = True
    assert prompt_for_verified_setup(console, observation, 'authorized-tenant', 10)
    console.confirm_setup_write.assert_called_once_with(10)
    assert 'authorized-tenant' in capsys.readouterr().out


def test_setup_field_status_requires_independent_readback_to_count_write():
    from modules.whatsapp.workflow import setup_field_write_status

    event = {'stage': 'setup', 'action': 'type', 'field': 'campaign_name', 'attempted': True}
    assert setup_field_write_status(event)['write'] == 'UNCERTAIN'
    event.update(ok=True, verification={'actual_match': True})
    assert setup_field_write_status(event) == {
        'field': 'campaign_name', 'write': 'WRITTEN', 'readback': 'VERIFIED',
    }
    assert setup_field_write_status({**event, 'action': 'verify'}) is None


@pytest.mark.asyncio
@pytest.mark.parametrize('key,decision', [('y', 'yes'), ('Y', 'yes'), (' \tY ', 'yes'), ('\x00Hy', 'yes'), ('\xe0Ky', 'yes'), ('n', 'no'), ('', 'no')])
async def test_operator_login_confirmation_keeps_event_loop_running(key, decision):
    from io import StringIO
    from browser.operator_console import OperatorConsole

    import asyncio

    commands_processed = asyncio.Event()
    asyncio.get_running_loop().call_soon(commands_processed.set)
    keys = iter([None, *key, '\r'])
    output = StringIO()
    console = OperatorConsole(read_key=lambda: next(keys), output=output,
                              sleep=lambda _: pytest.fail('Blocked the CDP event loop'))
    assert await console.confirm_operator_login(10) == decision
    assert commands_processed.is_set()
    assert ('Approval received' in output.getvalue()) is (decision == 'yes')
    assert output.getvalue().count('Write the approved WhatsApp Setup fields? [y/N]') == 1


@pytest.mark.asyncio
async def test_operator_login_starts_without_reviewed_evidence(tmp_path, monkeypatch):
    import sys
    from app.cli import build_parser
    from app.config import Config
    from app.model_settings import ModelSettings
    from modules.whatsapp.workflow import run_human_assisted

    class RuntimeReached(Exception):
        pass

    def reached_runtime():
        raise RuntimeReached

    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(Config, 'load', lambda: Config('unused'))
    monkeypatch.setattr(ModelSettings, 'load', reached_runtime)
    args = build_parser().parse_args([
        'whatsapp-human-assisted', '--authorized-live', '--authorized-inference',
        '--authorized-candidate-evaluation', '--manual-form-test', '--operator-login',
        '--login-evidence-file', str(tmp_path / 'absent.json'),
    ])
    with pytest.raises(RuntimeReached):
        await run_human_assisted(args)


@pytest.mark.asyncio
async def test_operator_setup_prompt_timeout_is_distinct_from_decline():
    from io import StringIO
    from browser.operator_console import OperatorConsole

    ticks = iter([0, 0, 0, 2, 2])
    console = OperatorConsole(read_key=lambda: None, clock=lambda: next(ticks), output=StringIO())
    assert await console.confirm_operator_login(1) == 'timeout'
    assert console.events[-1]['event'] == 'timeout'
    assert console.events[-1]['choice'] is None


@pytest.mark.asyncio
async def test_windows_operator_prompt_discards_pre_prompt_keys(monkeypatch):
    import sys
    from io import StringIO
    from browser.operator_console import OperatorConsole

    if sys.platform != 'win32':
        pytest.skip('Windows console buffering')
    monkeypatch.setattr(sys, 'stdin', SimpleNamespace(isatty=lambda: True))
    console = OperatorConsole(output=StringIO())
    keys = iter(['y', '\r', None, 'n', '\r'])
    console.read_key = lambda: next(keys)
    assert await console.confirm_operator_login(10) == 'no'
    assert 'Approval received' not in console.output.getvalue()


def test_preexisting_campaign_value_stays_pending_until_write_and_readback():
    from modules.whatsapp.contract import STAGE_FIELDS
    from modules.whatsapp.workflow import setup_attempt_verdict

    tools = SimpleNamespace(stage='setup', human_assisted_setup=True, terminal=None, pending_write=False,
                            failed_approaches={}, events=[])
    facts = {'stage_visible': True, 'fields': dict.fromkeys(STAGE_FIELDS['setup'], True)}
    verdict = setup_attempt_verdict(tools, facts)
    assert verdict['status'] == 'INCOMPLETE'
    assert verdict['next_required_field'] == 'campaign_name'

    tools.events.append({'stage': 'setup', 'field': 'campaign_name', 'action': 'type',
                         'attempted': True, 'ok': True, 'verification': {'actual_match': True}})
    assert setup_attempt_verdict(tools, facts)['status'] == 'COMPLETE'
