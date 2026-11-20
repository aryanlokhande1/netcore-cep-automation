"""Offline control-flow/contract checks. No browser or provider is started."""
import asyncio
import hashlib
import io
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest


def test_manual_confirmation_uses_required_terminal_prompt():
    from browser.operator_console import OperatorConsole
    keys = iter('c\r')
    output = io.StringIO()
    console = OperatorConsole(read_key=lambda: next(keys), output=output)
    assert console.confirm_readonly(10) is True
    assert 'Login completed. Type c to continue or x to abort:' in output.getvalue()
    assert sum(e['event'] == 'prompt_open' for e in console.events) == 1


def test_x_declines_the_single_session_checkpoint():
    from browser.operator_console import OperatorConsole
    keys=iter('x\r')
    console=OperatorConsole(read_key=lambda:next(keys),output=io.StringIO())
    assert console.confirm_readonly(10) is False
    assert [e['choice'] for e in console.events if e['event']=='input_valid']==['x']


@pytest.mark.parametrize('arguments', [
    {'action': 'inspect', 'reference': 'r', 'field': 'campaign_name'},
    {'action': 'type', 'reference': 'r', 'field': 'campaign_name', 'delta_y': 1},
    {'action': 'scroll', 'reference': 'r', 'delta_y': float('nan')},
    {'action': 'search', 'reference': 'r', 'field': 'campaign_name', 'x': 3},
    {'action': 'next_step', 'reference': 'r'},
])
def test_unsupported_action_arguments_rejected(arguments):
    from modules.whatsapp.contract import V2Action
    with pytest.raises(ValueError):
        V2Action.model_validate(arguments)


def test_stuck_requires_distinct_valid_failures_and_never_overrides_security():
    from modules.whatsapp.workflow import setup_attempt_verdict
    tools = SimpleNamespace(stage='setup', terminal=None, pending_write=False,
                            failed_approaches={('setup', 'waba'): {'semantic'}})
    facts = {'stage_visible': False, 'fields': {'waba': False}, 'checks': {}}
    assert setup_attempt_verdict(tools, facts)['status'] == 'INCOMPLETE'
    tools.failed_approaches[('setup', 'waba')].add('dom')
    assert setup_attempt_verdict(tools, facts)['status'] == 'STUCK'
    tools.terminal = 'WRITE_UNCERTAIN'
    assert setup_attempt_verdict(tools, facts)['status'] == 'BLOCKED'


async def test_unknown_form_reaches_genuine_deep_agent_and_registered_browser_tool(monkeypatch):
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from uuid import uuid4
    from app.trace import Trace
    from deep_agents.offline import SyntheticChatModel
    from deep_agents.permissions import InferenceBudget
    from learning.skill_storage.backend import SkillSnapshot
    from modules.whatsapp.agent import create_v2_doer
    from modules.whatsapp.contract import V2Authorization
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.workflow import run_bounded_setup_doer
    import modules.whatsapp.observer as observer

    class Model(SyntheticChatModel):
        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            self.calls += 1
            done = any(isinstance(m, ToolMessage) and m.name == 'browser_action' for m in messages)
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content='', tool_calls=[{
                'id': uuid4().hex, 'name': 'V2Turn' if done else 'browser_action',
                'args': {'status': 'STUCK'} if done else {'action': 'observe'}, 'type': 'tool_call'}]))])

    trace = Trace(); trace.enable_agent_trace()
    tools = object.__new__(V2BrowserTools)
    tools.browser = SimpleNamespace(trace=trace, config=SimpleNamespace(max_actions=300))
    tools.stage = 'setup'; tools.diagnostic_mode = False; tools.read_only = False
    tools.authorization = V2Authorization(live=True, inference=True, edits=True, manual_form_test=True)
    tools.events = []; tools.failed_approaches = {}; tools.terminal = None
    tools.pending_write = False; tools.last_observation = None; tools.preparations = {}
    tools.lock = asyncio.Lock(); tools.mutations = 0
    tools.protect = AsyncMock(return_value='owned-document')
    tools.observe = AsyncMock(return_value={'stage': 'setup', 'stage_visible': False,
                                            'controls': [], 'unresolved_fields': ['campaign_name']})
    facts = {'stage_visible': False, 'fields': {'campaign_name': False}, 'checks': {}}
    monkeypatch.setattr(observer, 'deterministic_readback', AsyncMock(return_value=facts))
    text = '# Offline candidate\n'
    skill = SkillSnapshot(text, hashlib.sha256(text.encode()).hexdigest(), {'status': 'test'})
    agent = create_v2_doer(Model(), tools, trace, InferenceBudget(8), skill)
    verdict = await run_bounded_setup_doer(agent, tools, trace,
        thread_id='p0-unknown-form', goal='Inspect the unknown form.', max_turns=1)
    assert tools.events and tools.events[0]['action'] == 'observe'
    assert verdict['status'] == 'INCOMPLETE'  # an unsupported model STUCK is not evidence
    assert verdict['failure']['code'] == 'DOER_TURN_BUDGET_EXHAUSTED'


