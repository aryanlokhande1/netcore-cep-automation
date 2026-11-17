"""Controlled offline regression cases; no Netcore/browser/provider execution."""
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from langchain_core.messages import AIMessage
from app.trace import Trace
from app.model_settings import ModelSettings
from deep_agents.permissions import InferenceBudget,PermissionMiddleware


def test_login_model_has_sufficient_separate_bounded_output():
    from deep_agents.models import openai_model
    settings=ModelSettings(api_key='CONTROLLED_UNUSED_KEY')
    model=openai_model('doer',settings,login=True)
    assert 1024<=model.max_tokens<=4096
    assert model.reasoning=={'effort':'low'}
    assert model.max_retries==0


@pytest.mark.parametrize('incomplete',[False,True])
async def test_incomplete_model_output_never_becomes_a_requested_tool(incomplete):
    trace=Trace();trace.enable_agent_trace()
    middleware=PermissionMiddleware({'submit_login'},trace,InferenceBudget(2),False)
    message=AIMessage(content='',tool_calls=[{'name':'submit_login','args':{},'id':'controlled'}],
        response_metadata={'status':'incomplete' if incomplete else 'completed',
            'incomplete_details':{'reason':'max_output_tokens'} if incomplete else {}},
        usage_metadata={'input_tokens':10,'output_tokens':500,'total_tokens':510})
    request=SimpleNamespace(tools=[{'name':'submit_login'}]);request.override=lambda **kw:request
    async def handler(_):return SimpleNamespace(result=[message])
    if incomplete:
        with pytest.raises(RuntimeError,match='OUTPUT_LIMIT_INCOMPLETE'):
            await middleware.awrap_model_call(request,handler)
        assert not any(e.get('tool_name')=='submit_login' for e in trace.events)
        assert trace.model_diagnostics[-1]['status']=='FAILED'
    else:
        await middleware.awrap_model_call(request,handler)
        assert any(e.get('tool_name')=='submit_login' for e in trace.events)
    assert trace.token_usage['total_tokens']==510


def valid_lesson():
    return {'owner':'model','finding':'Controlled incomplete output; no credentials submitted.',
        'evidence_refs':['trace:1'],'code_fix_required':True,'safe_next_iteration':False,
        'instructions':[],'change_summary':'Review the configured output capacity.',
        'recommended_code_repairs':[],'execution_facts':{'browser_creation_observed':True,
        'login_tool_calls':2,'task_tool_calls':0,'credential_submissions_observed':0,'observation_records':2}}


def test_installed_native_teacher_transport_and_strict_controller_validation():
    from langchain.agents.structured_output import ProviderStrategy,ProviderStrategyBinding
    from modules.whatsapp.contract import V2Lesson
    binding=ProviderStrategyBinding.from_schema_spec(ProviderStrategy(V2Lesson.model_json_schema(),strict=True).schema_spec)
    raw=binding.parse(AIMessage(content=[{'type':'text','text':json.dumps(valid_lesson())}]))
    lesson=V2Lesson.model_validate(raw)
    assert lesson.execution_facts.credential_submissions_observed==0


def test_teacher_invalid_fields_are_reported_without_private_inputs():
    from modules.whatsapp.contract import V2Lesson
    from deep_agents.permissions import model_failure_details
    from langchain.agents.structured_output import StructuredOutputValidationError
    bad=valid_lesson();bad['owner']='PRIVATE_SECRET_VALUE';bad['execution_facts']['login_tool_calls']='PRIVATE_SECRET_VALUE'
    try:V2Lesson.model_validate(bad)
    except Exception as source:
        error=StructuredOutputValidationError('V2Lesson',source,AIMessage(content=json.dumps(bad)))
        diagnostic=model_failure_details(error)
    assert diagnostic['exception_class']=='StructuredOutputValidationError'
    assert {tuple(e['location']) for e in diagnostic['validation_errors']} >= {('owner',),('execution_facts','login_tool_calls')}
    assert 'PRIVATE_SECRET_VALUE' not in json.dumps(diagnostic)
    assert diagnostic['response_sha256']


