"""Exercise the private MCP pipe with a synthetic, read-only owned step."""

import asyncio
import json
import sys
from types import SimpleNamespace

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from app.browser_mcp import ActiveStep, BrowserRouter, ToolScope, serve
from app.trace import Trace
from modules.whatsapp.contract import V2ManualNoVisionAction


async def fixture_server():
    trace = Trace()
    trace.enable_agent_trace(private_values=('synthetic-private-value',))
    trace.agent_event('fixture_ready', role='controller', status='READY', detail='synthetic-private-value')
    browser = SimpleNamespace(trace=trace)
    step = ActiveStep(trace.run_id, browser, object(), 'setup')

    async def require_current():
        return None

    async def unexpected_action(_):
        raise AssertionError('A rejected mutation reached the action handler')

    scope = ToolScope(step, 'teacher', frozenset({'trace', 'click'}), lambda: step, require_current)
    tools = SimpleNamespace(browser=browser, stage='setup', action_schema=lambda: V2ManualNoVisionAction,
                            action=unexpected_action)
    await serve(BrowserRouter(scope, whatsapp=tools))


async def check():
    params = StdioServerParameters(command=sys.executable, args=['-m', __spec__.name, '--fixture-server'])
    async with stdio_client(params) as (incoming, outgoing):
        async with ClientSession(incoming, outgoing) as client:
            negotiated = await client.initialize()
            assert negotiated.serverInfo.name == 'netcore-private-browser'
            listed = (await client.list_tools()).tools
            assert [entry.name for entry in listed] == ['redacted_trace']
            assert listed[0].inputSchema['properties']['limit']['maximum'] == 50
            result = await client.call_tool('redacted_trace', {'limit': 1})
            assert not result.isError
            assert json.loads(result.content[0].text)['events'][0]['kind'] == 'fixture_ready'
            assert 'synthetic-private-value' not in result.content[0].text
            denied = await client.call_tool('browser_click', {'reference': 'fixture'})
            assert denied.isError and denied.content[0].text == 'MCP_TOOL_OUTSIDE_ACTIVE_STEP_SCOPE'
    print('MCP stdio initialized. Typed trace listed and invoked. Mutation denied.')


if __name__ == '__main__':
    asyncio.run(fixture_server() if sys.argv[1:] == ['--fixture-server'] else check())
