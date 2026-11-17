"""Final readiness checks. Actual OpenAI execution is never enabled by these tests."""
import json
import logging
import os
from pathlib import Path
from uuid import uuid4
import pytest
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration,ChatResult
from app.config import ROOT,Config,ConfigurationError
from app.trace import Trace
from app.state import Observation,Result
from deep_agents.controller import review_completed,run_synthetic
from deep_agents.offline import SyntheticChatModel
from deep_agents.permissions import InferenceBudget
from learning.skill_storage.registry import SkillStore,validate_skill_directory
from learning.skill_storage.procedure import partial_candidate
from learning.skill_storage.audit import audit_candidate

VERSION='b6d7d0aa842649dabc53a833ef0566fc'
DIGEST='345c3065673bcafb8d778eb728d51cd78cc2059351930356927fc10cc30cf762'

def draft(store):
    attempt={'run_id':uuid4().hex,'source':'synthetic','verification':str(Result.UNEXPECTED_PAGE),'strategy_version':'baseline',
        'step_facts':[{'kind':'LOGIN_RECOGNIZED','event_index':0}],'missing_evidence':['dashboard']}
    return partial_candidate(attempt,[],store)

def test_standard_skill_directory_has_no_native_name_warning(tmp_path,caplog):
    from deepagents.middleware.skills import _parse_skill_metadata,_validate_skill_name
    store=SkillStore(tmp_path)
    artifact=draft(store)
    directory=store.version_path('candidates',artifact['version'])
    assert directory.name=='netcore-login' and directory.parent.name==artifact['version']
    assert not (directory.parent/'SKILL.md').exists()
    metadata=validate_skill_directory(directory)
    assert metadata['name']=='netcore-login'
    assert _validate_skill_name('netcore-login',directory.name)==(True,'')
    with caplog.at_level(logging.WARNING):
        native=_parse_skill_metadata((directory/'SKILL.md').read_text(),str(directory/'SKILL.md'),directory.name)
    assert native['name']=='netcore-login' and not any('does not follow Agent Skills' in r.message for r in caplog.records)
    wrong=tmp_path/uuid4().hex;wrong.mkdir()
    (wrong/'SKILL.md').write_text((directory/'SKILL.md').read_text())
    with pytest.raises(ValueError,match='parent directory'):validate_skill_directory(wrong)

def test_legacy_layout_normalization_preserves_every_byte_and_digest(tmp_path):
    store=SkillStore(tmp_path);artifact=draft(store)
    directory=store.version_path('candidates',artifact['version']);container=directory.parent
    original={name:(directory/name).read_bytes() for name in store.bundle_names(directory)}
    # Model the former flat version bundle without changing any contents.
    for name in original:(directory/name).rename(container/name)
    directory.rmdir()
    before=store.digest(container)
    normalized=store.normalize('candidates',artifact['version'])
    assert normalized.name=='netcore-login' and store.digest(normalized)==before==artifact['digest']
    assert original=={name:(normalized/name).read_bytes() for name in original}
    assert store.snapshot(artifact['version'],evaluation=True).digest==before
    with pytest.raises(PermissionError):store.snapshot(artifact['version'])
    with pytest.raises(PermissionError):store.promote(artifact['version'],before,True)

def test_real_candidate_provenance_history_and_bundle_integrity():
    store=SkillStore();directory=store.version_path('candidates',VERSION)
    assert store.digest(directory)==DIGEST and directory.name=='netcore-login'
    audit=audit_candidate(directory)
    assert audit['status']=='PASS' and not audit['live_certified']
    assert len(audit['sources'])==3 and audit['history_references']>20
    assert {r['event_index'] for r in audit['step_references']}=={6,10,19,22}
    assert all(s['source']=='synthetic' for s in audit['sources'])
    assert store.snapshot() is None

class ProposalModel(SyntheticChatModel):
    proposal:dict
    def _generate(self,*args,**kwargs):
        self.calls+=1
        message=AIMessage(content='',tool_calls=[{'name':'TeacherProposal','args':self.proposal,'id':uuid4().hex,'type':'tool_call'}],response_metadata={'finish_reason':'tool_calls'})
        return ChatResult(generations=[ChatGeneration(message=message)])

