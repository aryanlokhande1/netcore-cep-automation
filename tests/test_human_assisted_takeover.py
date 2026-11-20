"""Focused diagnostic contracts; offline tests do not certify Netcore."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.trace import Trace


def browser_double(monkeypatch, *, query='?view=observed'):
    from modules.whatsapp.contract import SETUP_URL
    from urllib.parse import urlsplit
    import browser.discovery as discovery
    target=urlsplit(SETUP_URL)
    url=f'{target.scheme}://{target.netloc}/'+target.path.strip('/').split('/')[0]+'/dashboard'+query
    current={'primary_document_coherent':True,'expected_host':True,'sdk_page_active':True,
        'primary_target_alias':1,'primary_session_alias':1,'generation':1,
        'document_generation':3,'page_path_sha256':'path-digest'}
    monkeypatch.setattr(discovery,'login_page_context',AsyncMock(return_value=current))
    page=SimpleNamespace(page_id='owned',url=AsyncMock(return_value=url))
    browser=SimpleNamespace(page=page,login_page=page,guard=SimpleNamespace(failed=False,blocking_hosts=set()),
        config=SimpleNamespace(expected_host=target.hostname),policy=SimpleNamespace(permits=lambda u:True),
        browser=SimpleNamespace(context=SimpleNamespace(active_page=AsyncMock(return_value=page))),
        lifecycle_context=lambda:{'process_state':'ALIVE','owned_close_started':False},trace=Trace())
    return browser,current


async def test_query_rejection_is_identified_without_authentication_grant(monkeypatch):
    from modules.whatsapp.workflow import human_assisted_session_safety
    browser,current=browser_double(monkeypatch)
    proof=await human_assisted_session_safety(browser)
    assert proof['diagnostic_session_safe'] is False
    assert proof['predicates']['owned_document_current'] is True
    assert proof['query_present'] is True
    assert proof['failed_predicates']==['query_absent']
    assert 'view=observed' not in json.dumps(proof)


@pytest.mark.parametrize('failure',['guard','ownership','active_page','tenant','fragment','process'])
async def test_session_safety_reports_the_actual_failed_invariant(monkeypatch,failure):
    from modules.whatsapp.workflow import human_assisted_session_safety
    browser,current=browser_double(monkeypatch,query='')
    expected={'guard':'guard_healthy','ownership':'owned_document_current','active_page':'protected_page_active',
        'tenant':'authorized_tenant_route','fragment':'unambiguous_url','process':'owned_process_alive'}[failure]
    if failure=='guard':browser.guard.failed=True
    if failure=='ownership':current['primary_document_coherent']=False
    if failure=='active_page':browser.browser.context.active_page.return_value=SimpleNamespace(page_id='other')
    if failure=='tenant':browser.page.url.return_value=browser.page.url.return_value.replace('/dashboard','-other/dashboard')
    if failure=='fragment':browser.page.url.return_value+='#unreviewed'
    if failure=='process':browser.lifecycle_context=lambda:{'process_state':'UNKNOWN','owned_close_started':False}
    proof=await human_assisted_session_safety(browser)
    assert proof['diagnostic_session_safe'] is False
    assert expected in proof['failed_predicates']


def test_run_menu_is_unavailable_until_current_binding_and_safety():
    from modules.whatsapp.workflow import human_assisted_run_preconditions
    assert human_assisted_run_preconditions({'session_safe':False})=='CURRENT_AUTHORIZED_DOCUMENT_NOT_PROVEN'
    assert human_assisted_run_preconditions({'session_safe':True,'selected_navigation':None})=='OPERATOR_OBSERVED_NAVIGATION_REQUIRED'
    assert human_assisted_run_preconditions({'session_safe':True,'selected_navigation':{'state':'CURRENT_VISIBLE_DOM_REFERENCE'}}) is None


def test_final_artifact_write_does_not_truncate_or_replace_live_log(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import append_live_terminal_event,write_human_assisted_artifacts
    trace=Trace();trace.enable_agent_trace(private_values=('PRIVATE-TENANT',))
    directory=tmp_path/'login-whatsapp-long-log';directory.mkdir()
    log=directory/'live-terminal.log'
    for i in range(100):
        append_live_terminal_event(log,{'timestamp':str(i),'phase':'controller','kind':'event',
            'sequence':i,'reason':'x'*180,'account':'PRIVATE-TENANT'},trace)
    original=log.read_bytes();assert len(original)>12000
    write_human_assisted_artifacts(tmp_path,trace=trace,result={'run_id':'long-log','status':'BLOCKED_OPERATOR_REVIEW'},terminal_log=log.read_text())
    assert log.read_bytes()==original
    assert 'PRIVATE-TENANT' not in log.read_text()


def test_terminal_stream_contains_cleanup_and_safety_artifacts(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts
    trace=Trace();trace.enable_agent_trace()
    trace.agent_event('diagnostic_session_safety',diagnostic_session_safe=False,failed_predicates=['guard_healthy'])
    trace.agent_event('browser_cleanup',status='CLOSED_AND_PROFILE_REMOVED')
    trace.agent_event('workflow_finished',status='BLOCKED_SECURITY_OR_SCOPE')
    directory=write_human_assisted_artifacts(tmp_path,trace=trace,result={'run_id':'test-run','status':'BLOCKED_SECURITY_OR_SCOPE'})
    terminal=[json.loads(s) for s in (directory/'terminal-events.jsonl').read_text().splitlines()]
    assert [e['kind'] for e in terminal]==['diagnostic_session_safety','browser_cleanup','workflow_finished']
    assert (directory/'diagnostic-session-safety.json').is_file()
    assert (directory/'candidate-bindings.json').is_file()


def test_login_actions_are_not_reported_as_whatsapp_tool_execution(tmp_path):
    from modules.whatsapp.diagnostic_artifacts import write_human_assisted_artifacts
    trace=Trace();trace.enable_agent_trace();trace.agent_phase='login_doer'
    trace.agent_event('tool_invocation',tool='submit_login',role='doer')
    directory=write_human_assisted_artifacts(tmp_path,trace=trace,result={'run_id':'test-run','task_doer_invoked':False})
    actions=json.loads((directory/'whatsapp-doer-actions.json').read_text())
    assert actions['events']==[]
    assert actions['status']=='NOT_INVOKED'


@pytest.mark.parametrize('arguments',[
    {'action':'observe'}, {'action':'inspect','reference':'opaque'},
    {'action':'readback','reference':'opaque'}, {'action':'scroll','reference':'opaque'},
    {'action':'scroll','reference':'opaque','delta_y':80.0},
    {'action':'navigate','reference':'opaque'},
])
async def test_installed_registered_tool_preserves_omitted_arguments(arguments):
    from test_human_assisted_tools import make_diagnostic_tools
    tools=make_diagnostic_tools()
    tools.action=AsyncMock(return_value=({'ok':True},None))
    await tools.registered_tools()[0].ainvoke(arguments)
    tools.action.assert_awaited_once_with(arguments)


def test_diagnostic_required_reference_cannot_be_null():
    from modules.whatsapp.contract import V2DiagnosticAction
    with pytest.raises(ValueError):V2DiagnosticAction(action='navigate',reference=None)


async def test_graph_stream_reconciles_through_final_cleanup(tmp_path):
    from modules.whatsapp.workflow import build_human_assisted_graph
    from modules.whatsapp.diagnostic_artifacts import append_live_terminal_event,write_human_assisted_artifacts
    trace=Trace();trace.enable_agent_trace();trace.stream_browser_diagnostics=True
    path=tmp_path/'login-whatsapp-test-run';path.mkdir()
    trace.agent_event_sink=lambda e:append_live_terminal_event(path/'live-terminal.log',e,trace)
    async def login(state):
        return {'route':'BLOCKED','authentication_verified':False,'diagnostic_consent':False}
    async def blocked(state):return {'status':'BLOCKED_SECURITY_OR_SCOPE'}
    async def never(state):raise AssertionError('DOER_MUST_NOT_RUN')
    async def finish(state):
        trace.lifecycle('BROWSER',{'state':'CLOSED_AND_PROFILE_REMOVED'})
        return state
    graph=build_human_assisted_graph(login,never,never,blocked,finish,trace=trace)
    await graph.ainvoke({})
    trace.agent_event('workflow_finished',cleanup='CLOSED_AND_PROFILE_REMOVED')
    write_human_assisted_artifacts(tmp_path,trace=trace,result={'run_id':'test-run','status':'BLOCKED_SECURITY_OR_SCOPE'},terminal_log=(path/'live-terminal.log').read_text())
    streamed=[json.loads(line) for line in (path/'terminal-events.jsonl').read_text().splitlines()]
    canonical=[e['agent_execution'] for e in trace.events if 'agent_execution' in e]
    assert streamed==canonical
    assert [e['node'] for e in streamed if e['kind']=='graph_node_started']==['login','blocked','finish']
    assert any(e['kind']=='browser_diagnostic' and e['diagnostic']['state']=='CLOSED_AND_PROFILE_REMOVED' for e in streamed)


async def test_failed_graph_node_records_sanitized_exception_code():
    from modules.whatsapp.contract import SetupStop
    from modules.whatsapp.workflow import build_human_assisted_graph

    trace = Trace()
    trace.enable_agent_trace()

    async def login(_state):
        return {
            "route": "DIAGNOSTIC",
            "authentication_verified": False,
            "diagnostic_consent": True,
        }

    async def fail(_state):
        raise SetupStop("CONTROL_IDENTITY_NOT_UNIQUE")

    async def blocked(_state):
        return {"status": "BLOCKED_OPERATOR_REVIEW"}

    async def finish(state):
        return state

    graph = build_human_assisted_graph(
        login, fail, fail, blocked, finish, trace=trace
    )
    with pytest.raises(SetupStop):
        await graph.ainvoke({})

    failed = next(
        event["agent_execution"]
        for event in trace.events
        if event.get("agent_execution", {}).get("kind") == "graph_node_finished"
        and event["agent_execution"].get("node") == "diagnostic_doer"
    )
    assert failed["error_code"] == "CONTROL_IDENTITY_NOT_UNIQUE"
    assert failed["failing_stage"] == "diagnostic_doer"
