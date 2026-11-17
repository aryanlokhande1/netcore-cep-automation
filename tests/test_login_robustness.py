"""Controlled offline inputs only; no browser, credentials, HTTP or inference."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import Config, OriginPolicy
from app.trace import Trace
from app.state import Observation, Result
from browser.stagehand_client import StagehandClient
from deep_agents.evidence import facts_only


def browser():
    b = StagehandClient(Config('unused', timeout_seconds=10),
        OriginPolicy({'https://tprappin2r.netcoresmartech.com'}), Trace())
    b.guard = SimpleNamespace(failed=False, blocking_hosts=set(),
        primary_observable=lambda: True)
    b.config = SimpleNamespace(**{**b.config.__dict__, 'login_poll_seconds': .005,
        'login_stable_seconds': .01, 'login_ready_seconds': .15,
        'login_ready_max_seconds': .22})
    return b


async def test_loading_waits_locally_without_marking_authentication_failed():
    b = browser()
    b.readiness_sample = AsyncMock(side_effect=[
        {'context': 'doc', 'expected_host': True, 'loading': True},
        *[{'context': 'doc', 'expected_host': True, 'shell': True,
           'loading': False} for _ in range(30)]])
    result = await b.wait_for_application_ready()
    assert result['state'] == 'APPLICATION_READY_AUTH_UNVERIFIED'
    assert b.readiness_sample.await_count >= 3
    assert b.trace.openai_api_calls == 0


async def test_loading_timeout_is_not_unexpected_page_or_auth_failed():
    b = browser()
    b.readiness_sample = AsyncMock(return_value={
        'context': 'doc', 'expected_host': True, 'loading': True})
    result = await b.wait_for_application_ready()
    assert result['code'] == 'POST_LOGIN_READINESS_TIMEOUT'
    assert result['state'] == 'APPLICATION_LOADING'
    assert result['deadline_extended'] is False


@pytest.mark.parametrize('flag,state',[
    ('rejected','AUTH_FAILED'),('account_restricted','AUTH_FAILED'),
    ('expired','SESSION_EXPIRED'),('challenge','HUMAN_ACTION_REQUIRED')])
async def test_security_negative_precedes_ready_shell(flag,state):
    b = browser()
    b.readiness_sample = AsyncMock(return_value={
        'context':'doc','expected_host':True,'shell':True,flag:True})
    assert (await b.wait_for_application_ready())['state'] == state
    assert b.readiness_sample.await_count == 1


def test_scope_counters_survive_teacher_fact_reduction():
    source={'state':'LOGIN_SCOPE_UNAVAILABLE','inaccessible_shadow_roots':1,
            'inventory_changed':True,'inventory_open_shadow_roots':14,
            'user_agent_shadow_roots':8,'negative_scope_complete':True}
    assert facts_only(source) == source


async def test_authenticated_observations_need_two_matching_contexts():
    from modules.login.evidence import stable_authentication
    b = browser()
    b.authentication_context = AsyncMock(side_effect=['old','new','new'])
    passed=Observation(expected_host=True,dashboard=True,tenant_matches=True,
                       readonly_access=True,evidence_configured=True)
    b.observe = AsyncMock(return_value=passed)
    result = await stable_authentication(b)
    assert result.readonly_access is False


async def test_valid_authentication_keeps_five_predicates():
    from modules.login.evidence import stable_authentication
    from modules.login.verifier import verify
    b=browser();b.authentication_context=AsyncMock(return_value='current')
    b.observe=AsyncMock(return_value=Observation(expected_host=True,dashboard=True,
        tenant_matches=True,readonly_access=True,evidence_configured=True))
    assert verify(await stable_authentication(b)) == Result.AUTHENTICATED_AND_VERIFIED
    assert b.observe.await_count == 2


@pytest.mark.parametrize('claim',[
    'Authentication occurred.', 'Login was authenticated.',
    'The session is authenticated.', 'WhatsApp Doer executed.'])
def test_teacher_cannot_assert_unverified_execution(claim):
    from modules.whatsapp.workflow import teacher_factual_contradiction
    actual={'login_tool_calls':5,'task_tool_calls':0}
    assert teacher_factual_contradiction(claim,actual,False) is not None


def test_teacher_may_describe_submission_without_authentication_claim():
    from modules.whatsapp.workflow import teacher_factual_contradiction
    assert teacher_factual_contradiction(
        'One submission occurred; authentication was not verified.',
        {'login_tool_calls':5,'task_tool_calls':0},False) is None


async def test_explicit_reviewed_nonboolean_resource_rule(tmp_path):
    import hashlib,json
    from test_evidence_discovery_repair import setup_discovery, network, captured, config, ACCOUNT
    b,d=setup_discovery(tmp_path)
    d.channel.send.return_value={'body':json.dumps({'session':{'status':'active','tenant':ACCOUNT}})}
    network(d);await captured(d)
    c=config().model_dump();c.update(authenticated_json_key='/session/status',
        tenant_json_key='/session/tenant',authenticated_value_sha256=hashlib.sha256(b'"active"').hexdigest(),
        authentication_contract_reference='CONTROLLED_OFFLINE_REVIEW_ONLY')
    from modules.login.evidence import EvidenceConfig
    c=EvidenceConfig.model_validate(c)
    assert d.check_resource(c)[0]
    c.authenticated_value_sha256=hashlib.sha256(b'"inactive"').hexdigest()
    assert not d.check_resource(c)[0]


@pytest.mark.parametrize('late_progress,expected_deadline',[(False,30),(True,45)])
async def test_readiness_deadline_extends_only_for_new_relevant_progress(monkeypatch,late_progress,expected_deadline):
    import browser.stagehand_client as module
    clock=[0.];original_sleep=asyncio.sleep
    monkeypatch.setattr(module,'monotonic',lambda:clock[0])
    async def advance(seconds):
        clock[0]+=seconds
        await original_sleep(0)
    monkeypatch.setattr(module.asyncio,'sleep',advance)
    b=browser();b.started=0;b.config=Config('unused',timeout_seconds=90)
    async def sample():
        return {'context':'new' if late_progress and clock[0]>=29 else 'doc',
                'expected_host':True,'loading':True}
    b.readiness_sample=sample
    result=await b.wait_for_application_ready()
    assert clock[0]==expected_deadline
    assert result['deadline_extended'] is late_progress
    assert result['code']=='POST_LOGIN_READINESS_TIMEOUT'


async def test_readiness_does_not_exceed_enclosing_deadline(monkeypatch):
    import browser.stagehand_client as module
    clock=[0.];original_sleep=asyncio.sleep
    monkeypatch.setattr(module,'monotonic',lambda:clock[0])
    async def advance(seconds):clock[0]+=seconds;await original_sleep(0)
    monkeypatch.setattr(module.asyncio,'sleep',advance)
    b=browser();b.started=0;b.config=Config('unused',timeout_seconds=3)
    b.readiness_sample=AsyncMock(return_value={'context':'doc','expected_host':True,'loading':True})
    assert (await b.wait_for_application_ready())['code']=='POST_LOGIN_READINESS_TIMEOUT'
    assert clock[0]==3


@pytest.mark.parametrize('closed,relevant_changed,expected',[(0,False,True),(1,False,False),(0,True,False)])
async def test_scope_change_requires_stable_relevant_evidence_and_no_closed_gap(monkeypatch,closed,relevant_changed,expected):
    from contextlib import asynccontextmanager
    import browser.metadata_channel as channel
    import browser.shadow_inventory as inventory
    from browser.discovery import certify_custom_host_scope
    @asynccontextmanager
    async def metadata(*args):yield object()
    async def collect(*args,observe,**kwargs):
        await observe()
        return {'inventory_changed':True,'inventory_complete':True,'inventory_passes':2,
            'inventory_open_shadow_roots':2,'inaccessible_shadow_roots':closed,'unknown_shadow_roots':0}
    monkeypatch.setattr(channel,'metadata_channel',metadata)
    monkeypatch.setattr(inventory,'shadow_inventory',collect)
    b=browser();b.budget=lambda:None
    owner=SimpleNamespace(target='t',session='s',generation=1,document_generation=1,frame_id='f',loader_id='l')
    b.guard.primary_session='s';b.guard.session_states={'s':owner}
    first={'dashboard':True,'tenant_matches':True,'mismatch':False,
        '_scope_diagnostics':{'inaccessible_or_unknown_hosts':1,'open_shadow_roots_inspected':2}}
    latest={**first,'tenant_matches':not relevant_changed}
    flags={k:False for k in ['credential_form','challenge','rejected','expired','account_restricted']}
    b.page=SimpleNamespace(evaluate=AsyncMock(side_effect=[latest,{'flags':{**flags,'negative_scope_complete':True}}]))
    _,proof=await certify_custom_host_scope(b,first,'CONTROLLED_EXPRESSION')
    assert proof['certified'] is expected


def test_reduction_never_keeps_authentication_secrets():
    from browser.discovery import identity_facts
    facts=identity_facts({'token':'secret','credentials':{'status':'active'},
        'session':{'status':'active','tenant':'CONTROLLED'}},'CONTROLLED')
    assert set(facts['scalar_hashes'])=={'/session/status'}


def test_dashboard_shell_is_discoverable_without_heading():
    from test_evidence_discovery_repair import dom_expression, BASE
    from modules.login.discovery import DOM_DISCOVERY
    data={'url':BASE+'/', 'children':[{'tag':'main','attrs':{'id':'workspace'},
        'children':[{'tag':'button','text':'Menu'}]}]}
    result=dom_expression(DOM_DISCOVERY,data)
    assert result['dashboard_candidates'][0]['kind']=='APPLICATION_SHELL'


def test_actual_embedded_readiness_expression_runs_without_secret_output():
    from test_evidence_discovery_repair import dom_expression, BASE
    from modules.login.discovery import READINESS_DOM
    data={'url':BASE+'/', 'children':[{'tag':'main','attrs':{'id':'workspace'},
        'children':[{'tag':'button','text':'PRIVATE-CONTROLLED-TEXT'}]}]}
    result=dom_expression(READINESS_DOM,data)
    assert result['shell'] is True
    assert 'PRIVATE' not in str(result)


async def test_passive_resource_cannot_cross_spa_context(tmp_path):
    from test_evidence_discovery_repair import setup_discovery, network, captured, config
    b,d=setup_discovery(tmp_path);network(d);await captured(d)
    assert d.check_resource(config())[0]
    d.navigation_epoch+=1
    assert d.check_resource(config()) == (False,'SPA_CONTEXT_CHANGED')


async def test_review_rejects_document_change_after_preview(tmp_path):
    from test_evidence_discovery_repair import live_review,config
    from browser.evidence_diagnostics import ApprovalFailure
    b,review=live_review(tmp_path)
    # Explicit controlled current-session identity; never real tenant evidence.
    b.authentication_context=AsyncMock(return_value='CONTROLLED_DOCUMENT_1')
    review.path.write_text(config().model_dump_json())
    preview=await review.preview()
    b.authentication_context.return_value='CONTROLLED_DOCUMENT_2'
    with pytest.raises(ApprovalFailure,match='APPROVAL_CONTEXT_CHANGED'):
        await review.approve(preview['digest'],True)


async def test_review_approval_keeps_exact_candidate_bytes_until_explicit_consent(tmp_path):
    import hashlib,json
    from test_evidence_discovery_repair import live_review,config
    b,review=live_review(tmp_path)
    b.authentication_context=AsyncMock(return_value='CONTROLLED_DOCUMENT')
    # The controlled observer supplies five independently-checkable test flags.
    b.observe=AsyncMock(return_value=Observation(expected_host=True,dashboard=True,
        tenant_matches=True,readonly_access=True,evidence_configured=True))
    review.path.write_text(config().model_dump_json());original=review.path.read_bytes()
    preview=await review.preview()
    assert preview['digest']==hashlib.sha256(original).hexdigest()
    assert review.path.read_bytes()==original
    assert not any(e.get('diagnostic',{}).get('state')=='AUTHENTICATED_AND_VERIFIED' for e in b.trace.events)
    await review.approve(preview['digest'],True)
    assert json.loads(review.path.read_text())['reviewed'] is True


@pytest.mark.parametrize('passed',[False,True])
async def test_actual_v2_authentication_gate_constructs_task_tools_only_after_verification(passed):
    """Execute actual start-node gate AST in isolation; no workflow/browser run."""
    import ast
    from pathlib import Path
    from modules.whatsapp.contract import V2Stop
    tree=ast.parse(Path('modules/whatsapp/workflow.py').read_text(encoding='utf-8-sig'))
    start=next(n for n in ast.walk(tree) if isinstance(n,ast.AsyncFunctionDef) and n.name=='start')
    block=next(n for n in start.body if isinstance(n,ast.Try)).body
    first=next(i for i,n in enumerate(block) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='authenticated_observation' for t in n.targets))
    last=next(i for i,n in enumerate(block) if isinstance(n,ast.Expr) and isinstance(n.value,ast.Call) and any(k.arg=='session_reused' for k in n.value.keywords))
    func=ast.AsyncFunctionDef(name='gate',args=ast.arguments(posonlyargs=[],args=[],kwonlyargs=[],kw_defaults=[],defaults=[]),
        body=block[first:last+1],decorator_list=[])
    module=ast.fix_missing_locations(ast.Module(body=[func],type_ignores=[]))
    b=browser();b.authentication_context=AsyncMock(return_value='CONTROLLED_CURRENT');b.page=object()
    b.observe=AsyncMock(return_value=Observation(expected_host=True,dashboard=passed,
        tenant_matches=passed,readonly_access=passed,evidence_configured=passed))
    constructed=[];ctx={'authentication_verified':False};result={}
    namespace={'browser':b,'ctx':ctx,'result':result,'trace':b.trace,
        'V2BrowserTools':lambda *args:constructed.append(args) or object(),
        'payload':object(),'ui':object(),'authorization':object(),'directory':None,
        'Result':Result,'V2Stop':V2Stop}
    from modules.login.evidence import stable_authentication
    namespace['stable_authentication']=stable_authentication
    from modules.login.verifier import verify
    namespace['verify']=verify
    exec(compile(module,'ACTUAL_V2_GATE_CONTROLLED_OFFLINE','exec'),namespace)
    if passed:
        await namespace['gate']()
        assert ctx['authentication_verified'] and constructed[0][0] is b
        assert b.login_page is b.page
    else:
        with pytest.raises(V2Stop):await namespace['gate']()
        assert not constructed and not ctx['authentication_verified']


def test_one_submission_latch_is_preserved_without_executing_credentials():
    b=StagehandClient(Config('unused'),OriginPolicy({'http://127.0.0.1:9000'}),Trace(),fixture=True)
    b.claim_login_submission()
    with pytest.raises(PermissionError,match='LOGIN_SUBMISSION_LIMIT'):b.claim_login_submission()
    assert b.login_submissions==1


def test_actual_saved_teacher_authentication_claim_is_rejected():
    import json
    from pathlib import Path
    from modules.whatsapp.workflow import teacher_factual_contradiction
    directory=Path('outputs/whatsapp-v2/72c33292808944edbffc51867bbb7bb9')
    result=json.loads((directory/'result.json').read_text())
    package=json.loads((directory/'teacher-input-evidence.json').read_text())
    lesson=result['teacher_review']
    assert teacher_factual_contradiction(lesson['finding']+' '+lesson['change_summary'],
        package['execution_facts'],result['authentication_verified']) == 'TEACHER_ASSERTED_UNVERIFIED_AUTHENTICATION'


def test_scope_and_resource_diagnostics_recompile_from_actual_source_indices():
    import hashlib,json
    from pathlib import Path
    from deep_agents.evidence import compile_evidence,source_events,digest
    from modules.login.review import LoginReviewAdapter
    directory=Path('outputs/whatsapp-v2/72c33292808944edbffc51867bbb7bb9')
    events=json.loads((directory/'72c33292808944edbffc51867bbb7bb9.json').read_text())
    summary=json.loads((directory/'72c33292808944edbffc51867bbb7bb9-summary.json').read_text())
    result={'run_id':directory.name,'verification':'UNEXPECTED_PAGE',
        'reason_code':'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED','step_facts':summary['step_facts']}
    package=compile_evidence(result,events,LoginReviewAdapter(),max_tokens=5000)
    assert sum(c.kind=='tool_result' for c in package.evidence)==5
    scope=[c for c in package.evidence if c.data.get('state')=='LOGIN_SCOPE_UNAVAILABLE']
    assert scope and scope[0].data['inaccessible_shadow_roots']==1 and scope[0].data['inventory_changed']
    for card in package.evidence:
        if card.kind=='source_digest':
            assert card.data['sha256']==digest(events[card.events[0]])


def test_access_denial_is_a_security_negative_in_the_full_scan():
    from test_evidence_discovery_repair import dom_expression,BASE
    from modules.login.negative_scan import NEGATIVE_STATES
    result=dom_expression(NEGATIVE_STATES,{'url':BASE+'/',
        'children':[{'attrs':{'role':'alert'},'text':'Access denied'}]})
    assert result['account_restricted'] is True


def test_resource_review_does_not_export_customer_keys_or_unrelated_values():
    from browser.discovery import identity_facts
    facts=identity_facts({'customers':{'PRIVATE_CUSTOMER_NAME':{'status':'active'}},
        'session':{'status':'active','message':'PRIVATE_CUSTOMER_MESSAGE'}},'CONTROLLED')
    assert set(facts['scalar_hashes'])=={'/session/status'}