def proposal(**changes):
    return {'outcome':'success','change_required':False,'finding':'Synthetic verification passed.',
        'proposed_change':'','evidence_events':[0],'analysis_kind':'no_change',
        'worked':'Trusted steps are supported by the fixture.','inefficiency':'No supported inefficiency.',
        'comparison':'Same deterministic fixture context.',**changes}

async def review(args,verification=Result.AUTHENTICATED_AND_VERIFIED,reason='VERIFIED_SUCCESS'):
    trace=Trace()
    trace.add('verify',Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True),verification)
    result={'verification':str(verification),'reason_code':reason,'teacher_proposal':None,'teacher_error':None}
    budget=InferenceBudget(1,role_limits={'doer':0,'teacher':1})
    model=ProposalModel(role='teacher',proposal=args)
    await review_completed(result,trace,model,budget,True)
    return result,trace,budget,model

@pytest.mark.parametrize('args,verification,reason',[
    (proposal(),Result.AUTHENTICATED_AND_VERIFIED,'VERIFIED_SUCCESS'),
    (proposal(change_required=True,proposed_change='Prefer accessible roles.',strategy_rule='prefer_semantic_roles',analysis_kind='policy'),Result.AUTHENTICATED_AND_VERIFIED,'VERIFIED_SUCCESS'),
    (proposal(outcome='failure',finding='Submission completed; independent evidence is unresolved.',analysis_kind='evidence_gap'),Result.UNEXPECTED_PAGE,'AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED')])
async def test_updated_teacher_schema_parses_through_genuine_agent(args,verification,reason):
    result,trace,budget,model=await review(args,verification,reason)
    assert result['teacher_status']=='ANALYZED' and result['teacher_proposal']['analysis_kind']==args['analysis_kind']
    assert budget.requests==model.calls==1 and trace.openai_api_calls==0

@pytest.mark.parametrize('args',[
    {'outcome':'success'},proposal(extra_unapproved_field=True),
    proposal(change_required=True,proposed_change='',strategy_rule='prefer_semantic_roles'),
    proposal(proposed_change='Unexpected change with no_change.'),
    proposal(change_required=True,proposed_change='Disable the allowlist.',strategy_rule='prefer_semantic_roles'),
    proposal(strategy_rule='unknown_policy')])
async def test_invalid_teacher_structured_output_is_bounded_and_categorized(args):
    result,trace,budget,model=await review(args)
    assert result['teacher_status']=='ANALYSIS_UNAVAILABLE' and result['teacher_proposal'] is None
    assert result['teacher_diagnostic']['category']=='STRUCTURED_OUTPUT_VALIDATION'
    assert budget.requests==model.calls==1 and trace.openai_api_calls==0

@pytest.mark.parametrize('args',[proposal(outcome='failure'),proposal(evidence_events=[999])])
async def test_well_formed_but_false_teacher_evidence_is_rejected(args):
    result,trace,budget,model=await review(args)
    assert result['teacher_diagnostic']['stage']=='PROPOSAL_VALIDATION'
    assert result['teacher_diagnostic']['category']=='PROPOSAL_VALIDATION'
    assert result['teacher_proposal'] is None and budget.requests==1