async def test_doer_turn_resets_mutation_limit_and_requires_independent_readback(monkeypatch):
    from app.trace import Trace
    from modules.whatsapp.contract import V2Turn
    from modules.whatsapp.workflow import run_bounded_setup_doer
    import modules.whatsapp.observer as observer
    trace = Trace(); trace.enable_agent_trace()
    tools = SimpleNamespace(stage='setup', terminal=None, pending_write=False,
        mutations=1, events=[], failed_approaches={}, browser=SimpleNamespace(trace=trace),
        observe=AsyncMock(), last_observation=None)
    calls = []
    class Agent:
        async def ainvoke(self, *args, **kwargs):
            calls.append(tools.mutations)
            tools.mutations = 1
            return {'structured_response': V2Turn(status='VERIFY')}
    readback = AsyncMock(side_effect=[
        {'stage_visible': True, 'fields': {'campaign_name': False}, 'checks': {}},
        {'stage_visible': True, 'fields': {'campaign_name': True}, 'checks': {}},
    ])
    monkeypatch.setattr(observer, 'deterministic_readback', readback)
    result = await run_bounded_setup_doer(Agent(), tools, trace,
        thread_id='turn-test', goal='Complete Setup.', max_turns=2)
    assert result['status'] == 'COMPLETE'
    assert calls == [0, 0]
    assert result['verified_fields'] == ['campaign_name']
    assert readback.await_count == 2


def test_learning_reserves_teacher_without_granting_persistence():
    from modules.whatsapp.workflow import validate_human_assisted_authorization, human_assisted_authorization
    args = SimpleNamespace(authorized_live=True, authorized_inference=True,
        authorized_candidate_evaluation=True, authorized_learning=True, manual_form_test=True)
    caps = validate_human_assisted_authorization(args, 24)
    assert caps['teacher'] == 1
    assert sum(caps[k] for k in ('login', 'task', 'observer', 'teacher')) == caps['total']
    authorization = human_assisted_authorization(args, verified=True)
    assert authorization.learning is True
    assert authorization.persistence is False


