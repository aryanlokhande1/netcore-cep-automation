"""An in-process MCP connection to an already owned browser step."""

import json
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import timedelta

from langchain_core.tools import StructuredTool
from mcp.shared.memory import create_connected_server_and_client_session

from app.browser_mcp import ActiveStep, BrowserRouter, OBSERVER_FAILURE_CATEGORIES, Role, ToolScope, create_server
from browser.observed_control import protected_document
from modules.whatsapp.contract import V2Stop


@dataclass(frozen=True)
class StepTools:
    role: Role
    stage: str
    operations: frozenset[str]
    campaign_name_reference: str | None = None
    ga_tracking_reference: str | None = None


class StepClient:
    def __init__(self, client, listed, trace, role, is_active):
        self.client = client
        self.tools = {tool.name: tool for tool in listed}
        self.trace = trace
        self.role = role
        self.is_active = is_active

    async def call(self, name, arguments=None):
        if not self.is_active():
            raise V2Stop('MCP_STEP_SCOPE_REVOKED', owner='authorization', unsafe=True)
        if name not in self.tools:
            raise V2Stop('MCP_STEP_TOOL_UNAVAILABLE', owner='authorization', unsafe=True)
        self.trace.agent_event('mcp_tool_request', role=self.role, tool=name)
        try:
            result = await self.client.call_tool(name, arguments or {})
        except Exception:
            raise V2Stop('MCP_TRANSPORT_FAILED', owner='tool', unsafe=True) from None
        if result.isError:
            failure = V2Stop('MCP_BROWSER_DISPATCH_FAILED', owner='tool', unsafe=True)
            detail = result.structuredContent or {}
            if (self.role == 'observer' and detail.get('operation') == name
                    and detail.get('phase') in ('protect', 'invoke')
                    and isinstance(detail.get('category'), str) and detail['category'] in OBSERVER_FAILURE_CATEGORIES):
                failure.diagnostic = {key: detail[key] for key in ('operation', 'phase', 'category')}
            raise failure
        return result

    async def read(self, name, arguments=None):
        sms = name.startswith(('inspect_sms_', 'observe_sms_', 'read_sms_', 'set_sms_', 'use_sms_', 'apply_sms_'))
        try:
            result = await self.call(name, arguments)
        except V2Stop as error:
            if sms:
                self.trace.agent_event('sms_mcp_result', role=self.role, tool=name, code=error.code,
                                       diagnostic=getattr(error, 'diagnostic', None))
            raise
        try:
            response = json.loads(result.content[0].text)
        except (AttributeError, IndexError, ValueError):
            raise V2Stop('MCP_READBACK_RESPONSE_INVALID', owner='tool', unsafe=True) from None
        if sms and isinstance(response, dict):
            self.trace.agent_event('sms_mcp_result', role=self.role, tool=name,
                **{key: response[key] for key in ('ok', 'code', 'field', 'already_matching',
                    'needs_independent_readback', 'bound_controls', 'binding_checks', 'fields') if key in response})
        return response

    def agent_tools(self, names=None, *, image_content=True):
        def wrap(tool):
            async def invoke(**arguments):
                if not image_content:
                    return await self.read(tool.name, arguments)
                result = await self.call(tool.name, arguments)
                content = []
                for block in result.content:
                    if block.type == 'text':
                        content.append({'type': 'text', 'text': block.text})
                    elif block.type == 'image':
                        content.append({'type': 'image_url', 'image_url': {
                            'url': f'data:{block.mimeType};base64,{block.data}', 'detail': 'low'}})
                return content, {}

            return StructuredTool.from_function(coroutine=invoke, name=tool.name, description=tool.description,
                                                args_schema=tool.inputSchema,
                                                response_format='content_and_artifact' if image_content else 'content')

        return [wrap(tool) for name, tool in self.tools.items() if names is None or name in names]


@asynccontextmanager
async def open_step(tools, config, *, held_ga=None, document_identity=protected_document, login=False,
                    independent_observer=False, task_doer=False):
    """Give a single graph node the private pipe and revoke it on exit."""
    if not config.operations:
        yield None
        return
    trace = tools.browser.trace
    if independent_observer and (config.role != 'observer' or login or held_ga is not None):
        raise V2Stop('MCP_INVALID_INDEPENDENT_OBSERVER_SCOPE', owner='authorization', unsafe=True)
    if task_doer and (config.role != 'doer' or login or held_ga is not None or independent_observer):
        raise V2Stop('MCP_INVALID_INDEPENDENT_DOER_SCOPE', owner='authorization', unsafe=True)
    try:
        document = document_identity(tools.browser)
    except Exception:
        raise V2Stop('MCP_OWNED_SESSION_NOT_BOUND', owner='browser', unsafe=True) from None
    step = ActiveStep(trace.run_id, tools.browser, document[:3], config.stage)
    active = True

    def current():
        stage = 'login' if login else tools.stage
        if not active or stage != config.stage:
            return None
        return ActiveStep(trace.run_id, tools.browser, document_identity(tools.browser)[:3], stage)

    async def require_current():
        if login:
            await tools.browser.guard.ensure_observable()
            current_document = document_identity(tools.browser)
        else:
            current_document = await tools.protect()
        if current_document[:3] != step.session:
            raise V2Stop('MCP_OWNED_SESSION_CHANGED', owner='browser', unsafe=True)

    scope = ToolScope(step, config.role, config.operations, current, require_current,
                      campaign_name_reference=config.campaign_name_reference,
                      ga_tracking_reference=config.ga_tracking_reference)
    body_error = None
    try:
        router = BrowserRouter(scope, login=tools if login else None,
                               whatsapp=None if login or independent_observer or task_doer else tools,
                               held_ga=held_ga, observer=tools if independent_observer else None,
                               task_doer=tools if task_doer else None)
        trace.agent_event('mcp_scope_open', role=config.role, operations=sorted(config.operations), stage=config.stage)
        async with create_connected_server_and_client_session(create_server(router), timedelta(seconds=60)) as client:
            listed = (await client.list_tools()).tools
            if not listed:
                raise V2Stop('MCP_STEP_CATALOG_EMPTY', owner='authorization', unsafe=True)
            names = sorted(tool.name for tool in listed)
            if any('_sms_' in name for name in names):
                trace.agent_event('sms_mcp_catalog', role=config.role, stage=config.stage, tool_names=names)
            try:
                yield StepClient(client, listed, trace, config.role, lambda: active)
            except Exception as error:
                body_error = error
                raise
            finally:
                active = False
    except V2Stop:
        raise
    except Exception as error:
        if body_error is not None:
            raise body_error from None
        # AnyIO may wrap body exceptions when closing the private task group.
        if isinstance(error, BaseExceptionGroup):
            remaining = error.exceptions
            while len(remaining) == 1 and isinstance(remaining[0], BaseExceptionGroup):
                remaining = remaining[0].exceptions
            if len(remaining) == 1 and isinstance(remaining[0], V2Stop):
                raise remaining[0] from None
        raise V2Stop('MCP_STEP_CONNECTION_FAILED', owner='tool', unsafe=True) from None
    finally:
        active = False
        trace.agent_event('mcp_scope_revoked', role=config.role, stage=config.stage)