async def test_truncated_teacher_response_preserves_available_finish_status_without_retry():
    class Truncated(SyntheticChatModel):
        def _generate(self,*args,**kwargs):
            self.calls+=1
            message=AIMessage(content='',response_metadata={'finish_reason':'length','status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}})
            return ChatResult(generations=[ChatGeneration(message=message)])
    trace=Trace();trace.add('verify',Observation(),Result.UNEXPECTED_PAGE)
    result={'verification':str(Result.UNEXPECTED_PAGE),'teacher_proposal':None}
    model=Truncated(role='teacher');budget=InferenceBudget(1,role_limits={'doer':0,'teacher':1})
    await review_completed(result,trace,model,budget,True)
    assert result['teacher_status']=='ANALYSIS_UNAVAILABLE' and budget.requests==model.calls==1
    assert trace.model_diagnostics[0]['finish_status']=='length'
    assert trace.model_diagnostics[0]['incomplete_reason']=='max_output_tokens'

@pytest.mark.parametrize('authorized,budget,tokens',[(False,1,500),(True,0,500),(True,2,500),(True,1,1000)])
async def test_paid_teacher_smoke_gate_precedes_any_model_or_fixture_access(monkeypatch,authorized,budget,tokens):
    import evals.teacher_smoke as smoke
    def forbidden(*a,**k):raise AssertionError('No model, key or fixture access before approval')
    monkeypatch.setattr(smoke,'openai_model',forbidden);monkeypatch.setattr(smoke,'synthetic_fixture',forbidden)
    with pytest.raises(ConfigurationError):await smoke.run_teacher_smoke('openai',authorized,budget,tokens)

async def test_prepared_teacher_smoke_runs_offline_without_browser_or_api(monkeypatch):
    import evals.teacher_smoke as smoke
    def forbidden(*a,**k):raise AssertionError('Offline smoke cannot construct an actual model')
    monkeypatch.setattr(smoke,'openai_model',forbidden)
    result=await smoke.run_teacher_smoke()
    assert result['teacher_status']=='ANALYZED' and result['model_requests']==1
    assert result['actual_api_requests']==0 and not result['browser_launched'] and result['model_availability_lookups']==0

def test_first_session_review_needs_no_placeholder_endpoint_or_existing_file(tmp_path):
    from modules.login.evidence import EvidenceConfig
    missing=tmp_path/'not-created-until-discovery.json'
    assert EvidenceConfig.for_session(missing,review_mode=True) is None
    with pytest.raises(FileNotFoundError):EvidenceConfig.for_session(missing,review_mode=False)
    missing.write_text('{"reviewed": false}')
    assert EvidenceConfig.for_session(missing,review_mode=True) is None
    with pytest.raises(ValueError):EvidenceConfig.for_session(missing,review_mode=False)

@pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Headed local-only native skill loading')
async def test_migrated_pinned_candidate_loads_in_fresh_native_doer_without_warning(caplog):
    config=Config(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    with caplog.at_level(logging.WARNING):
        result=await run_synthetic(config,candidate_version=VERSION,_evaluation=True,review=True)
    assert result['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert result['skill_digest']==DIGEST and result['skill_loaded'] and result['native_skill_metadata_loaded']
    assert result['actual_api_requests']==0
    assert not any('does not follow Agent Skills' in r.message for r in caplog.records)

async def test_incomplete_or_changed_evidence_cannot_become_trusted(tmp_path):
    from app.config import OriginPolicy
    from app.safety import authorize_live
    from browser.stagehand_client import StagehandClient
    from browser.evidence_review import EvidenceReview
    from modules.login.evidence import observe_live
    from pydantic import SecretStr
    path=tmp_path/'operator-review.json'
    path.write_text(json.dumps({'reviewed':False,'dashboard_selector':'main','tenant_selector':'span','readonly_path':'/fixture-readonly','tenant_json_key':'tenant','authenticated_json_key':'authenticated'}))
    config=Config('unused',live_login_enabled=True,evidence_file=str(path))
    permit=authorize_live(config,True,8);permit.started=True
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.expected_account=SecretStr('SYNTHETIC-PRIVATE-MARKER')
    class Page:
        good=False
        rejected=False
        async def url(self):return 'https://tprappin2r.netcoresmartech.com/'
        async def evaluate(self,expression):
            if 'const alerts' in expression:return {'credential_form':False,'challenge':False,'rejected':self.rejected,'expired':False,'account_restricted':False}
            return {'dashboard':self.good,'tenant_matches':self.good,'readonly_access':self.good,'evidence_configured':True,'mismatch':False}
    browser.page=Page()
    async def observe():return await observe_live(browser,await browser.page.url())
    browser.observe=observe
    review=EvidenceReview(browser)
    first=await review.preview()
    with pytest.raises(PermissionError,match='INDEPENDENT_EVIDENCE_INCOMPLETE'):await review.approve(first['digest'],True)
    assert not json.loads(path.read_text())['reviewed'] and browser.trace.verification_snapshot is None
    browser.page.good=True
    browser.page.rejected=True
    misleading=await review.preview()
    assert all(misleading['gates'].values()) and misleading['verification']==str(Result.AUTH_FAILED)
    with pytest.raises(PermissionError,match='INDEPENDENT_EVIDENCE_INCOMPLETE'):await review.approve(misleading['digest'],True)
    assert not json.loads(path.read_text())['reviewed']
    browser.page.rejected=False
    second=await review.preview()
    data=json.loads(path.read_text());data['dashboard_selector']='section';path.write_text(json.dumps(data))
    with pytest.raises(PermissionError):await review.approve(second['digest'],True)
    assert not json.loads(path.read_text())['reviewed'] and browser.evidence is None
    third=await review.preview()
    observed=await review.approve(third['digest'],True)
    assert observed.readonly_access and browser.trace.verification_snapshot
    assert browser.login_submissions==0 and 'SYNTHETIC-PRIVATE-MARKER' not in json.dumps(browser.trace.summary())

async def test_operator_review_deadline_also_bounds_a_stalled_readonly_probe(monkeypatch):
    import asyncio
    import io
    import time
    import browser.evidence_review as module
    class StalledReview:
        def __init__(self,browser):pass
        async def preview(self):await asyncio.Future()
    class Input(io.StringIO):
        def isatty(self):return True
    monkeypatch.setattr(module,'EvidenceReview',StalledReview)
    monkeypatch.setattr(module.sys,'stdin',Input('p\n'))
    monkeypatch.setattr(module.sys,'platform','linux')
    import select
    monkeypatch.setattr(select,'select',lambda *args:([module.sys.stdin],[],[]))
    started=time.monotonic()
    with pytest.raises(TimeoutError):await module.evidence_review_hold(object(),1)
    assert time.monotonic()-started<2

async def test_evidence_preview_uses_the_guarded_browser_observation(tmp_path):
    from app.config import OriginPolicy
    from app.safety import authorize_live
    from browser.stagehand_client import StagehandClient
    from browser.evidence_review import EvidenceReview
    path=tmp_path/'review.json'
    path.write_text(json.dumps({'reviewed':False,'dashboard_selector':'main','tenant_selector':'span','readonly_path':'/fixture-readonly','tenant_json_key':'tenant','authenticated_json_key':'authenticated'}))
    config=Config('unused',live_login_enabled=True,evidence_file=str(path))
    permit=authorize_live(config,True,8);permit.started=True
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    class Page:
        async def url(self):return 'https://tprappin2r.netcoresmartech.com/'
        async def evaluate(self,*a,**k):raise AssertionError('Must not bypass guarded observation')
    browser.page=Page();calls=[]
    async def guarded():
        calls.append(True)
        browser.verification_details={'missing':['dashboard','tenant_matches','readonly_access','evidence_configured']}
        return Observation(allowed=False)
    browser.observe=guarded
    review=EvidenceReview(browser)
    preview=await review.preview()
    assert calls==[True] and preview['verification']==str(Result.UNEXPECTED_PAGE)
    with pytest.raises(PermissionError):await review.approve(preview['digest'],True)
    assert browser.evidence is None and not json.loads(path.read_text())['reviewed']

async def test_operator_evidence_review_cannot_override_terminal_controller_policy(monkeypatch):
    from types import SimpleNamespace
    from deep_agents.controller import _execute_doer
    from deep_agents.schemas import DoerReport
    from browser.navigation_guard import NavigationGuard
    from app.config import OriginPolicy
    trace=Trace()
    guard=NavigationGuard(OriginPolicy(set()))
    class Browser:
        config=SimpleNamespace(timeout_seconds=5)
        fixture=True
        page=object()
        evidence_review=True
        login_submissions=0
        verification_details=None
        async def start(self):pass
        async def observe(self):return Observation(allowed=False)
        async def close(self):trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED'
    browser=Browser();browser.trace=trace;browser.guard=guard
    tools=SimpleNamespace(entry_url='http://127.0.0.1:1234/start',navigation_count=1,terminal_error=True)
    class Agent:
        async def ainvoke(self,*a,**k):return {'structured_response':DoerReport(status='blocked',reason='tool_error')}
    monkeypatch.setattr('deep_agents.controller.create_doer',lambda *a,**k:(Agent(),None))
    async def reviewed(*a,**k):
        browser.verification_details={'reason':'VERIFIED_SUCCESS','missing':[]}
        return Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True)
    monkeypatch.setattr('browser.evidence_review.evidence_review_hold',reviewed)
    result=await _execute_doer(browser,tools,'test',None,InferenceBudget(1),True,hold_seconds=1)
    assert result['verification']==str(Result.UNEXPECTED_PAGE) and result['reason_code']=='TERMINAL_BROWSER_POLICY_STOP'