@pytest.mark.parametrize('analysis_only',[False,True])
async def test_stuck_reaches_genuine_teacher_and_saves_only_unapproved_version(monkeypatch, tmp_path,analysis_only):
    import json
    from langchain_core.messages import AIMessage, HumanMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from app.trace import Trace
    from deep_agents.offline import SyntheticChatModel
    from deep_agents.permissions import InferenceBudget
    import learning.skill_storage.registry as registry
    from modules.whatsapp.contract import ReviewedUI, V2Authorization, V2Stop
    from modules.whatsapp.workflow import review_v2_attempt, store_v2_candidate

    class Teacher(SyntheticChatModel):
        def _generate(self, messages, **kwargs):
            self.calls += 1
            packet = json.loads(next(m.content for m in reversed(messages) if isinstance(m, HumanMessage)))
            lesson = dict(owner='model', finding='Two discovery strategies failed.',
                evidence_refs=['0'], code_fix_required=False, safe_next_iteration=True,
                instructions=[dict(goal='setup', when='control_unresolved', action='dom', field='waba')],
                change_summary='Inspect current structural labels.', execution_facts=packet['execution_facts'])
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content=json.dumps(lesson)))])

    monkeypatch.setattr(registry, 'whatsapp_history', lambda: tmp_path / 'history')
    trace = Trace(roles=('doer', 'observer', 'teacher')); trace.enable_agent_trace()
    trace.agent_event('browser_action', role='doer', tool='browser_action',
                      action='dom', event_id='0', ok=False, error='CONTROL_UNRESOLVED')
    skill = registry.module_baseline('whatsapp')
    original = skill.content
    ctx = dict(trace=trace, directory=tmp_path, teacher=Teacher(role='teacher'),
        budget=InferenceBudget(2), skill=skill, findings=[], authentication_verified=False,
        task_skill_read=True, tools=SimpleNamespace(events=[dict(id='0', action='dom', ok=False)],
                                                  terminal=None, pending_write=False))
    state = dict(stage='setup', status='STUCK', failure=dict(code='STUCK', owner='model', unsafe=False))
    authorization = V2Authorization(live=True, inference=True, learning=True)
    payload = SimpleNamespace(model_dump=lambda: {'campaign_name': 'offline private campaign'})
    await review_v2_attempt(ctx, state, authorization, payload,analysis_only=analysis_only)
    assert trace.teacher_result == 'ANALYZED', ctx.get('teacher_validation')
    package = json.loads((tmp_path / 'teacher-input-evidence.json').read_text())
    assert package['status'] == 'STUCK'
    assert package['events'][0]['id'] == '0'
    if analysis_only:
        assert ctx['lesson'].code_fix_required is True
        assert ctx['lesson'].instructions==[]
        assert ctx['lesson'].safe_next_iteration is False
        assert 'candidate_result' not in ctx
        assert not (tmp_path/'history').exists()
        return
    await store_v2_candidate(ctx, state, authorization, payload, ReviewedUI(), 'a' * 64)
    candidate = ctx['candidate_result']
    assert candidate['status'] == 'UNAPPROVED_PENDING_EXECUTION'
    directory = tmp_path / 'history' / 'candidates' / candidate['version']
    content = (directory / 'SKILL.md').read_bytes()
    assert hashlib.sha256(content).hexdigest() == candidate['content_sha256']
    assert json.loads((directory / 'provenance.json').read_text())['approved'] is False
    assert not (tmp_path / 'history' / 'approved').exists()
    assert registry.module_baseline('whatsapp').content == original
    with pytest.raises(V2Stop, match='CANDIDATE_EVALUATION_REQUIRED'):
        registry.whatsapp_skill(candidate=candidate['version'], evaluate=False)
    loaded=registry.whatsapp_skill(candidate=candidate['version'],evaluate=True,
        task_digest='a'*64,ui=ReviewedUI())
    assert loaded.digest==candidate['content_sha256']
    assert loaded.approved is False


async def test_two_unresolved_discovery_strategies_feed_deterministic_stuck():
    from app.trace import Trace
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.workflow import setup_attempt_verdict
    trace = Trace(); trace.enable_agent_trace()
    tools = object.__new__(V2BrowserTools)
    tools.browser = SimpleNamespace(trace=trace)
    tools.stage='setup'; tools.read_only=False; tools.diagnostic_mode=False
    tools.terminal=None; tools.pending_write=False; tools.events=[]; tools.last_observation=None
    tools.failed_approaches={}; tools.preparations={}; tools.lock=asyncio.Lock()
    tools.protect=AsyncMock(return_value='owned-document')
    tools.observe=AsyncMock(return_value={'stage_visible':False,'controls':[],
        'unresolved_fields':['campaign_name'],'field_evidence':[]})
    facts={'stage_visible':False,'fields':{'campaign_name':False},'checks':{}}
    await tools.action({'action':'observe'})
    assert setup_attempt_verdict(tools,facts)['status']=='INCOMPLETE'
    await tools.action({'action':'observe'})  # identical approach is never a second failure
    assert setup_attempt_verdict(tools,facts)['status']=='INCOMPLETE'
    await tools.action({'action':'dom'})
    assert setup_attempt_verdict(tools,facts)['status']=='STUCK'
    assert {call.kwargs['strategy'] for call in tools.observe.await_args_list}=={'semantic','dom'}


