"""Teacher attribution preflight from saved sanitized evidence; no model or browser."""
import hashlib
import json
from pathlib import Path
import pytest
from app.config import ROOT
from deep_agents.evidence import compile_evidence
from deep_agents.review_contract import ReviewDecision,EvidenceError
from modules.whatsapp.review import WhatsAppReviewAdapter,claims_setup_submission

RUN='985a065565e84b778777763c06d5baba'


def saved_package():
    result_path=ROOT/'outputs'/f'{RUN}-result.json'
    trace_path=ROOT/'outputs'/f'{RUN}.json'
    result=json.loads(result_path.read_text(encoding='utf-8'))
    events=json.loads(trace_path.read_text(encoding='utf-8'))
    adapter=WhatsAppReviewAdapter(result,events)
    return result,events,adapter,compile_evidence(result,events,adapter)


def test_saved_login_submission_has_explicit_login_only_attribution_with_real_references():
    result,events,adapter,package=saved_package()
    attribution=package.module_evidence['submission_attribution']
    assert attribution['workflow']=='netcore.login'
    assert attribution['operation']=='LOGIN_CREDENTIAL_SUBMISSION_ONLY'
    assert attribution['source_tool']=='submit_login' and attribution['event_refs']==[95]
    assert attribution['recorded_login_submissions']==1
    assert attribution['setup_submission_action']=='NOT_AUTHORIZED_OR_INVOKED'
    assert result['explicit_commit_actions']==0 and result['stage_requests']['setup']==0
    assert events[95]['diagnostic']['state']=='TARGET_CHANGED'
    assert any(f['kind']=='SUBMITTED_ONCE' and f['event']==95 for f in package.facts)
    assert not any(f['kind'] in {'SETUP_FORM_DETECTED','SETUP_FIELDS_VERIFIED'} for f in package.facts)
    offered={i for card in package.evidence for i in card.events}
    assert set(attribution['event_refs'])<=offered


def test_login_fact_and_tool_stages_are_scoped_away_from_whatsapp_setup():
    result,events,adapter,package=saved_package()
    stage='initialization';observed={}
    for index,event in enumerate(events):
        stage=adapter.stage(event,stage)
        observed[index]=stage
    assert observed[64]=='login_observation'
    assert observed[66]=='login_submission'  # LOGIN_ACTION_REQUIRED is a login diagnostic.
    assert observed[67]=='login_submission'  # submit_login requested.
    assert observed[68]=='login_field_validation'
    assert observed[95]=='login_submission'  # SUBMITTED_ONCE points to the login click.
    assert observed[170]=='poc_checkpoint'
    assert observed[214]=='whatsapp_setup'
    assert observed[257]=='cleanup'
    assert not any(s['stage']=='submission' for s in package.stages)


def test_saved_false_setup_attribution_is_rejected_without_changing_citations_or_records():
    paths=[ROOT/'outputs'/f'{RUN}{suffix}' for suffix in ['.json','-result.json','-summary.json']]
    before={path:hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    result,events,adapter,package=saved_package()
    actual=ReviewDecision.model_validate(result['teacher_decision'])
    assert 'setup form was submitted once' in actual.finding.lower()
    assert actual.evidence_events==[64,95,170,214,257]
    with pytest.raises(EvidenceError,match='UNSUPPORTED_SETUP_SUBMISSION_CLAIM'):
        adapter.validate(actual,package)
    # This local decision double changes only its prose. The original actual reply
    # and its citations are neither modified nor relabelled as a new successful review.
    corrected=actual.model_copy(update={'finding':
        'Login credentials were submitted once; Setup destination opened, but preflight blocked before field filling. Cleanup completed.'})
    adapter.validate(corrected,package)
    assert corrected.evidence_events==actual.evidence_events
    assert all(hashlib.sha256(path.read_bytes()).hexdigest()==digest for path,digest in before.items())


@pytest.mark.parametrize('finding',[
    'The setup form was submitted once.',
    'The WhatsApp Setup form was submitted once.',
    'Campaign Setup was successfully submitted.',
    'Submitted the WhatsApp setup form.',
    'The campaign form was submitted once.',
])
def test_clear_setup_or_campaign_submission_claims_are_rejected(finding):
    assert claims_setup_submission(finding)
    result,events,adapter,package=saved_package()
    decision=ReviewDecision.model_validate(result['teacher_decision']).model_copy(update={'finding':finding})
    with pytest.raises(EvidenceError,match='UNSUPPORTED_SETUP_SUBMISSION_CLAIM'):
        adapter.validate(decision,package)


@pytest.mark.parametrize('finding',[
    'Login credentials were submitted once; Setup preflight blocked.',
    'The login form was submitted once. The Setup form was not submitted.',
    'No Setup form was submitted. Login completed once; Setup stopped before field filling.',
    'Login submitted once; never submitted the campaign form.',
    'Setup destination opened, but no campaign submission occurred.',
    'Setup values were not verified; no form was submitted.',
])
def test_correct_login_and_unsaved_or_blocked_setup_statements_remain_allowed(finding):
    assert not claims_setup_submission(finding)
    result,events,adapter,package=saved_package()
    decision=ReviewDecision.model_validate(result['teacher_decision']).model_copy(update={'finding':finding})
    adapter.validate(decision,package)


def test_zero_submit_context_never_invents_a_login_submission_reference():
    local={'verification':'UNEXPECTED_PAGE','login_submissions':0,'submission_state':'NOT_ATTEMPTED','step_facts':[]}
    adapter=WhatsAppReviewAdapter(local,[])
    attribution=adapter.context(local)['submission_attribution']
    assert attribution['event_refs']==[] and attribution['recorded_login_submissions']==0
    assert attribution['setup_submission_action']=='NOT_AUTHORIZED_OR_INVOKED'
