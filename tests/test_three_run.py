from pathlib import Path
import json
import os
from types import SimpleNamespace
from uuid import uuid4
import pytest
import yaml
from app.config import Config,ConfigurationError
from app.state import Result,Observation
from app.trace import Trace
from deep_agents.permissions import InferenceBudget
from deep_agents.controller import review_completed
from deep_agents.offline import SyntheticChatModel
from learning.three_run import run_three,safety_stop,quality
from learning.skill_storage.registry import SkillStore
from learning.skill_storage.procedure import partial_candidate


def record(strategy,scenario='valid',review=True):
    correct=scenario!='aria_variant' or 'prefer_semantic_roles' in strategy.rules
    expected=__import__('learning.autonomous',fromlist=['EXPECTED']).EXPECTED[scenario]
    verification=str(expected) if correct else str(Result.HUMAN_ACTION_REQUIRED)
    success=verification==Result.AUTHENTICATED_AND_VERIFIED
    events=[{'action_category':'observe','observation':Observation(expected_host=success,dashboard=success,tenant_matches=success,readonly_access=success,evidence_configured=success).model_dump(),
        'step_result':verification}]
    facts=[{'kind':kind,'event_index':0} for kind in (('LOGIN_RECOGNIZED','FIELD_ROLES_VALIDATED','SUBMITTED_ONCE','DESTINATION_VERIFIED') if success else ())]
    proposal={'outcome':'success' if success else 'blocked','change_required':not correct,
        'finding':'Verified execution.' if success else 'Semantic roles were not recognized.',
        'proposed_change':'' if correct else 'Prefer accessible roles.', 'strategy_rule':None if correct else 'prefer_semantic_roles','evidence_events':[0]}
    return {'run_id':uuid4().hex,'source':'synthetic','scenario':scenario,'verification':verification,'reason_code':'VERIFIED_SUCCESS' if success else 'HUMAN_ACTION_REQUIRED',
        'step_facts':facts,'strategy_version':strategy.version,'role_evidence':{},'teacher_status':'ANALYZED' if review else 'NOT_RUN',
        'teacher_error':None,'teacher_proposal':proposal if review else None,'cleanup_result':'CLOSED_AND_PROFILE_REMOVED',
        'model_requests':7 if review else 6,'actual_api_requests':0,'role_usage':{'doer':{'requests':6},'teacher':{'requests':1 if review else 0}},
        'token_usage':{'input_tokens':0,'output_tokens':0,'total_tokens':0},'token_usage_available':False,
        'metrics':{'browser_actions':10,'duration_seconds':1,'doer_duration_seconds':.8,'inspections':2},
        'rediscovery_used':False,'login_submissions':1 if success else 0,'submission_state':'COMPLETED' if success else 'NOT_ATTEMPTED',
        'mode':'offline','mocked':True,'error':None,'_events':events}


@pytest.fixture
def fake_execution(monkeypatch):
    calls=[]
    async def execute(config,task=None,scenario='valid',review=False,strategy=None,skill_store=None,candidate_version=None,**kwargs):
        r=record(strategy,scenario,review)
        if candidate_version:r.update(skill_loaded=True,native_skill_metadata_loaded=True)
        calls.append({'review':review,'strategy':strategy,'result':r,'history':kwargs.get('history')})
        return r
    monkeypatch.setattr('deep_agents.controller.run_synthetic',execute)
    return calls

