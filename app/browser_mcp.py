"""Private stdio adapter for controller-owned Netcore browser handlers."""

import asyncio
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Literal

from mcp import types
from mcp.server import Server
from mcp.server.stdio import stdio_server
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from modules.whatsapp.contract import v2_action_arguments
from modules.whatsapp.observer import deterministic_readback


Role = Literal['doer', 'observer', 'teacher']


class EmptyArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class TraceArguments(EmptyArguments):
    limit: int = Field(default=20, ge=1, le=50)


@dataclass(frozen=True)
class ActiveStep:
    run_id: str
    browser: object
    session: object
    stage: str


@dataclass(frozen=True)
class ToolScope:
    """Supplied by the trusted host. None from current() revokes the scope."""

    step: ActiveStep
    role: Role
    operations: frozenset[str]
    current: Callable[[], ActiveStep | None]
    require_current: Callable[[], Awaitable[None]]
    campaign_name_reference: str | None = None
    ga_tracking_reference: str | None = None


@dataclass(frozen=True)
class BrowserTool:
    name: str
    operation: str
    roles: frozenset[Role]
    schema: dict
    invoke: Callable[[dict], Awaitable[object]]
    description: str


class ToolDenied(Exception):
    pass


OBSERVER_FAILURE_CATEGORIES = frozenset({
    'SMS_SETUP_PROTECTED_ROUTE_UNVERIFIED', 'SMS_SETUP_DOCUMENT_CHANGED',
    'SMS_STAGE_ROUTE_UNVERIFIED', 'SMS_STAGE_DOCUMENT_CHANGED',
    'MCP_LOGIN_OWNED_TARGET_UNAVAILABLE', 'MCP_OWNED_SESSION_CHANGED',
    'OPERATOR_CONFIRMED_TAB_OR_SESSION_CHANGED', 'OPERATOR_CONFIRMED_SESSION_REDIRECTED_TO_SIGN_IN',
    'OPERATOR_TENANT_ROUTE_CHANGED', 'AUTHENTICATION_OR_SECURITY_STOP', 'SECURITY_OBSERVATION_INVALID',
    'OBSERVER_TIMEOUT', 'OBSERVER_PERMISSION_DENIED', 'OBSERVER_HANDLER_ERROR',
})


