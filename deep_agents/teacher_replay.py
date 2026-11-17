"""Actual production Teacher replay of immutable sanitized live evidence only."""
import hashlib
import json
import re
from pathlib import Path
from uuid import uuid4
from app.config import ROOT,ConfigurationError
from app.model_settings import ModelSettings
from app.trace import Trace
from app.state import Observation
from deep_agents.evidence import source_events,compile_evidence,digest,canonical
from deep_agents.review import review_evidence
from deep_agents.models import openai_model
from deep_agents.permissions import InferenceBudget
from modules.login.review import LoginReviewAdapter

EVENT_KEYS={'action_category','cleanup','codex_calls','diagnostic','diagnostic_stage','elapsed_seconds','error_category',
    'iteration','model_calls','observation','observation_kind','openai_api_calls','reasoner_mode','reasoning_turns','run_id',
    'step_result','stop_code','strategy_version','timestamp','tool_name','tool_phase','tool_role','total_browser_actions','verification_result'}
DIAGNOSTIC_KEYS={'state','elapsed_ms','kind','target','session','waiting_for_debugger','command','duration_ms','category',
    'detached','stage','status','code','guard','gates','guard_failure','trusted','parent_verified','generation','attached',
    'scope','policy','resource','request','primary_observable','pending_documents','completion','reason','fixture','redirect',
    'digest','verification','missing','issues','gate_status','discovery','dashboard_candidates','tenant_candidates',
    'observed_json_resources','supported_json_resources','unreviewed_draft_written','operator_approval_required',
    'network_requests_replayed','gaps','transport_status','authenticated_boolean_resources','matching_tenant_resources',
    'cached_response_unavailable','rcvd_close_code','sent_close_code','last_observation_retained'}

def source_hash(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def validate_diagnostic(data):
    if not set(data)<=DIAGNOSTIC_KEYS:raise ConfigurationError('Unsupported unsanitized diagnostic fields')
    for key,value in data.items():
        if isinstance(value,str) and (len(value)>128 or re.search(r'https?://|sk-|[\\/]|[<>{}]',value)):
            raise ConfigurationError('Non-canonical diagnostic value')
        if isinstance(value,dict) and key in {'guard_failure'}:validate_diagnostic(value)

def load_saved_live(run_id):
    if not re.fullmatch('[a-f0-9]{32}',run_id):raise ConfigurationError('Invalid source run ID')
    paths={name:ROOT/'outputs'/f'{run_id}{suffix}' for name,suffix in {
        'trace':'.json','result':'-result.json','summary':'-summary.json'}.items()}
    if any(not p.is_file() or not p.resolve().is_relative_to((ROOT/'outputs').resolve()) for p in paths.values()):
        raise ConfigurationError('Complete original trace/result/summary required')
    r=json.loads(paths['result'].read_bytes());es=json.loads(paths['trace'].read_bytes());summary=json.loads(paths['summary'].read_bytes())
    if r.get('run_id')!=run_id or summary.get('run_id')!=run_id or r.get('source')!='live' or r.get('mode')!='openai' or r.get('mocked') is not False:
        raise ConfigurationError('Expected genuine persisted live provenance')
    if not r.get('actual_api_requests') or not es or not any(e.get('action_category')=='outcome' for e in es):
        raise ConfigurationError('Incomplete live execution record')
    if summary.get('cleanup_result')!=r.get('cleanup_result'):raise ConfigurationError('Inconsistent source cleanup provenance')
    for e in es:
        if not set(e)<=EVENT_KEYS or e.get('run_id')!=run_id:raise ConfigurationError('Unexpected trace schema/provenance')
        if e.get('reasoner_mode')!='deepagents_openai_gpt-6-luna':raise ConfigurationError('Expected actual model execution trace')
        if e.get('observation') is not None:Observation.model_validate(e['observation'])
        if e.get('diagnostic'):validate_diagnostic(e['diagnostic'])
    # The compiler independently admits only typed observations and canonical facts;
    # raw source metadata/old model prose is never added to the review package.
    manifest={'run_id':run_id,'source':'live','mocked':False,'model':'gpt-6-luna',
        'sha256':{k:source_hash(p) for k,p in paths.items()},'source_event_count':len(es),
        'source_authentication_verified':r['verification']=='AUTHENTICATED_AND_VERIFIED'}
    return r,es,manifest,paths

async def teacher_replay(run_id,authorized_api=False,api_request_budget=0,max_output_tokens=2000):
    if not authorized_api or api_request_budget!=1 or not 1<=max_output_tokens<=2000:
        raise ConfigurationError('Approve exactly one Teacher-only inference request and at most 2000 output tokens')
    result,events,manifest,paths=load_saved_live(run_id)
    settings=ModelSettings.load()  # Model-only allowlist; never Config.load/credentials.
    cap=min(max_output_tokens,settings.teacher_output)
    adapter=LoginReviewAdapter()
    before=compile_evidence(result,events,adapter)
    # Production receives the pre-Teacher prefix; replay may load a trailing old review.
    production=compile_evidence(result,source_events(events),adapter)
    if canonical(before.model_dump())!=canonical(production.model_dump()):raise ConfigurationError('Production/replay evidence mismatch')
    trace=Trace();trace.events=source_events(events)
    trace.reasoner_mode='deepagents_openai_gpt-6-luna'
    trace.step_facts=list(result.get('step_facts',[]))
    budget=InferenceBudget(1,role_limits={'doer':0,'teacher':1},max_input_tokens=settings.teacher_input,max_output_tokens=cap)
    model=openai_model('teacher',settings,trace,budget,max_output_tokens=cap)
    # Original result is input only; diagnostic output is separate and does not update skills.
    review_result={k:result[k] for k in result if k not in {'teacher_proposal','teacher_diagnostic','teacher_error','teacher_status','teacher_decision'}}
    await review_evidence(review_result,trace,model,budget,False,adapter=adapter)
    unchanged=manifest['sha256']=={k:source_hash(p) for k,p in paths.items()}
    output={'replay_id':trace.run_id,'source_manifest':manifest,'source_unchanged':unchanged,
        'production_replay_equivalent':True,'evidence_digest':digest(before.model_dump()),
        'model':'gpt-6-luna','mode':'openai','mocked':False,'actual_api_requests':trace.openai_api_calls,
        'model_requests':budget.requests,'max_output_tokens':cap,'max_input_tokens':settings.teacher_input,
        'reasoning_effort':settings.teacher_reasoning,'token_usage':trace.token_usage,'role_usage':trace.role_usage,
        'model_diagnostics':trace.model_diagnostics,'boundary_diagnostics':trace.boundary_diagnostics,
        **{k:review_result.get(k) for k in ['teacher_status','teacher_error','teacher_diagnostic','teacher_decision',
            'teacher_proposal','teacher_rejected_decision','teacher_validation_summary','teacher_evidence','teacher_review_seconds']},
        'browser_launched':False,'credentials_loaded':False,'netcore_accessed':False,'candidate_generated':False,
        'sdk_retries':0,'model_fallback':False}
    if not unchanged:output.update(teacher_status='ANALYSIS_UNAVAILABLE',teacher_error='SOURCE_CHANGED_DURING_REVIEW')
    folder=ROOT/'outputs'/'teacher-replays'/trace.run_id;folder.mkdir(parents=True,exist_ok=False)
    (folder/'result.json').write_text(json.dumps(output,indent=2),encoding='utf-8')
    (folder/'evidence.json').write_text(json.dumps(before.model_dump(),indent=2),encoding='utf-8')
    return output
