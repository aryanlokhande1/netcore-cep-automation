"""Deterministic saved-real-evidence validation only. No fabricated Teacher responses."""
import json
from pathlib import Path
import pytest
from app.model_settings import ModelSettings,selected_model_environment
from app.trace import Trace
from deep_agents.teacher_replay import load_saved_live
from deep_agents.evidence import compile_evidence,canonical,source_events,retrieve_evidence,tokens
from deep_agents.review import review_evidence
from deep_agents.review_contract import ReviewDecision,EvidenceError
from deep_agents.models import openai_model
from deep_agents.permissions import InferenceBudget,TokenPool
from modules.login.review import LoginReviewAdapter

LATEST='9a819d85adf64f1884f656349ea411c7'
PREVIOUS='77b49f41c7f34ceda3cbb1047a382537'

@pytest.mark.parametrize('rid',[LATEST,PREVIOUS,'74fbfd2ab12f4c9c9eb0350faa89eed3','e0612bc79b084dd0a411ccce8e9fed78'])
def test_compilation_of_immutable_real_evidence_is_deterministic_and_equivalent(rid):
    result,events,manifest,paths=load_saved_live(rid)
    first=compile_evidence(result,events,LoginReviewAdapter())
    second=compile_evidence(result,source_events(events),LoginReviewAdapter())
    assert canonical(first.model_dump())==canonical(second.model_dump())
    offered={i for c in first.evidence for i in c.events}
    assert all(0<=i<len(events) for i in offered)
    assert {f['event_index'] for f in result['step_facts']}<=offered
    critical={i for i,e in enumerate(source_events(events)) if e.get('action_category') in {'error','verify','outcome'} or e.get('diagnostic',{}).get('state') in {'GUARD_FAILURE','COMMAND_FAILED','CHILD_TARGET_DETACHED'} or e.get('diagnostic',{}).get('status')=='ERROR'}
    assert critical<=offered
    assert first.verified_result!='AUTHENTICATED_AND_VERIFIED' and tokens(first.model_dump())<6000
    assert all('timestamp' not in canonical(c.data) for c in first.evidence)
    assert manifest['source']=='live' and not manifest['mocked']


def test_latest_retains_fatal_guard_and_handled_children_with_original_references():
    r,es,*_=load_saved_live(LATEST);p=compile_evidence(r,es,LoginReviewAdapter())
    assert any(120 in c.events and c.data.get('state')=='GUARD_FAILURE' for c in p.evidence)
    assert any(92 in c.events and c.data.get('state')=='CHILD_TARGET_DETACHED' for c in p.evidence)
    assert any(95 in c.events and c.kind=='fact' for c in p.evidence)
    with pytest.raises(EvidenceError):compile_evidence(r,es,LoginReviewAdapter(),max_tokens=10)
    with pytest.raises(EvidenceError):retrieve_evidence(p,[999999])


def test_real_archived_proposal_has_valid_refs_but_native_decision_rejects_false_success():
    r,es,*_=load_saved_live(PREVIOUS);p=compile_evidence(r,es,LoginReviewAdapter())
    assert all(0<=i<len(es) for i in r['teacher_proposal']['evidence_events'])
    # Validate the machine contract, not a mocked model response.
    with pytest.raises(EvidenceError):LoginReviewAdapter().validate(ReviewDecision(outcome='success',decision='no_change',
        finding='Unsupported result',evidence_events=[74],strategy_rule=None,proposed_change=''),p)


def test_token_reservations_are_shared_and_cannot_escalate():
    pool=TokenPool(input_limit=10,output_limit=20)
    one=InferenceBudget(1,token_pool=pool);two=InferenceBudget(1,token_pool=pool)
    one.reserve_tokens(8,10)
    with pytest.raises(RuntimeError):two.reserve_tokens(3,5)
    assert pool.input_reserved==8