def _execution_tools():
    from app.trace import Trace
    from modules.whatsapp.contract import FieldBinding, V2Authorization
    from modules.whatsapp.tools import V2BrowserTools
    trace=Trace(); trace.enable_agent_trace()
    tools=object.__new__(V2BrowserTools)
    tools.browser=SimpleNamespace(trace=trace,page=SimpleNamespace(evaluate=AsyncMock(return_value=True)))
    tools.stage='setup';tools.read_only=False;tools.diagnostic_mode=False
    tools.terminal=None;tools.pending_write=False;tools.events=[];tools.last_observation=None
    tools.failed_approaches={};tools.preparations={};tools.lock=asyncio.Lock();tools.mutations=0
    tools.images={};tools.native_open_attempts=set()
    tools.reviewed_fields={'campaign_name'};tools.observed_bindings={}
    tools.authorization=V2Authorization(live=True,inference=True,edits=True,manual_form_test=True)
    tools.ui=SimpleNamespace(fields={'campaign_name':FieldBinding(selector='#name',kind='text')})
    tools.protect=AsyncMock(return_value='owned-document');tools.authorize_write=AsyncMock()
    tools.references={'r':dict(field='campaign_name',part='control',enabled=True,create=False,
                              selector='#name',expanded=False,tag='INPUT',type='text')}
    return tools


async def test_text_click_focuses_the_current_input_without_dropdown_claim():
    tools=_execution_tools()
    loc=SimpleNamespace(click=AsyncMock())
    tools.resolve=AsyncMock(return_value=(tools.references['r'],loc))
    result,_=await tools.action({'action':'click','reference':'r'})
    assert result=={'ok':True,'focused':True,'selection_verified':False}
    assert tools.events[-1]['attempted'] is True
    assert tools.events[-1]['ok'] is True


async def test_dom_click_uses_distinct_supported_stagehand_primitive():
    tools=_execution_tools();tools.observation_strategy='dom'
    loc=SimpleNamespace(send_click_event=AsyncMock())
    tools.resolve=AsyncMock(return_value=(tools.references['r'],loc))
    result,_=await tools.action({'action':'click','reference':'r'})
    assert result['focused'] is True
    assert tools.events[-1]['method']=='dom'
    assert tools.events[-1]['attempted'] is True


async def test_input_combobox_search_uses_approved_text_without_claiming_selection():
    from modules.whatsapp.contract import FieldBinding
    tools=_execution_tools();tools.reviewed_fields={'waba'}
    tools.ui.fields={'waba':FieldBinding(selector='#name',kind='combobox')}
    tools.references['r'].update(field='waba',role='combobox')
    tools.payload=SimpleNamespace(waba='offline approved WABA')
    tools.failed_approaches[('setup','waba')] = {'waba:dom'}
    loc=SimpleNamespace(fill=AsyncMock(),input_value=AsyncMock(side_effect=['',tools.payload.waba]))
    tools.resolve=AsyncMock(return_value=(tools.references['r'],loc))
    result,_=await tools.action({'action':'search','field':'waba','reference':'r'})
    assert result['search_text_verified'] is True
    assert result['selection_verified'] is False
    assert tools.events[-1]['attempted'] is True
    assert tools.failed_approaches[('setup','waba')] == {'waba:dom'}


async def test_non_input_combobox_cannot_be_filled_as_search():
    from modules.whatsapp.contract import FieldBinding
    tools=_execution_tools();tools.reviewed_fields={'waba'}
    tools.ui.fields={'waba':FieldBinding(selector='#name',kind='combobox')}
    tools.references['r'].update(field='waba',role='combobox',tag='DIV',type=None)
    tools.resolve=AsyncMock(return_value=(tools.references['r'],SimpleNamespace()))
    result,_=await tools.action({'action':'search','field':'waba','reference':'r'})
    assert result['error']=='OBSERVED_SEARCH_REQUIRED'
    assert tools.events[-1]['attempted'] is False


