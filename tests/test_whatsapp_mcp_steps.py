from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from langgraph.graph import END, START, StateGraph

pytest.importorskip('mcp')

from app.browser_mcp_client import StepTools, open_step
from app.trace import Trace
from modules.whatsapp.contract import V2ManualNoVisionAction, V2Stop
from modules.whatsapp.mcp_steps import GA_TEACHER_STEP, doer_step, require_retry_candidate, retry_candidate, write_strategy


@pytest.mark.asyncio
async def test_graph_revokes_doer_before_teacher_and_verifies_retry_before_login(tmp_path):
    trace = Trace()
    trace.enable_agent_trace()
    browser = SimpleNamespace(trace=trace)
    document = ('target', 'session', 1, 1, 'frame', 'loader')
    tools = SimpleNamespace(browser=browser, stage='setup', read_only=False,
                            protect=AsyncMock(return_value=document),
                            action=AsyncMock(return_value=({'ok': True}, None)),
                            action_schema=lambda: V2ManualNoVisionAction)
    first_client = None
    observed = []
    saved = {'version': 'a' * 32, 'content_sha256': 'b' * 64}
    folder = tmp_path / 'candidates' / saved['version']
    folder.mkdir(parents=True)
    parent = SimpleNamespace(digest='c' * 64, metadata={})
    lesson = SimpleNamespace(instructions=[SimpleNamespace(action='verify', field='ga_tracking')],
                             evidence_refs=['fixture-evidence'])
    approval = SimpleNamespace(candidate_evaluation=True, fresh_iterations=True)

    async def python_step(state):
        async with open_step(tools, StepTools('doer', 'setup', frozenset()),
                             document_identity=lambda _: pytest.fail('Python step bound the browser')) as client:
            assert client is None
        assert not trace.events
        return state

    async def doer(state):
        nonlocal first_client
        async with open_step(tools, doer_step(tools), document_identity=lambda _: document) as client:
            first_client = client
            assert (await client.read('browser_inspect', {'reference': 'fixture'}))['ok']
        return {**state, 'status': 'STUCK'}

    async def teacher(state):
        with pytest.raises(V2Stop, match='MCP_STEP_SCOPE_REVOKED'):
            await first_client.call('browser_type', {'reference': 'fixture', 'field': 'ga_tracking'})
        async with open_step(tools, GA_TEACHER_STEP, document_identity=lambda _: document) as client:
            assert set(client.tools) == {'redacted_trace'}
            evidence = await client.read('redacted_trace')
            assert any(event['kind'] == 'mcp_scope_revoked' and event['role'] == 'doer'
                       for event in evidence['events'])
        return state

    async def candidate(state):
        digest = write_strategy(saved, parent, lesson, tmp_path, 'ga_tracking')
        ctx = {'candidate_result': saved, 'strategy_sha256': digest}
        assert retry_candidate({**ctx, 'save_may_have_landed': True}, state, approval) == {}
        return {**state, **retry_candidate(ctx, state, approval)}

    async def fresh_login(state):
        snapshot = SimpleNamespace(approved=False, digest=saved['content_sha256'])
        assert 'independent_readback' in require_retry_candidate(
            snapshot, state['candidate_sha256'], state['strategy_sha256'], folder)
        observed.append(('login', Trace().run_id, state['candidate_sha256']))
        return state

    graph = StateGraph(dict)
    nodes = [python_step, doer, teacher, candidate, fresh_login]
    for node in nodes:
        graph.add_node(node.__name__, node)
    for left, right in zip([START, *(node.__name__ for node in nodes)],
                           [*(node.__name__ for node in nodes), END]):
        graph.add_edge(left, right)
    await graph.compile().ainvoke({})
    tools.action.assert_awaited_once_with({'action': 'inspect', 'reference': 'fixture'})
    assert observed[0][0] == 'login' and observed[0][1] != trace.run_id
    assert observed[0][2] == saved['content_sha256']
    with pytest.raises(V2Stop, match='MCP_RETRY_CANDIDATE_DIGEST_MISMATCH'):
        require_retry_candidate(SimpleNamespace(approved=False, digest=saved['content_sha256']),
                                saved['content_sha256'], '0' * 64, folder)


def test_registry_persists_and_reloads_mcp_ga_switch_candidate(tmp_path, monkeypatch):
    from learning.skill_storage import registry
    from modules.whatsapp.contract import ReviewedUI, V2Lesson

    monkeypatch.setattr(registry, 'whatsapp_history', lambda: tmp_path)
    parent = registry.module_baseline('whatsapp')
    report = {'status': 'OBSERVED', 'controls': [
        {'visible': True, 'disabled': False, 'inside_nearby_group': True, 'role': 'switch'}]}
    lesson = V2Lesson(owner='model', finding='One switch is visible in the held tracking group.',
                      evidence_refs=['held-dom'], code_fix_required=False, safe_next_iteration=True,
                      change_summary='Use the observed tracking switch and verify its checked state.',
                      instructions=[{'goal':'setup', 'when':'ga_scoped_control_bound', 'action':'type', 'field':'ga_tracking'},
                                    {'goal':'setup', 'when':'milestone_ready', 'action':'verify', 'field':'ga_tracking'}])
    approved_payload = SimpleNamespace(model_dump=lambda: {})
    ui = ReviewedUI()
    saved = registry.save_whatsapp_candidate(lesson, run_id='a' * 32, parent=parent, task_digest='b' * 64,
        evidence_refs=['held-dom'], payload=approved_payload, ui=ui,
        ga_binding_evidence={'id':'held-dom', 'result':report}, ga_switch_only=True)
    digest = write_strategy(saved, parent, lesson, tmp_path, 'ga_tracking')
    snapshot = registry.whatsapp_skill(candidate=saved['version'], evaluate=True, task_digest='b' * 64, ui=ui)
    strategy = require_retry_candidate(snapshot, saved['content_sha256'], digest, tmp_path/'candidates'/saved['version'])
    assert 'browser_type' in strategy and 'independent_readback' in strategy
    assert not (tmp_path/'approved').exists()