def test_login_model_error_is_not_classified_as_authentication_rejection():
    from modules.whatsapp.workflow import login_execution_stop
    stop=login_execution_stop({'error':'OUTPUT_LIMIT_INCOMPLETE'})
    assert stop.owner=='model' and stop.code=='OUTPUT_LIMIT_INCOMPLETE'


@pytest.mark.parametrize('destination',[
    'https://other.invalid/tenant/campaign/whatsapp',
    'https://allowed.invalid/other/campaign/whatsapp',
    'https://allowed.invalid/tenant/campaign/whatsapp/create',
    'https://allowed.invalid/tenant/campaign/whatsapp?send=1',
    'https://allowed.invalid/tenant/campaign/whatsapp/%63reate'])
def test_readonly_navigation_rejects_unapproved_or_mutating_destinations(destination):
    from modules.whatsapp.tools import readonly_whatsapp_destination
    from modules.whatsapp.contract import V2Stop
    with pytest.raises(V2Stop):readonly_whatsapp_destination(
        'https://allowed.invalid/tenant/campaign/unified',destination,'https://allowed.invalid')


def test_readonly_navigation_requires_observed_reference_and_no_field_or_url_argument():
    from modules.whatsapp.contract import V2DiscoveryAction,V2Action
    assert V2DiscoveryAction(action='navigate',reference='CONTROLLED_REFERENCE').action=='navigate'
    with pytest.raises(ValueError):V2Action(action='navigate',reference='CONTROLLED_REFERENCE')
    for arguments in [{'action':'navigate'},{'action':'navigate','reference':'x','field':'campaign_name'},
            {'action':'navigate','reference':'x','url':'https://not-allowed.invalid'}]:
        with pytest.raises(ValueError):V2DiscoveryAction.model_validate(arguments)


async def test_real_login_agent_reads_skill_and_submits_once_in_six_calls():
    from langchain_core.messages import ToolMessage
    from langchain_core.outputs import ChatResult,ChatGeneration
    from uuid import uuid4
    from test_whatsapp_handoff_repair import EarlyReportModel,LoginToolsDouble
    from test_whatsapp_phase1 import session
    from deep_agents.doer import create_doer
    from learning.skill_storage.backend import SkillSnapshot
    from modules.whatsapp.login_handoff import HandoffLoginTools
    from app.state import Observation
    class Model(EarlyReportModel):
        def _generate(self,messages,**kwargs):
            last=next((m for m in reversed(messages) if isinstance(m,ToolMessage)),None)
            if last is None or last.name=='read_approved_skill':
                self.calls+=1
                name='read_approved_skill' if last is None else 'navigate_approved'
                args={} if last is None else {'url':'https://login.netcoresmartech.com/'}
                return ChatResult(generations=[ChatGeneration(message=AIMessage(content='',tool_calls=[{
                    'id':uuid4().hex,'name':name,'args':args,'type':'tool_call'}]))])
            return super()._generate(messages,**kwargs)
    b=session();b.login_submissions=0;b.trace.step_facts=[];b.page.location=b.config.login_url
    async def observe():return Observation(login_entry=not b.login_submissions,
        credential_form=not b.login_submissions,expected_host=bool(b.login_submissions))
    b.observe=observe
    async def submit(mapping):b.claim_login_submission();b.trace.fact('SUBMITTED_ONCE')
    class Tools(HandoffLoginTools):
        _inspect=LoginToolsDouble._inspect
        _resolve=LoginToolsDouble._resolve
    import hashlib
    content='---\nname: netcore-login\ndescription: Controlled offline test skill only.\n---\nObserve before submitting.\n'
    snapshot=SkillSnapshot(content,hashlib.sha256(content.encode()).hexdigest(),{},approved=True)
    tools=Tools(b,SimpleNamespace(submit=submit));model=Model()
    agent,_=create_doer(model,tools,b.trace,InferenceBudget(6),offline=True,skill_snapshot=snapshot)
    await agent.ainvoke({'messages':[{'role':'user','content':'Controlled offline Login flow.'}]},
        {'configurable':{'thread_id':uuid4().hex},'recursion_limit':45})
    assert model.calls==6 and b.login_submissions==1 and tools.approved_login_skill_loaded
    assert any(f['kind']=='SKILL_READ' for f in b.trace.step_facts)


