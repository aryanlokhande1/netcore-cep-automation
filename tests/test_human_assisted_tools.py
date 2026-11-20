import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from urllib.parse import urlsplit

import pytest


def diagnostic_origin():
    from modules.whatsapp.contract import SETUP_URL

    parts = urlsplit(SETUP_URL)
    return f"{parts.scheme}://{parts.netloc}"


def trace_double():
    return SimpleNamespace(
        agent_event=lambda *args, **kwargs: "event-ref",
        safe_tool_arguments=lambda _tool, arguments: {
            key: value for key, value in arguments.items()
            if key in {"action", "reference", "delta_y"}
        },
    )


def make_diagnostic_tools():
    from modules.whatsapp.tools import V2BrowserTools

    browser = SimpleNamespace(
        policy=SimpleNamespace(origins={diagnostic_origin()}),
        trace=trace_double(),
    )
    return V2BrowserTools(browser, diagnostic_origin=diagnostic_origin())


def test_diagnostic_tools_require_no_campaign_or_write_context():
    tools = make_diagnostic_tools()

    assert tools.diagnostic_mode is True
    assert tools.read_only is True
    assert tools.payload is None
    assert tools.ui is None
    assert tools.authorization is None


def test_diagnostic_action_schema_exposes_only_redacted_read_operations():
    from modules.whatsapp.contract import V2DiagnosticAction

    schema = V2DiagnosticAction.model_json_schema()
    branches = {
        branch["properties"]["action"]["const"]: branch
        for branch in schema["oneOf"]
    }
    assert set(branches) == {"observe", "inspect", "readback", "scroll", "navigate"}
    assert branches["observe"]["not"]["anyOf"] == [
        {"required": ["delta_y"]},
        {"required": ["reference"]},
    ]
    assert branches["inspect"]["required"] == ["reference"]
    assert branches["inspect"]["not"] == {"anyOf": [{"required": ["delta_y"]}]}
    assert branches["scroll"]["required"] == ["reference"]
    tool_schema = make_diagnostic_tools().registered_tools()[0].args_schema.model_json_schema()
    assert tool_schema["oneOf"] == schema["oneOf"]

    allowed = (
        {"action": "observe"},
        {"action": "inspect", "reference": "CURRENT"},
        {"action": "readback", "reference": "CURRENT"},
        {"action": "scroll", "reference": "CURRENT", "delta_y": 80},
        {"action": "navigate", "reference": "OBSERVED_LINK"},
    )
    for arguments in allowed:
        assert V2DiagnosticAction.model_validate(arguments)

    rejected = (
        {"action": "click", "reference": "CURRENT"},
        {"action": "type", "reference": "CURRENT"},
        {"action": "search", "reference": "CURRENT"},
        {"action": "select", "reference": "CURRENT"},
        {"action": "advance"},
        {"action": "persist"},
        {"action": "screenshot"},
        {"action": "observe", "reference": "CURRENT"},
        {"action": "observe", "reference": None},
        {"action": "navigate", "reference": "OBSERVED_LINK", "url": "https://elsewhere.invalid"},
        {"action": "readback", "reference": "CURRENT", "field": "campaign_name"},
    )
    for arguments in rejected:
        with pytest.raises(ValueError):
            V2DiagnosticAction.model_validate(arguments)


async def test_diagnostic_observation_uses_no_payload_and_redacts_control_values(monkeypatch):
    import modules.whatsapp.observation as observation

    tools = make_diagnostic_tools()
    tools.browser.page = SimpleNamespace(
        url=AsyncMock(return_value="https://app.invalid/tenant/whatsapp"),
        evaluate=AsyncMock(return_value=True),
    )
    tools.protect = AsyncMock(return_value="DOCUMENT")
    tools.readonly_navigation_links = AsyncMock(return_value=[])
    node = {
        "selector": "#private-control",
        "field": "campaign_name",
        "part": "control",
        "tag": "INPUT",
        "role": None,
        "type": "text",
        "label": None,
        "enabled": True,
        "expanded": False,
        "checked": None,
        "value_present": True,
        "selected_count": 0,
    }

    async def explore(browser, ui, payload, stage, *, diagnostic=False, **_kwargs):
        assert ui is None
        assert payload is None
        assert diagnostic is True
        return {"nodes": [node], "truncated": False}

    monkeypatch.setattr(observation, "explore_v2", explore)
    monkeypatch.setattr(
        "modules.whatsapp.tools.observed_node",
        AsyncMock(return_value="CURRENT_NODE"),
    )

    result = await tools.observe_diagnostic()
    serialized = json.dumps(result)

    assert result["controls"][0]["value_state"] == "POPULATED_REDACTED"
    assert result["controls"][0]["reference"]
    assert result["whatsapp_page_observed"] is True
    assert "#private-control" not in serialized
    assert "campaign_name" not in serialized
    assert "value" not in result["controls"][0]


