import asyncio
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from urllib.parse import urlsplit

import pytest


def _trace():
    events = []

    def agent_event(*args, **kwargs):
        events.append((args, kwargs))
        return f"trace-{len(events)}"

    return SimpleNamespace(
        run_id="offline-manual-form-test",
        agent_phase="task_doer",
        events=events,
        agent_event=agent_event,
        safe_tool_arguments=lambda _tool, arguments: arguments,
        fact=lambda *_args, **_kwargs: None,
        lifecycle=lambda *_args, **_kwargs: None,
    )


def _write_tools(stage="setup"):
    from modules.whatsapp.tools import V2BrowserTools

    tools = object.__new__(V2BrowserTools)
    tools.diagnostic_mode = False
    tools.read_only = False
    tools.stage = stage
    tools.events = []
    tools.pending_write = False
    tools.start_state_verified = False
    tools.manual_authentication_verified = True
    tools._last_protected_route = {'fragment_class': 'SETUP'}
    tools.next_allowed = False
    tools.controller_attempted = False
    tools.browser = SimpleNamespace(trace=_trace())
    tools.ui = SimpleNamespace(require_record=Mock())
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    return tools


def test_human_assisted_edits_require_manual_form_flag_and_verified_login():
    from modules.whatsapp.workflow import human_assisted_authorization

    read_only = human_assisted_authorization(SimpleNamespace(
        authorized_live=True,
        authorized_inference=True,
        manual_form_test=False,
    ), verified=True)
    manual = human_assisted_authorization(SimpleNamespace(
        authorized_live=True,
        authorized_inference=True,
        manual_form_test=True,
    ), verified=True)

    assert read_only.edits is False
    assert read_only.manual_form_test is False
    assert read_only.persistence is False
    assert manual.edits is True
    assert manual.manual_form_test is True
    assert manual.persistence is False


async def test_manual_setup_edit_does_not_need_record_or_start_state_proof(monkeypatch):
    import modules.whatsapp.tools as tools_module
    from modules.whatsapp.contract import V2Authorization

    tools = _write_tools()
    tools.authorization = V2Authorization(
        live=True,
        inference=True,
        edits=True,
        persistence=False,
        manual_form_test=True,
    )
    tools.protect = AsyncMock(side_effect=["CURRENT_DOCUMENT", "CURRENT_DOCUMENT"])
    tools.ui.require_record = Mock(side_effect=AssertionError("record proof is not needed"))
    tools.install_guard = AsyncMock()
    monkeypatch.setattr(
        tools_module, "read_safety_snapshot", AsyncMock(return_value={"safe": True}), raising=False
    )
    monkeypatch.setattr(
        tools_module, "safety_diagnostic", lambda _snapshot: {"code": None}, raising=False
    )

    await tools.authorize_write()

    tools.ui.require_record.assert_not_called()
    assert tools.protect.await_count == 2


async def test_manual_form_test_cannot_write_outside_setup(monkeypatch):
    from modules.whatsapp.contract import V2Authorization, V2Stop

    tools = _write_tools(stage="audience")
    tools.authorization = V2Authorization(
        live=True,
        inference=True,
        edits=True,
        persistence=False,
        manual_form_test=True,
    )

    with pytest.raises(V2Stop, match="MANUAL_FORM_TEST_SETUP_ONLY"):
        await tools.authorize_write()

    tools.protect.assert_not_awaited()
    tools.ui.require_record.assert_not_called()


async def test_manual_form_test_never_allows_campaign_persistence():
    from modules.whatsapp.contract import V2Authorization, V2Stop

    with pytest.raises(ValueError):
        V2Authorization(
            live=True,
            inference=True,
            edits=True,
            persistence=True,
            manual_form_test=True,
        )

    tools = _write_tools()
    tools.authorization = V2Authorization(
        live=True,
        inference=True,
        edits=True,
        persistence=False,
        manual_form_test=True,
    )
    tools.next_allowed = True
    tools.browser.trace.agent_event = Mock(return_value="trace")

    with pytest.raises(V2Stop):
        await tools.advance()

    assert tools.controller_attempted is False