class BrowserRouter:
    def __init__(self, scope: ToolScope, *, login=None, whatsapp=None, held_ga=None, observer=None, task_doer=None):
        if scope.role not in {'doer', 'observer', 'teacher'}:
            raise ValueError('Invalid trusted role')
        bindings = [(field, ref) for field, ref in
                    (('campaign_name', scope.campaign_name_reference), ('ga_tracking', scope.ga_tracking_reference))
                    if ref is not None]
        if len(bindings) > 1:
            raise ValueError('Only one field can be authorized')
        self.write_binding = bindings[0] if bindings else None
        if self.write_binding and (
                scope.role != 'doer' or scope.step.stage != 'setup'
                or scope.operations != frozenset({'type'}) or whatsapp is None):
            raise ValueError('Invalid single-field write scope')
        self.scope = scope
        self.field_write_dispatched = False
        self.login = login
        self.whatsapp = whatsapp
        self.held_ga = held_ga
        self.observer = observer
        self.task_doer = task_doer
        if observer is not None and (scope.role != 'observer' or any((login, whatsapp, held_ga, task_doer))):
            raise ValueError('Invalid independent observer scope')
        if task_doer is not None and (scope.role != 'doer' or any((login, whatsapp, held_ga, observer))):
            raise ValueError('Invalid independent task Doer scope')
        self.lock = asyncio.Lock()
        self.trace = scope.step.browser.trace
        if self.trace.run_id != scope.step.run_id or scope.step.session is None:
            raise ValueError('Invalid trusted run or session')
        owners = [login, whatsapp, held_ga.setup if held_ga else None, observer, task_doer]
        if any(owner.browser is not scope.step.browser for owner in owners if owner is not None):
            raise ValueError('Handler browser differs from the owned browser')

    def _current(self):
        step = self.scope.current()
        intended = self.scope.step
        return (step is not None and step.run_id == intended.run_id and step.browser is intended.browser
                and step.session == intended.session and step.stage == intended.stage
                and not getattr(step.browser, 'close_started', False)
                and all(handler is None or handler.stage == intended.stage
                        for handler in (self.whatsapp, self.observer, self.task_doer)))

    def _catalog(self):
        for owner, role in ((self.observer, 'observer'), (self.task_doer, 'doer')):
            if owner is not None:
                for handler in owner.registered_tools():
                    yield from self._existing_tool(handler, frozenset({role}))
        if self.login is not None and self.scope.step.stage == 'login':
            for handler in self.login.registered_tools():
                roles = frozenset({'observer', 'doer'} if handler.name == 'verify_dashboard' else {'doer'})
                yield from self._existing_tool(handler, roles)
        if self.whatsapp is not None:
            schema = self.whatsapp.action_schema()
            full_schema = schema.model_json_schema()
            for action, (required, allowed) in v2_action_arguments(schema).items():
                arguments = {'type': 'object', 'additionalProperties': False,
                             'properties': {key: full_schema['properties'][key] for key in allowed},
                             'required': list(required)}
                if '$defs' in full_schema:
                    arguments['$defs'] = full_schema['$defs']
                roles = frozenset({'doer', 'observer'} if action in {'observe', 'verify', 'readback'} else {'doer'})
                if action == 'type' and self.write_binding:
                    field, reference = self.write_binding
                    arguments['properties']['field'] = {'type': 'string', 'const': field}
                    arguments['properties']['reference'] = {'type': 'string', 'const': reference}

                async def dispatch(values, action=action, schema=schema):
                    try:
                        schema.model_validate({'action': action, **values})
                    except ValidationError:
                        raise ToolDenied('MCP_INVALID_BROWSER_ARGUMENTS') from None
                    if self.write_binding:
                        field, reference = self.write_binding
                        if values != {'field': field, 'reference': reference}:
                            raise ToolDenied('MCP_FIELD_WRITE_OUTSIDE_SCOPE')
                        self.field_write_dispatched = True
                    result, image = await self.whatsapp.action({'action': action, **values})
                    return self._content(result, image)

                yield BrowserTool(f'browser_{action}', action, roles, arguments, dispatch,
                                  f'Run the existing protected browser action {action}.')
            if (self.scope.step.stage == 'setup' and getattr(self.whatsapp, 'mcp_ga_scope', False)
                    and not self.whatsapp.read_only):
                async def reveal(values):
                    EmptyArguments.model_validate(values)
                    await self.whatsapp.reveal_ga_advanced()
                    return {'result': 'OBSERVE_TO_VERIFY_ADVANCED_DISCLOSURE'}

                yield BrowserTool('reveal_ga_advanced', 'reveal_ga_advanced', frozenset({'doer'}),
                                  EmptyArguments.model_json_schema(), reveal, 'Open the bound GA disclosure without editing fields.')
            if not getattr(self.whatsapp, 'diagnostic_mode', False):
                async def readback(values):
                    EmptyArguments.model_validate(values)
                    return await deterministic_readback(self.whatsapp)

                yield BrowserTool('independent_readback', 'readback', frozenset({'observer'}),
                                  EmptyArguments.model_json_schema(), readback, 'Independently read current approved fields.')
                if self.scope.step.stage in {'schedule', 'preview'}:
                    async def records(values):
                        EmptyArguments.model_validate(values)
                        return await self.whatsapp.records()

                    yield BrowserTool('persisted_record_readback', 'reconcile', frozenset({'observer'}),
                                      EmptyArguments.model_json_schema(), records, 'Read the independently reviewed saved record.')
        if self.held_ga is not None and self.scope.step.stage == 'setup':
            for handler in self.held_ga.registered():
                yield from self._existing_tool(handler, frozenset({'teacher'}))
        yield BrowserTool('redacted_trace', 'trace', frozenset({'teacher'}), TraceArguments.model_json_schema(),
                          self._read_trace, 'Read bounded redacted event metadata from the active run.')

    def _existing_tool(self, handler, roles):
        schema = handler.args_schema
        shape = {**schema.model_json_schema(), 'additionalProperties': False}

        async def dispatch(values):
            checked = schema.model_validate(values, strict=True)
            arguments = checked.model_dump(exclude_unset=True)
            if handler.response_format == 'content_and_artifact':
                content, _ = await handler.coroutine(**arguments)
                blocks = []
                for item in content:
                    if item['type'] == 'text':
                        blocks.extend(self._content(json.loads(item['text'])))
                    elif item['type'] == 'image_url':
                        prefix, _, data = item['image_url']['url'].partition(',')
                        if prefix == 'data:image/png;base64':
                            blocks.append(types.ImageContent(type='image', data=data, mimeType='image/png'))
                return blocks
            return await handler.coroutine(**arguments)

        yield BrowserTool(handler.name, handler.name, roles, shape, dispatch, handler.description)

    async def _read_trace(self, arguments):
        limit = TraceArguments.model_validate(arguments).limit
        allowed = ('id', 'kind', 'role', 'stage', 'status', 'code', 'failing_stage', 'action', 'field',
                   'evidence_ref', 'verified_fields', 'click_result')
        events = [entry['agent_execution'] for entry in self.trace.events if 'agent_execution' in entry]
        return {'run_id': self.scope.step.run_id, 'events': [
            {key: event[key] for key in allowed if key in event} for event in events[-limit:]]}

    def _content(self, result, image=None):
        content = [types.TextContent(type='text', text=json.dumps(self.trace.redact_agent(result)))]
        if image:
            content.append(types.ImageContent(type='image', data=image, mimeType='image/png'))
        return content

    def catalog(self):
        if self.field_write_dispatched or not self._current():
            return {}
        return {tool.name: tool for tool in self._catalog()
                if self.scope.role in tool.roles and tool.operation in self.scope.operations}

    async def dispatch(self, name, arguments):
        async with self.lock:
            selected = self.catalog().get(name)
            if selected is None:
                raise ToolDenied('MCP_TOOL_OUTSIDE_ACTIVE_STEP_SCOPE')
            if 'action' in arguments or set(arguments) - set(selected.schema.get('properties', {})):
                raise ToolDenied('MCP_INVALID_BROWSER_ARGUMENTS')
            phase = 'protect'
            try:
                await self.scope.require_current()
                if not self._current():
                    raise ToolDenied('MCP_OWNED_SESSION_CHANGED')
                phase = 'invoke'
                output = await selected.invoke(arguments)
            except ValidationError:
                raise ToolDenied('MCP_INVALID_BROWSER_ARGUMENTS') from None
            except Exception as error:
                if self.observer is None:
                    raise
                code = str(error) if isinstance(error, ToolDenied) else getattr(error, 'code', None)
                category = ('OBSERVER_TIMEOUT' if isinstance(error, TimeoutError) else
                            'OBSERVER_PERMISSION_DENIED' if isinstance(error, PermissionError) else
                            'OBSERVER_HANDLER_ERROR')
                if isinstance(code, str) and code in OBSERVER_FAILURE_CATEGORIES:
                    category = code
                detail = {'operation': selected.name, 'phase': phase, 'category': category}
                self.trace.agent_event('mcp_observer_failure', role='observer', stage=self.scope.step.stage, **detail)
                return types.CallToolResult(isError=True, structuredContent=detail, content=[
                    types.TextContent(type='text', text='MCP_BROWSER_HANDLER_FAILED')])
            return output if isinstance(output, list) else self._content(output)


def create_server(router: BrowserRouter | None = None):
    server = Server('netcore-private-browser')

    @server.list_tools()
    async def list_tools():
        return [types.Tool(name=entry.name, description=entry.description, inputSchema=entry.schema)
                for entry in router.catalog().values()] if router else []

    @server.call_tool(validate_input=False)
    async def call_tool(name, arguments):
        try:
            if router is None:
                raise ToolDenied('MCP_NO_ACTIVE_RUN')
            return await router.dispatch(name, arguments or {})
        except ToolDenied as error:
            return types.CallToolResult(isError=True, content=[types.TextContent(type='text', text=str(error))])
        except Exception:
            return types.CallToolResult(isError=True, content=[
                types.TextContent(type='text', text='MCP_BROWSER_HANDLER_FAILED')])

    return server


async def serve(router=None):
    server = create_server(router)
    async with stdio_server() as (incoming, outgoing):
        await server.run(incoming, outgoing, server.create_initialization_options())


if __name__ == '__main__':
    asyncio.run(serve())