def test_model_only_loader_does_not_load_credential_keys(tmp_path,monkeypatch):
    path=tmp_path/'selected.env';path.write_text('OPENAI_MODEL=gpt-6-luna\nNETCORE_PASSWORD=DO_NOT_LOAD\nTEACHER_REASONING_EFFORT=low\n')
    data=selected_model_environment(path)
    assert 'NETCORE_PASSWORD' not in data and data['OPENAI_MODEL']=='gpt-6-luna'


async def test_real_factory_boundary_probe_aborts_before_any_http(monkeypatch):
    # This test supplies no model response. It captures the genuine installed factory's
    # outbound request then intentionally aborts transport before network execution.
    r,events,*_=load_saved_live(LATEST)
    trace=Trace();trace.events=source_events(events)
    budget=InferenceBudget(1,role_limits={'doer':0,'teacher':1})
    model=openai_model('teacher',ModelSettings(api_key='NOT_SENT'),trace,budget)
    calls=[]
    async def blocked_transport(*a,**k):calls.append(True);raise RuntimeError('NETWORK_DISABLED_BOUNDARY_PROBE')
    monkeypatch.setattr(model.root_async_client.responses,'create',blocked_transport)
    monkeypatch.setattr(model.root_async_client.responses,'parse',blocked_transport)
    await review_evidence(dict(r),trace,model,budget,False,adapter=LoginReviewAdapter())
    assert len(calls)==1
    assert trace.boundary_diagnostics[-1]['provider_native_schema']
    assert trace.boundary_diagnostics[-1]['input_bound_with_margin']<8000
    assert trace.boundary_diagnostics[-1]['reasoning_effort']=='low'
    assert trace.boundary_diagnostics[-1]['max_output_tokens']==2000
    assert trace.teacher_result=='ANALYSIS_UNAVAILABLE'  # No model response was fabricated.


def test_public_and_compatibility_citation_contracts_have_identical_capacity():
    from deep_agents.schemas import TeacherProposal
    from deep_agents.review_contract import ReviewDecision,MAX_EVIDENCE_CITATIONS
    assert TeacherProposal.model_json_schema()['properties']['evidence_events']['maxItems']==MAX_EVIDENCE_CITATIONS
    assert ReviewDecision.model_json_schema()['properties']['evidence_events']['maxItems']==MAX_EVIDENCE_CITATIONS


def test_retained_output_incomplete_is_classified_before_missing_proposal():
    from deep_agents.diagnostics import teacher_failure
    result,*_=load_saved_live(LATEST)
    trace=Trace();trace.model_diagnostics=result['model_diagnostics'];trace.role_usage=result['role_usage']
    diagnostic=teacher_failure(ValueError(),'PROPOSAL_VALIDATION',trace)
    assert diagnostic['category']=='OUTPUT_LIMIT_INCOMPLETE'


def test_separate_role_limits_and_bounded_source_retrieval():
    from deep_agents.evidence import retrieve_source_evidence
    settings=ModelSettings(api_key='NOT_SENT')
    doer=openai_model('doer',settings);teacher=openai_model('teacher',settings)
    assert doer.max_tokens==500 and teacher.max_tokens==2000
    assert doer.max_retries==teacher.max_retries==0
    r,es,*_=load_saved_live(LATEST)
    detail=retrieve_source_evidence(es,[120],LoginReviewAdapter())
    assert detail[0]['event_index']==120 and detail[0]['diagnostic']['state']=='GUARD_FAILURE'


def test_persisted_comparison_context_replays_the_same_production_package():
    current,events,*_=load_saved_live(LATEST)
    previous,*_=load_saved_live(PREVIOUS)
    history=[{'run_id':previous['run_id'],'verification':previous['verification'],
        'strategy_assessment':{'decision':previous['learning']['decision']}}]
    production=compile_evidence(current,events,LoginReviewAdapter(),history)
    replay_input={**current,'teacher_review_context':{'comparison_history':production.comparison}}
    replay=compile_evidence(replay_input,events,LoginReviewAdapter())
    assert canonical(production.model_dump())==canonical(replay.model_dump())