@pytest.mark.parametrize('role', ['checkbox', 'switch'])
async def test_manual_form_action_can_toggle_an_observed_aria_checkbox(monkeypatch, role):
    import modules.whatsapp.observer as observer
    from modules.whatsapp.contract import V2Authorization
    from modules.whatsapp.tools import V2BrowserTools

    tools = _write_tools()
    tools.authorization = V2Authorization(
        live=True,
        inference=True,
        edits=True,
        persistence=False,
        manual_form_test=True,
    )
    tools.payload = SimpleNamespace(ga_tracking=True)
    tools.ui = SimpleNamespace(
        fields={"ga_tracking": SimpleNamespace(kind="checkbox")},
        stage_selectors={"setup": "#setup"},
    )
    tools.reviewed_fields = {"ga_tracking"}
    tools.observed_bindings = {}
    tools.preparations = {}
    tools.failed_approaches = {}
    tools.images = {}
    tools.last_observation = None
    tools.mutations = 0
    tools.terminal = None
    tools.lock = asyncio.Lock()
    tools.browser.page = SimpleNamespace(evaluate=AsyncMock(side_effect=[True, False]))
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    tools.authorize_write = AsyncMock()
    tools.observe = AsyncMock(return_value={"stage": "setup", "stage_visible": True})
    locator = SimpleNamespace(click=AsyncMock())
    entry = {
        "selector": "#ga-tracking",
        "identity": "CURRENT_NODE",
        "stage": "setup",
        "field": "ga_tracking",
        "part": "control",
        "tag": "DIV",
        "role": role,
        "type": "button" if role == "switch" else None,
        "enabled": True,
        "create": False,
    }
    tools.resolve = AsyncMock(return_value=(entry, locator))
    monkeypatch.setattr(
        observer,
        "deterministic_readback",
        AsyncMock(side_effect=[
            {"fields": {"ga_tracking": False}},
            {"fields": {"ga_tracking": True}},
        ]),
    )

    result, _ = await tools.action({
        "action": "type",
        "reference": "aria-checkbox",
        "field": "ga_tracking",
    })

    assert result["ok"] is True
    locator.click.assert_awaited_once()


async def test_setup_observation_retries_dynamic_rendering_with_a_finite_bound(
    monkeypatch, tmp_path
):
    import app.config
    import modules.whatsapp.observation as observation
    import modules.whatsapp.tools as tools_module
    from modules.whatsapp.contract import SETUP_URL, ReviewedUI, V2Authorization, V2Payload
    from modules.whatsapp.tools import V2BrowserTools

    target = urlsplit(SETUP_URL)
    ui = ReviewedUI(
        origin=f"{target.scheme}://{target.netloc}",
        tenant=target.path.strip("/").split("/")[0],
    )
    tools = object.__new__(V2BrowserTools)
    tools.diagnostic_mode = False
    tools.browser = SimpleNamespace(page=SimpleNamespace(), trace=_trace())
    tools.payload = V2Payload(
        campaign_name="offline campaign",
        waba="offline-waba",
        business_number="offline-number",
        tag="offline-tag",
        ga_tracking=False,
        audience_type="Lists/Segments",
        include_audience="offline-include",
        exclude_audience="offline-exclude",
        template="offline-template",
        send_type="Send Later",
        schedule_datetime="2099-01-01T10:00",
        timezone="UTC",
        frequency_cap_enabled=False,
        contact_limit_enabled=False,
        final_action="SAVE_AND_PREVIEW",
        publish_allowed=False,
        send_test_allowed=False,
    )
    tools.ui = ui
    tools.authorization = V2Authorization()
    tools.stage = "setup"
    tools.references = {}
    tools.events = []
    tools.pending_write = False
    tools.read_only = False
    tools.reviewed_fields = set()
    tools.reviewed_stages = set()
    tools.observed_bindings = {}
    tools.preview_identity = None
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    monkeypatch.setattr(app.config, "ROOT", tmp_path)
    monkeypatch.setattr(
        tools_module, "observed_node", AsyncMock(return_value="CURRENT_NODE")
    )
    wait = AsyncMock()
    monkeypatch.setattr(observation, "wait_for_setup_change", wait)

    ready = {
        "stage_visible": True,
        "nodes": [],
        "truncated": False,
        "scope_selector": "#setup",
        "field_candidates": [{
            "field": "campaign_name",
            "selector": "#campaign-name",
            "kind": "text",
            "field_label": "campaign name",
        }],
        "discovered_bindings": {
            "campaign_name": {"selector": "#campaign-name", "kind": "text"},
        },
    }
    results = [
        {"stage_visible": False, "nodes": [], "field_candidates": []},
        ready,
    ]
    explore = AsyncMock(side_effect=results)
    monkeypatch.setattr(observation, "explore_v2", explore)

    result = await tools.observe()

    assert result["stage_visible"] is True
    assert result["field_evidence"] == ["campaign_name"]
    assert explore.await_count == 2
    wait.assert_awaited_once_with(tools.browser)