@pytest.mark.parametrize('case',['safe','create','other_tenant','stale','changed_link','redirect'])
async def test_navigation_executor_uses_only_a_fresh_observed_readonly_link(monkeypatch,case):
    import modules.whatsapp.tools as module
    from modules.whatsapp.contract import V2Stop
    source='https://allowed.invalid/tenant/campaign/unified'
    destination='https://allowed.invalid/tenant/campaign/whatsapp'
    trace=Trace();trace.enable_agent_trace();trace.agent_stage='setup'
    page=SimpleNamespace(url=AsyncMock(return_value=source),evaluate=AsyncMock(return_value=case!='changed_link'))
    browser=SimpleNamespace(page=page,trace=trace)
    async def navigate(url):page.url.return_value=source if case=='redirect' else url
    browser.navigate=AsyncMock(side_effect=navigate)
    tools=object.__new__(module.V2BrowserTools);tools.browser=browser;tools.read_only=True
    tools.ui=SimpleNamespace(origin='https://allowed.invalid')
    tools.references={'observed':{'part':'navigation','selector':'#controlled','href':
        destination+'/create' if case=='create' else destination.replace('/tenant/','/other/') if case=='other_tenant' else destination,
        'identity':'CURRENT_NODE','document':'CURRENT_DOC'}}
    tools.protect=AsyncMock(return_value='CURRENT_DOC');tools.install_guard=AsyncMock()
    tools.observe=AsyncMock(return_value={'stage_visible':True})
    monkeypatch.setattr(module,'observed_node',AsyncMock(return_value='STALE_NODE' if case=='stale' else 'CURRENT_NODE'))
    event={}
    if case=='safe':
        assert (await tools.navigate_readonly('observed',event))['stage_visible']
        browser.navigate.assert_awaited_once_with(destination)
        assert event['progress'] and not tools.references
    else:
        with pytest.raises(V2Stop):await tools.navigate_readonly('observed',event)
        assert browser.navigate.await_count==(1 if case=='redirect' else 0)


def test_teacher_factory_uses_strict_json_transport_without_relaxing_controller_schema(monkeypatch):
    import deep_agents.teacher as module
    from deep_agents.offline import SyntheticChatModel
    from modules.whatsapp.contract import V2Lesson
    captured={}
    monkeypatch.setattr(module,'create_deep_agent',lambda **kw:captured.update(kw) or object())
    module.create_teacher(SyntheticChatModel(role='teacher'),Trace(),InferenceBudget(1),schema=V2Lesson)
    fmt=captured['response_format']
    assert fmt.schema_spec.schema_kind=='json_schema'
    assert fmt.schema_spec.schema==V2Lesson.model_json_schema()
    assert fmt.to_model_kwargs()['response_format']['json_schema']['strict'] is True


@pytest.mark.parametrize('read_only',[False,True])
async def test_real_browser_action_dispatch_matches_readonly_navigation_schema(read_only):
    import asyncio
    from modules.whatsapp.tools import V2BrowserTools
    trace=Trace();trace.enable_agent_trace()
    tools=object.__new__(V2BrowserTools)
    for key,value in {'browser':SimpleNamespace(trace=trace),'read_only':read_only,
        'stage':'setup','events':[],'preparations':{},'failed_approaches':{},'last_observation':None,
        'lock':asyncio.Lock(),'pending_write':False,'terminal':None}.items():setattr(tools,key,value)
    tools.protect=AsyncMock();tools.navigate_readonly=AsyncMock(return_value={'page_observed':True})
    result,_=await tools.action({'action':'navigate','reference':'CONTROLLED'})
    if read_only:
        assert result['ok'];tools.navigate_readonly.assert_awaited_once()
    else:
        assert not result['ok'];tools.navigate_readonly.assert_not_awaited()