async def test_three_successes_keep_running_with_separate_teacher_and_no_change(tmp_path,fake_execution):
    cycle=await run_three(Config('unused'),store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(cycle['iterations'])==3
    main=[c for c in fake_execution if c['review']]
    assert len(main)==3 and all(r['assessment']['decision']=='NO_CHANGE' for r in cycle['iterations'])
    assert main[1]['history'][0]['run_id']==cycle['iterations'][0]['run_id']
    assert main[2]['history'][1]['strategy_assessment']['decision']=='NO_CHANGE'
    assert len({r['run_id'] for r in cycle['iterations']})==3
    assert cycle['graduation']['state']=='GRADUATION_READY' and not cycle['graduation']['live_certified']
    path=Path(cycle['candidate_skill']['path'])
    front=yaml.safe_load((path/'SKILL.md').read_text().split('---')[1])
    assert set(front)=={'name','description'}
    assert '1. Application policy:' in (path/'SKILL.md').read_text()
    assert 'FIELD_ROLES_VALIDATED' in json.dumps(json.loads((path/'provenance.json').read_text()))
    store=SkillStore(tmp_path/'skills')
    with pytest.raises(PermissionError):store.promote(cycle['candidate_skill']['version'],cycle['candidate_skill']['digest'])
    assert store.snapshot() is None

async def test_revised_strategy_reaches_next_fresh_doers(tmp_path,fake_execution):
    cycle=await run_three(Config('unused'),scenario='aria_variant',store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    main=[c for c in fake_execution if c['review']]
    assert cycle['iterations'][0]['assessment']['decision']=='ACCEPTED'
    assert 'prefer_semantic_roles' in main[1]['strategy'].rules and 'prefer_semantic_roles' in main[2]['strategy'].rules
    assert main[1]['strategy'].version==cycle['iterations'][0]['assessment']['next_strategy']['version']
    assert cycle['iterations'][1]['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert cycle['graduation']['gates']['repeatability']

async def test_teacher_failure_preserves_execution_and_prevents_graduation(tmp_path,fake_execution,monkeypatch):
    original=__import__('deep_agents.controller',fromlist=['run_synthetic']).run_synthetic
    count=0
    async def execute(*args,**kwargs):
        nonlocal count
        r=await original(*args,**kwargs)
        if kwargs.get('review'):
            count+=1
            if count==2:r.update(teacher_status='ANALYSIS_UNAVAILABLE',teacher_proposal=None,teacher_error='TEACHER_ANALYSIS_UNAVAILABLE')
        return r
    monkeypatch.setattr('deep_agents.controller.run_synthetic',execute)
    cycle=await run_three(Config('unused'),store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(cycle['iterations'])==3 and cycle['iterations'][1]['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert cycle['graduation']['state']!='GRADUATION_READY'
    assert not cycle['graduation']['gates']['all_teacher_reviews']

@pytest.mark.parametrize('changes',[{'verification':str(Result.AUTH_FAILED)},{'submission_state':'UNCERTAIN'},{'error':'OPERATOR_CANCELLED'},{'cleanup_result':'CLEANUP_FAILED'}])
async def test_safety_stop_prevents_further_live_attempts(tmp_path,monkeypatch,changes):
    calls=[]
    async def execute(*args,**kwargs):
        from learning.policies import Strategy
        r=record(kwargs.get('strategy') or Strategy());r.update(source='live',**changes);calls.append(r);return r
    monkeypatch.setattr('deep_agents.controller.run_live',execute)
    cycle=await run_three(Config('unused',live_login_enabled=True,live_comparison_enabled=True),mode='live',
        authorized_live=True,authorized_live_comparison=True,total_api_budget=24,store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(calls)==1 and cycle['stop_reason'] and cycle['graduation']['state']=='DRAFT'

@pytest.mark.parametrize('enabled,auth,comparison,total',[(False,True,True,24),(True,False,True,24),(True,True,False,24),(True,True,True,0)])
async def test_live_cycle_gates_before_any_session(enabled,auth,comparison,total,tmp_path,monkeypatch):
    async def forbidden(*args,**kwargs):raise AssertionError('Must not launch')
    monkeypatch.setattr('deep_agents.controller.run_live',forbidden)
    with pytest.raises(ConfigurationError):
        await run_three(Config('unused',live_login_enabled=True,live_comparison_enabled=enabled),mode='live',
            authorized_live=auth,authorized_live_comparison=comparison,total_api_budget=total,root=tmp_path)

async def test_exactly_three_required(tmp_path):
    with pytest.raises(ConfigurationError):await run_three(Config('unused'),learning_runs=2,root=tmp_path)

def test_teacher_budget_is_separate_and_cannot_be_spent_by_doer():
    budget=InferenceBudget(8,role_limits={'doer':6,'teacher':2})
    for _ in range(6):budget.claim('doer')
    with pytest.raises(RuntimeError):budget.claim('doer')
    budget.claim('teacher');budget.claim('teacher')
    with pytest.raises(RuntimeError):budget.claim('teacher')
    assert budget.requests==8

async def test_teacher_failure_is_categorized_without_exception_values():
    class Broken(SyntheticChatModel):
        def _generate(self,*a,**k):raise ValueError('SYNTHETIC-PRIVATE-MARKER')
    result={'verification':str(Result.AUTHENTICATED_AND_VERIFIED),'teacher_proposal':None}
    trace=Trace()
    await review_completed(result,trace,Broken(role='teacher'),InferenceBudget(2),True)
    assert result['teacher_diagnostic']['stage']=='MODEL_EXECUTION'
    assert result['teacher_diagnostic']['category']=='EXECUTION_FAILURE'
    assert 'SYNTHETIC-PRIVATE-MARKER' not in json.dumps(result)+json.dumps(trace.summary())

def test_partial_live_draft_never_claims_destination_success(tmp_path):
    from learning.policies import Strategy
    r=record(Strategy());r.update(source='live',verification=str(Result.UNEXPECTED_PAGE),missing_evidence=['dashboard','tenant_matches','readonly_access','evidence_configured'])
    r['step_facts']=[f for f in r['step_facts'] if f['kind']!='DESTINATION_VERIFIED']
    store=SkillStore(tmp_path)
    artifact=partial_candidate(r,r['_events'],store)
    text=(Path(artifact['path'])/'SKILL.md').read_text()
    assert 'Unresolved: authenticated destination' in text and artifact['state']=='DRAFT'
    with pytest.raises(PermissionError):store.promote(artifact['version'],artifact['digest'],True)

async def test_evidence_preview_requires_exact_explicit_approval_and_no_resubmit(tmp_path):
    from app.config import OriginPolicy
    from app.safety import authorize_live
    from browser.stagehand_client import StagehandClient
    from browser.evidence_review import EvidenceReview
    from modules.login.evidence import observe_live
    from pydantic import SecretStr
    file=tmp_path/'review.json'
    file.write_text(json.dumps({'reviewed':False,'dashboard_selector':'main','tenant_selector':'span','readonly_path':'/confirmed-context','tenant_json_key':'tenant','authenticated_json_key':'authenticated'}))
    config=Config('unused',live_login_enabled=True,evidence_file=str(file))
    permit=authorize_live(config,True,8);permit.started=True
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.expected_account=SecretStr('synthetic-private-account')
    class Page:
        async def url(self):return 'https://tprappin2r.netcoresmartech.com/'
        async def evaluate(self,expression):
            if 'const alerts' in expression:return {'credential_form':False,'challenge':False,'rejected':False,'expired':False,'account_restricted':False}
            return {'dashboard':True,'tenant_matches':True,'readonly_access':True,'evidence_configured':True,'mismatch':False}
    browser.page=Page()
    async def observe():return await observe_live(browser,await browser.page.url())
    browser.observe=observe
    review=EvidenceReview(browser)
    preview=await review.preview()
    assert not preview['trusted'] and browser.trace.verification_snapshot is None and browser.evidence is None
    with pytest.raises(PermissionError):await review.approve(preview['digest'])
    observed=await review.approve(preview['digest'],True)
    assert observed.readonly_access and browser.trace.verification_snapshot and browser.login_submissions==0
    assert json.loads(file.read_text())['reviewed'] and 'synthetic-private-account' not in json.dumps(browser.trace.summary())

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Headed loopback-only three-run integration')
@pytest.mark.parametrize('scenario',['valid','aria_variant'])
async def test_actual_three_run_deep_agents_chromium_graduation(tmp_path,scenario):
    config=Config(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    cycle=await run_three(config,scenario=scenario,store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(cycle['iterations'])==3 and cycle['actual_api_requests']==0
    assert all(r['teacher_status']=='ANALYZED' for r in cycle['iterations'])
    assert cycle['iterations'][-1]['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert cycle['graduation']['state']=='GRADUATION_READY' and not cycle['graduation']['live_certified']
    assert cycle['graduation']['gates']['native_skill_loading'] and cycle['graduation']['gates']['repeatability']
    assert all(r['login_submissions']<=1 for r in cycle['iterations'])
    assert all(not any(e.get('stop_code')=='INSPECT_BEFORE_NAVIGATION' for e in json.loads((__import__('app.config',fromlist=['ROOT']).ROOT/'outputs'/f"{r['run_id']}.json").read_text())) for r in cycle['iterations'])
    if scenario=='aria_variant':assert cycle['iterations'][0]['assessment']['decision']=='ACCEPTED'
    else:assert all(not r['teacher_proposal']['change_required'] for r in cycle['iterations'])

async def test_authorized_mock_live_comparison_continues_after_success(tmp_path,fake_execution,monkeypatch):
    calls=[]
    async def live(*args,**kwargs):
        r=record(kwargs['strategy']);r['source']='live';calls.append(r);return r
    monkeypatch.setattr('deep_agents.controller.run_live',live)
    cycle=await run_three(Config('unused',live_login_enabled=True,live_comparison_enabled=True),mode='live',
        authorized_live=True,authorized_live_comparison=True,total_api_budget=24,store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(calls)==3 and all(r['login_submissions']==1 for r in calls)
    assert len({r['run_id'] for r in calls})==3

async def test_final_instability_prevents_graduation(tmp_path,fake_execution,monkeypatch):
    original=__import__('deep_agents.controller',fromlist=['run_synthetic']).run_synthetic
    count=0
    async def execute(*args,**kwargs):
        nonlocal count
        r=await original(*args,**kwargs)
        if kwargs.get('review'):
            count+=1
            if count==3:r['verification']=str(Result.UNEXPECTED_PAGE)
        return r
    monkeypatch.setattr('deep_agents.controller.run_synthetic',execute)
    cycle=await run_three(Config('unused'),store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert not cycle['graduation']['gates']['repeatability'] and cycle['graduation']['state']!='GRADUATION_READY'

def test_faster_but_wrong_or_more_expensive_proposal_is_rejected():
    baseline=[{'scenario':'valid','correct':True,'requests':6,'actions':10}]
    assert not quality([{'scenario':'valid','correct':False,'requests':1,'actions':1}],baseline)['accepted']
    assert not quality([{'scenario':'valid','correct':True,'requests':6,'actions':11}],baseline)['accepted']

def test_categorical_teacher_validation_budget_and_finish_failures():
    from deep_agents.diagnostics import teacher_failure
    from pydantic import ValidationError
    trace=Trace()
    assert teacher_failure(RuntimeError('MODEL_BUDGET_EXHAUSTED'),'MODEL_EXECUTION',trace)['category']=='REQUEST_BUDGET_EXHAUSTED'
    try:Observation.model_validate({'allowed':'private-invalid-value'})
    except ValidationError as error:
        diagnostic=teacher_failure(error,'MODEL_EXECUTION',trace)
    assert diagnostic['category']=='STRUCTURED_OUTPUT_VALIDATION' and 'private-invalid-value' not in json.dumps(diagnostic)
    trace.model_diagnostics=[{'role':'teacher','status':'RETURNED','finish_status':'length'}]
    assert teacher_failure(ValueError(),'PROPOSAL_VALIDATION',trace)['category']=='PROPOSAL_VALIDATION'
    assert teacher_failure(ValueError(),'MODEL_EXECUTION',trace)['category']=='OUTPUT_TOKEN_LIMIT'

async def test_safety_failure_during_proposal_evaluation_stops_cycle(tmp_path,fake_execution,monkeypatch):
    original=__import__('deep_agents.controller',fromlist=['run_synthetic']).run_synthetic
    evaluations=[]
    async def execute(*args,**kwargs):
        r=await original(*args,**kwargs)
        if not kwargs.get('review'):
            evaluations.append(r)
            r['error']='OPERATOR_CANCELLED'
        return r
    monkeypatch.setattr('deep_agents.controller.run_synthetic',execute)
    cycle=await run_three(Config('unused'),scenario='aria_variant',store=SkillStore(tmp_path/'skills'),root=tmp_path/'learning')
    assert len(cycle['iterations'])==1 and len(evaluations)==1
    assert cycle['stop_reason']=='EVALUATION_EXECUTION_OR_CLEANUP_STOP'
    assert cycle['graduation']['state']=='DRAFT'