async def test_observation_skips_nonunique_nonfield_nodes_and_records_the_reason(
    monkeypatch, tmp_path
):
    import app.config
    import modules.whatsapp.observation as observation
    import modules.whatsapp.tools as tools_module
    from modules.whatsapp.contract import SETUP_URL, ReviewedUI, SetupStop, V2Authorization
    from modules.whatsapp.tools import V2BrowserTools

    target = urlsplit(SETUP_URL)
    tools = object.__new__(V2BrowserTools)
    tools.diagnostic_mode = False
    tools.browser = SimpleNamespace(page=SimpleNamespace(), trace=_trace())
    tools.ui = ReviewedUI(
        origin=f"{target.scheme}://{target.netloc}",
        tenant=target.path.strip("/").split("/")[0],
    )
    tools.authorization = V2Authorization()
    tools.payload = SimpleNamespace()
    tools.stage = "setup"
    tools.references = {}
    tools.events = []
    tools.pending_write = False
    tools.read_only = False
    tools.reviewed_fields = set()
    tools.reviewed_stages = set()
    tools.observed_bindings = {}
    tools.preview_identity = None
    tools.last_observation = None
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    monkeypatch.setattr(app.config, "ROOT", tmp_path)
    monkeypatch.setattr(
        tools_module,
        "observed_node",
        AsyncMock(side_effect=SetupStop("CONTROL_IDENTITY_NOT_UNIQUE")),
    )
    monkeypatch.setattr(
        observation,
        "wait_for_setup_change",
        AsyncMock(),
    )
    explore = AsyncMock(return_value={
        "stage_visible": False,
        "nodes": [{"selector": "#transient", "part": "control"}],
        "field_candidates": [],
        "scope_selector": None,
        "scope_basis": "UNRESOLVED",
        "observation_scope": "DOCUMENT_LIGHT_DOM",
    })
    monkeypatch.setattr(observation, "explore_v2", explore)

    result = await tools.observe()

    assert result["stage_visible"] is False
    assert result["controls"] == []
    skipped = [
        kwargs for args, kwargs in tools.browser.trace.events
        if args and args[0] == "observation_control_skipped"
    ]
    assert skipped
    assert skipped[0]["reason"] == "CONTROL_IDENTITY_NOT_UNIQUE"


async def test_inspect_returns_redacted_target_state_not_a_whole_page_observation(
    monkeypatch,
):
    import modules.whatsapp.tools as tools_module
    from modules.whatsapp.tools import V2BrowserTools

    tools = _write_tools()
    tools.stage = "setup"
    tools.read_only = True
    tools.last_observation = None
    tools.preparations = {}
    tools.failed_approaches = {}
    tools.lock = asyncio.Lock()
    tools.browser.page = SimpleNamespace(evaluate=AsyncMock(return_value={
        "tag": "INPUT",
        "role": None,
        "type": "text",
        "label": " Campaign Name ",
        "visible": True,
        "enabled": False,
        "checked": None,
        "expanded": False,
        "value_present": True,
        "selected_count": 0,
        "invalid": True,
        "validation_error_present": True,
        "value": "PRIVATE_CURRENT_VALUE",
    }))
    entry = {
        "selector": "#campaign-name",
        "identity": "CURRENT_NODE",
        "stage": "setup",
        "field": "campaign_name",
        "part": "control",
        "label": "campaign name",
        "tag": "INPUT",
        "type": "text",
        "enabled": False,
    }
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    tools.resolve = AsyncMock(return_value=(entry, SimpleNamespace()))
    tools.observe = AsyncMock(side_effect=AssertionError("inspect must be target-specific"))
    monkeypatch.setattr(
        tools_module, "observed_node", AsyncMock(return_value="CURRENT_NODE")
    )

    result, _ = await tools.action({"action": "inspect", "reference": "CURRENT"})
    serialized = json.dumps(result)

    assert result["ok"] is True
    assert result["label"] == "campaign name"
    assert result["enabled"] is False
    assert result["value_state"] == "POPULATED_REDACTED"
    assert result["invalid"] is True
    assert result["validation_error_present"] is True
    assert "PRIVATE_CURRENT_VALUE" not in serialized