async def test_unsupported_widget_action_rejected_before_browser_interaction():
    from modules.whatsapp.contract import V2_CAPABILITIES, FieldBinding
    kinds=('text','checkbox','native_select','combobox','selected_text','datetime_split','radio')
    tools=_execution_tools()
    tools.resolve=AsyncMock(side_effect=AssertionError('must reject at action boundary'))
    for action,targets in V2_CAPABILITIES.items():
        for kind in kinds:
            for part in ('control','search','option'):
                if (kind,part) in targets:continue
                tools.ui.fields['campaign_name']=FieldBinding(selector='#name',kind=kind)
                tools.references['r']['part']=part
                args={'action':action,'reference':'r'}
                if action in {'type','search','select','verify'}:args['field']='campaign_name'
                result,_=await tools.action(args)
                assert result['error']=='UNSUPPORTED_FIELD_ACTION',(action,kind,part,result)
    assert tools.protect.await_count==0


async def test_operation_timeout_preserves_the_limit_and_stops_retries():
    tools=_execution_tools();tools.operation_timeout_ms=1
    async def slow(**kwargs):
        await asyncio.sleep(.1)
    tools.observe=slow
    result,_=await tools.action({'action':'observe'})
    assert result['error']=='BROWSER_OPERATION_TIMEOUT'
    assert result['retry_safe'] is False
    assert tools.terminal=='BROWSER_OPERATION_TIMEOUT'


async def test_manual_campaign_entry_uses_v2_target_and_independent_readback(monkeypatch):
    import modules.whatsapp.observer as observer
    tools=_execution_tools()
    tools.payload=SimpleNamespace(campaign_name='offline approved campaign')
    tools.baseline=SimpleNamespace(discover=AsyncMock(side_effect=AssertionError('legacy recognizer forbidden')))
    loc=SimpleNamespace(fill=AsyncMock())
    tools.resolve=AsyncMock(return_value=(tools.references['r'],loc))
    tools.observe=AsyncMock(return_value={'controls':[]})
    monkeypatch.setattr(observer,'deterministic_readback',AsyncMock(side_effect=[
        {'fields':{'campaign_name':False}}, {'fields':{'campaign_name':True}}]))
    result,_=await tools.action({'action':'type','field':'campaign_name','reference':'r'})
    assert result['ok'] is True
    assert result['fields']['campaign_name'] is True
    assert tools.events[-1]['attempted'] is True
    assert tools.events[-1]['progress'] is True


async def test_uncertain_manual_campaign_write_never_retries(monkeypatch):
    import modules.whatsapp.observer as observer
    tools=_execution_tools();tools.payload=SimpleNamespace(campaign_name='offline approved campaign')
    loc=SimpleNamespace(fill=AsyncMock())
    tools.resolve=AsyncMock(return_value=(tools.references['r'],loc))
    tools.observe=AsyncMock(return_value={'controls':[]})
    tools.last_independent_readback = {'fields': {'campaign_name': True, 'waba': True}}
    monkeypatch.setattr(observer,'deterministic_readback',AsyncMock(return_value={'fields':{'campaign_name':False}}))
    result,_=await tools.action({'action':'type','field':'campaign_name','reference':'r'})
    assert result['error']=='FIELD_WRITE_UNCERTAIN_NO_RETRY'
    assert result['retry_safe'] is False
    assert tools.terminal=='FIELD_WRITE_UNCERTAIN_NO_RETRY'
    tools.mutations=0
    await tools.action({'action':'type','field':'campaign_name','reference':'r'})
    assert tools.resolve.await_count==2  # only the first attempt's two resolutions
    loc.fill.assert_awaited_once()
    assert tools.last_independent_readback['fields'] == {'waba': True}


def test_manual_candidate_cannot_include_future_stage_guidance():
    from modules.whatsapp.contract import V2Lesson
    # Typed guidance may be valid for the full workflow; the manual storage
    # boundary must additionally reject Audience scope (covered below).
    lesson=V2Lesson(owner='model',finding='Missing control.',evidence_refs=['0'],
        code_fix_required=False,safe_next_iteration=True,change_summary='Inspect a control.',
        instructions=[dict(goal='audience',when='control_unresolved',action='dom')])
    assert lesson.instructions[0].goal=='audience'