async def test_diagnostic_readback_redacts_unexpected_private_value(monkeypatch):
    import modules.whatsapp.tools as tools_module

    tools = make_diagnostic_tools()
    tools.browser.trace = trace_double()
    tools.browser.page = SimpleNamespace(
        evaluate=AsyncMock(return_value={
            "tag": "INPUT",
            "role": None,
            "type": "text",
            "visible": True,
            "enabled": True,
            "checked": None,
            "expanded": False,
            "value_present": True,
            "selected_count": 0,
            "value": "PRIVATE-ACCOUNT-VALUE",
        }),
    )
    tools.protect = AsyncMock(return_value="DOCUMENT")
    tools.resolve = AsyncMock(return_value=(
        {"selector": "#private-control", "identity": "CURRENT_NODE", "label": "campaign name"},
        SimpleNamespace(),
    ))
    monkeypatch.setattr(
        tools_module,
        "observed_node",
        AsyncMock(return_value="CURRENT_NODE"),
    )
    tools.references = {
        "CURRENT": {
            "selector": "#private-control",
            "identity": "CURRENT_NODE",
            "stage": "diagnostic",
            "label": "campaign name",
        }
    }

    result, _ = await tools.action({"action": "readback", "reference": "CURRENT"})
    serialized = json.dumps(result)

    assert result["ok"] is True
    assert result["value_state"] == "POPULATED_REDACTED"
    assert "PRIVATE-ACCOUNT-VALUE" not in serialized
    assert "#private-control" not in serialized


@pytest.mark.parametrize(
    "action",
    ["click", "type", "search", "select", "advance", "persist"],
)
async def test_diagnostic_write_actions_are_rejected_before_browser_calls(action):
    tools = make_diagnostic_tools()
    tools.browser.trace = trace_double()
    tools.protect = AsyncMock()
    tools.browser.page = SimpleNamespace(
        evaluate=AsyncMock(),
        url=AsyncMock(),
    )
    tools.browser.navigate = AsyncMock()
    tools.events = []

    result, _ = await tools.action({"action": action, "reference": "CURRENT"})

    assert result == {
        "ok": False,
        "error": "INVALID_BROWSER_ACTION_ARGUMENTS",
        "owner": "tool",
        "retry_safe": True,
    }
    tools.protect.assert_not_awaited()
    tools.browser.page.evaluate.assert_not_awaited()
    tools.browser.navigate.assert_not_awaited()


async def test_diagnostic_scroll_uses_only_a_current_observed_reference():
    tools = make_diagnostic_tools()
    tools.browser.page = SimpleNamespace(
        evaluate=AsyncMock(side_effect=[
            [0, 0, 10, 10],
            {"x": 40, "y": 30},
            [0, 80, 10, 10],
        ]),
        scroll=AsyncMock(),
    )
    tools.browser.guard = SimpleNamespace(send=AsyncMock())
    tools.protect = AsyncMock(return_value="DOCUMENT")
    entry = {
        "selector": "#observed-control",
        "identity": ("target", "session", "generation", "document", "frame", "loader", 42),
        "stage": "diagnostic",
        "part": "control",
    }
    tools.resolve = AsyncMock(return_value=(entry, SimpleNamespace()))

    result, _ = await tools.action({
        "action": "scroll",
        "reference": "CURRENT",
        "delta_y": 80,
    })

    assert result == {"ok": True, "scrolled": True}
    tools.browser.page.scroll.assert_awaited_once_with(40, 30, 0, 80)
    tools.browser.guard.send.assert_not_awaited()


async def test_diagnostic_navigation_uses_only_fresh_observed_same_origin_link(monkeypatch):
    import modules.whatsapp.tools as tools_module
    from modules.whatsapp.contract import V2Stop

    source = "https://allowed.invalid/tenant/campaign/unified"
    destination = "https://allowed.invalid/tenant/campaign/whatsapp"
    page = SimpleNamespace(
        url=AsyncMock(return_value=source),
        evaluate=AsyncMock(return_value=True),
    )

    async def navigate(url):
        page.url.return_value = url

    browser = SimpleNamespace(
        page=page,
        trace=trace_double(),
        navigate=AsyncMock(side_effect=navigate),
    )
    tools = object.__new__(tools_module.V2BrowserTools)
    tools.browser = browser
    tools.diagnostic_mode = True
    tools.read_only = True
    tools.origin = "https://allowed.invalid"
    tools.stage = "diagnostic"
    tools.references = {
        "OBSERVED_LINK": {
            "part": "navigation",
            "selector": "#whatsapp-link",
            "href": destination,
            "identity": "CURRENT_NODE",
            "document": "CURRENT_DOCUMENT",
            "stage": "diagnostic",
        }
    }
    tools.protect = AsyncMock(return_value="CURRENT_DOCUMENT")
    tools.observe_diagnostic = AsyncMock(return_value={"page_observed": True})
    tools.install_guard = AsyncMock()
    monkeypatch.setattr(
        tools_module,
        "observed_node",
        AsyncMock(return_value="CURRENT_NODE"),
    )
    event = {}

    assert await tools.navigate_readonly("OBSERVED_LINK", event) == {"page_observed": True}
    browser.navigate.assert_awaited_once_with(destination)
    tools.install_guard.assert_not_awaited()
    assert event["progress"] is True

    tools.references["OBSERVED_LINK"] = {
        "part": "navigation",
        "selector": "#whatsapp-link",
        "href": "https://elsewhere.invalid/tenant/whatsapp",
        "identity": "CURRENT_NODE",
        "document": "CURRENT_DOCUMENT",
        "stage": "diagnostic",
    }
    with pytest.raises(V2Stop):
        await tools.navigate_readonly("OBSERVED_LINK", {})
    assert browser.navigate.await_count == 1


async def test_diagnostic_persistence_methods_stop_before_browser_or_filesystem_use():
    from modules.whatsapp.contract import V2Stop

    tools = make_diagnostic_tools()
    tools.browser.page = SimpleNamespace(evaluate=AsyncMock())

    with pytest.raises(V2Stop):
        await tools.advance()
    with pytest.raises(V2Stop):
        await tools.records()
    with pytest.raises(V2Stop):
        tools.write_journal("SAVE", "PENDING")

    tools.browser.page.evaluate.assert_not_awaited()