async def test_real_v2_doer_invokes_its_registered_browser_action_offline():
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from uuid import uuid4

    from app.trace import Trace
    from deep_agents.offline import SyntheticChatModel
    from deep_agents.permissions import InferenceBudget
    from learning.skill_storage.backend import SkillSnapshot
    from modules.whatsapp.contract import V2Authorization
    from modules.whatsapp.agent import create_v2_doer
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.workflow import require_setup_observation

    class BrowserActionModel(SyntheticChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            invoked = any(
                isinstance(message, ToolMessage) and message.name == "browser_action"
                for message in messages
            )
            name, args = (
                ("V2Turn", {"status": "INCOMPLETE"})
                if invoked
                else ("browser_action", {"action": "observe"})
            )
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="",
                tool_calls=[{"id": uuid4().hex, "name": name, "args": args, "type": "tool_call"}],
            ))])

    recorded = _recorded_setup_observation()
    observation = recorded or {"stage": "setup", "stage_visible": True}
    require_setup_observation(observation)
    trace = Trace()
    trace.enable_agent_trace()
    tools = object.__new__(V2BrowserTools)
    tools.diagnostic_mode = False
    tools.read_only = False
    tools.authorization = V2Authorization(
        live=True,
        inference=True,
        edits=True,
        manual_form_test=True,
        persistence=False,
    )
    tools.stage = "setup"
    tools.events = []
    tools.last_observation = None
    tools.images = {}
    tools.pending_write = False
    tools.terminal = None
    tools.preparations = {}
    tools.failed_approaches = {}
    tools.lock = asyncio.Lock()
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    tools.observe = AsyncMock(return_value=observation)
    tools.browser = SimpleNamespace(
        trace=trace,
        budget=lambda: None,
    )
    skill_content = "# Offline fixture\n"
    skill = SkillSnapshot(
        content=skill_content,
        digest=hashlib.sha256(skill_content.encode()).hexdigest(),
        metadata={"status": "offline-test", "source_version": "offline-test"},
    )
    model = BrowserActionModel()
    agent = create_v2_doer(model, tools, trace, InferenceBudget(3), skill)
    action_tool = tools.registered_tools()[0]
    actions = action_tool.args_schema.model_json_schema()["properties"]["action"]["enum"]

    await agent.ainvoke(
        {"messages": [{"role": "user", "content": "Observe the current Setup form."}]},
        {"configurable": {"thread_id": "offline-v2-tool-registration"}, "recursion_limit": 8},
    )

    tools.observe.assert_awaited_once()
    assert {"type", "search", "select"} <= set(actions)
    assert "browser_action" in model.bound_tool_names
    assert any(event.get("tool_name") == "browser_action" for event in trace.events)
    assert tools.authorization.persistence is False
    assert tools.observe.await_count == 1


def _recorded_setup_observation():
    from app.config import ROOT

    trace_path = (
        ROOT
        / "outputs"
        / "human-assisted"
        / "login-whatsapp-be11ba072e934b2a9cb7ffb834ca9417"
        / "agent-trace.json"
    )
    if not trace_path.exists():
        return None
    events = json.loads(trace_path.read_text(encoding="utf-8"))["events"]
    return next(
        event["result"]
        for event in events
        if event.get("kind") == "dom_observation"
        and event.get("result", {}).get("stage") == "setup"
    )


def test_recorded_setup_observation_passes_the_handoff_gate_and_invalid_forms_block():
    from modules.whatsapp.contract import V2Stop
    from modules.whatsapp.workflow import require_setup_observation

    recorded = _recorded_setup_observation()
    if recorded is None:
        pytest.skip("saved sanitized Setup observation is unavailable")

    assert recorded["stage_visible"] is True
    assert recorded["observation_scope"] == "DOCUMENT_LIGHT_DOM"
    assert recorded["observation_counts"]["visible_iframes"] == 0
    assert recorded["persistence_allowed"] is False
    assert require_setup_observation(recorded) is recorded

    with pytest.raises(V2Stop) as caught:
        require_setup_observation({"stage": "setup", "stage_visible": False})
    assert caught.value.code == "WHATSAPP_SETUP_FORM_NOT_VERIFIED"


async def test_operator_waba_opens_with_native_click_even_after_dom_discovery():
    from modules.whatsapp.contract import V2Authorization

    tools = _write_tools()
    tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
    tools.operator_setup_binding = object()
    tools.payload = SimpleNamespace()
    tools.ui = SimpleNamespace(fields={'waba': SimpleNamespace(kind='combobox')})
    tools.reviewed_fields = {'waba'}
    tools.observed_bindings = {}
    tools.preparations = {('setup', 'waba'): 'DOM'}
    tools.failed_approaches = {}
    tools.images = {}
    tools.last_observation = None
    tools.mutations = 0
    tools.terminal = None
    tools.lock = asyncio.Lock()
    tools.browser.page = SimpleNamespace(evaluate=AsyncMock(return_value=True))
    tools.authorize_write = AsyncMock()
    tools.observe = AsyncMock(return_value={'controls': [
        {'field': 'waba', 'part': 'control', 'expanded': True}]})
    loc = SimpleNamespace(click=AsyncMock(), send_click_event=AsyncMock())
    entry = {'selector': '#waba', 'field': 'waba', 'part': 'control', 'tag': 'MAT-SELECT',
             'type': None, 'expanded': False, 'create': False, 'enabled': True}
    tools.resolve = AsyncMock(return_value=(entry, loc))

    result, _ = await tools.action({'action': 'click', 'reference': 'waba', 'field': 'waba'})
    assert result['ok'] is True
    assert result['dropdown_open_verified'] is True
    loc.click.assert_awaited_once()
    loc.send_click_event.assert_not_awaited()