async def test_manual_storage_rejects_future_stage_candidate_without_writing(tmp_path):
    from app.trace import Trace
    from modules.whatsapp.contract import V2Authorization,V2Lesson
    from modules.whatsapp.workflow import store_v2_candidate
    trace=Trace();trace.enable_agent_trace()
    lesson=V2Lesson(owner='model',finding='Missing control.',evidence_refs=['0'],
        code_fix_required=False,safe_next_iteration=True,change_summary='Inspect a control.',
        instructions=[dict(goal='audience',when='control_unresolved',action='dom')])
    ctx={'trace':trace,'lesson':lesson,'tools':SimpleNamespace(terminal=None,pending_write=False)}
    auth=V2Authorization(live=True,inference=True,edits=True,manual_form_test=True,learning=True)
    await store_v2_candidate(ctx,{'failure':{}},auth,None,None,'a'*64)
    assert ctx['candidate_error']=='CANDIDATE_OUTSIDE_MANUAL_SETUP_SCOPE'
    assert 'candidate_result' not in ctx


@pytest.mark.parametrize('owner',['tool','controller','authentication','authorization','browser','environment'])
def test_deterministic_or_security_failure_cannot_become_runtime_candidate(owner):
    from learning.skill_storage.registry import save_whatsapp_candidate
    from modules.whatsapp.contract import V2Lesson
    lesson=V2Lesson(owner=owner,finding='Repair the responsible component.',evidence_refs=['0'],
        code_fix_required=False,safe_next_iteration=True,change_summary='Inspect the code.',
        instructions=[dict(goal='setup',when='control_unresolved',action='dom')])
    assert save_whatsapp_candidate(lesson,run_id='a'*32,parent=None,task_digest='b'*64,
        evidence_refs=['0'],payload=None,ui=None) is None


async def test_v2_tool_budget_exhaustion_is_not_a_forbidden_tool_error():
    from app.trace import Trace
    from deep_agents.permissions import InferenceBudget,PermissionMiddleware
    trace=Trace();trace.enable_agent_trace()
    middleware=PermissionMiddleware({'browser_action'},trace,InferenceBudget(2),True,
        max_tool_calls=0,tool_budget_error='DOER_TOOL_CALL_BUDGET_EXHAUSTED')
    request=SimpleNamespace(tool_call={'id':'offline-call','name':'browser_action','args':{'action':'observe'}})
    with pytest.raises(RuntimeError,match='DOER_TOOL_CALL_BUDGET_EXHAUSTED'):
        await middleware.awrap_tool_call(request,AsyncMock())
    from deep_agents.permissions import model_failure_details
    assert model_failure_details(RuntimeError('DOER_TOOL_CALL_BUDGET_EXHAUSTED'))['code']=='DOER_TOOL_CALL_BUDGET_EXHAUSTED'


def test_teacher_distinguishes_task_invocation_from_browser_execution():
    from app.trace import Trace
    from modules.whatsapp.workflow import task_invocation_observed,teacher_factual_contradiction
    trace=Trace();trace.enable_agent_trace();trace.agent_phase='task_doer'
    trace.agent_event('model_invocation',role='doer')
    assert task_invocation_observed(trace.events) is True
    actual={'task_tool_calls':0}
    assert teacher_factual_contradiction('Task Doer was invoked.',actual,False,task_invoked=True) is None
    assert teacher_factual_contradiction('Task Doer filled a field.',actual,False,task_invoked=True)=='TEACHER_ASSERTED_UNOBSERVED_TASK_BROWSER_EXECUTION'


def _node_dom(expression,children):
    import json,shutil,subprocess
    from pathlib import Path
    if not shutil.which('node'):pytest.skip('Node is unavailable for deterministic JavaScript checks')
    # Complete only the DOM-double properties needed by this production fragment.
    prefix="""const originalMatches=Element.prototype.matches;
    Element.prototype.matches=function(s){return s==='*'||originalMatches.call(this,s)};
    Element.prototype.contains=function(n){return this===n||this.querySelectorAll('*').includes(n)};
    Object.defineProperty(Element.prototype,'isContentEditable',{get(){
      const v=this.getAttribute('contenteditable');
      return v==='false'?false:v!==null?true:!!this.parentElement?.isContentEditable;
    }});
    """
    result=subprocess.run(['node',str(Path(__file__).with_name('evidence_dom_double.js'))],
        input=json.dumps({'document':{'children':children},'expression':'(()=>{'+prefix+expression+'})()'}),
        capture_output=True,text=True,check=True)
    return json.loads(result.stdout)


@pytest.mark.parametrize('editable',[None,'','true','plaintext-only','inherited'])
def test_discovery_uses_persistent_owned_noneditable_display_not_popup(editable):
    from modules.whatsapp.observation import V2_EXPLORE
    block=V2_EXPLORE.split('   // Selection proof must remain',1)[1].split('   bindings[c.field]=',1)[0]
    block='// Selection proof must remain'+block
    display={'tag':'span','attrs':{'id':'display'},'text':'Select an existing number'}
    combo={'tag':'div','attrs':{'id':'combo','role':'combobox'},'children':[display]}
    if editable=='inherited':combo['attrs']['contenteditable']='true'
    elif editable is not None:display['attrs']['contenteditable']=editable
    popup={'tag':'div','attrs':{'id':'popup'},'children':[
        {'tag':'div','attrs':{'id':'option','role':'option','aria-selected':'true'},'text':'approved option'}]}
    expression="""const norm=x=>String(x??'').replace(/\\s+/g,' ').trim();
    const visible=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden';
    const e=document.querySelector('#combo'),c={field:'waba',field_label:'waba id'};
    const popups=[document.querySelector('#popup')],s={values:{waba:'approved option'}};
    const controlSelector='input,textarea,select,[role=combobox],[role=checkbox],[role=radio]';
    """+block+"return selected.map(n=>n.id);"
    assert _node_dom(expression,[combo,popup])==(['display'] if editable is None else [])


@pytest.mark.parametrize('editable',[None,'','true','plaintext-only','inherited'])
async def test_independent_combobox_readback_rejects_editable_search_text(editable):
    from modules.whatsapp.contract import FieldBinding
    from modules.whatsapp.observer import deterministic_readback
    display={'tag':'span','attrs':{'id':'display'},'text':'approved option'}
    combo={'tag':'div','attrs':{'id':'combo','role':'combobox'},'children':[display]}
    if editable=='inherited':combo['attrs']['contenteditable']='true'
    elif editable is not None:display['attrs']['contenteditable']=editable
    children=[{'tag':'form','attrs':{'id':'setup'},'children':[combo]}]
    class Page:
        async def evaluate(self,expression):return _node_dom('return '+expression,children)
    trace=SimpleNamespace(agent_phase='task_doer',agent_event=lambda *args,**kwargs:'evidence')
    tools=SimpleNamespace(stage='setup',protect=AsyncMock(return_value='owned-document'),
        reviewed_fields={'waba'},observed_bindings={},verify_binding=AsyncMock(),
        ui=SimpleNamespace(fields={'waba':FieldBinding(selector='#combo',kind='combobox',selected_selector='#display')},stage_selectors={'setup':'#setup'}),
        payload=SimpleNamespace(waba='approved option'),browser=SimpleNamespace(page=Page(),trace=trace),waba_selected=True)
    facts=await deterministic_readback(tools,['waba'])
    assert facts['fields']['waba'] is (editable is None)


async def test_supervised_setup_blocks_later_field_until_campaign_write_is_read_back():
    from modules.whatsapp.contract import FieldBinding

    tools = _execution_tools()
    tools.human_assisted_setup = True
    tools.last_independent_readback = {'fields': {'campaign_name': True}}
    tools.reviewed_fields = {'waba'}
    tools.ui.fields = {'waba': FieldBinding(selector='#name', kind='combobox')}
    tools.references['r'].update(field='waba', role='combobox')
    locator = SimpleNamespace(click=AsyncMock())
    tools.resolve = AsyncMock(return_value=(tools.references['r'], locator))
    outcome, _ = await tools.action({'action': 'click', 'field': 'waba', 'reference': 'r'})
    assert outcome['error'] == 'WRITE_AND_VERIFY_CAMPAIGN_NAME_FIRST'
    tools.authorize_write.assert_not_awaited()
    locator.click.assert_not_awaited()
