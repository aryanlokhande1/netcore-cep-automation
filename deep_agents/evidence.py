"""Deterministic semantic evidence compiler. Source indices are never renumbered."""
import hashlib
import json
import re
from collections import Counter
import tiktoken
import os
from app.config import ROOT
os.environ.setdefault("TIKTOKEN_CACHE_DIR",str(ROOT/".cache"/"tiktoken"))
from deep_agents.review_contract import ReviewPackage,EvidenceCard,EvidenceError

ENCODING=tiktoken.get_encoding('o200k_base')
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=True)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
def tokens(value):return len(ENCODING.encode(value if isinstance(value,str) else canonical(value)))

# Only canonical application codes/booleans/counts can become diagnostic facts.
CODES={'state','stage','category','code','command','scope','kind','resource','policy','completion','status',
    'reason','detached','attached','generation','primary_observable','parent_verified','pending_documents'}
IMPORTANT={'GUARD_FAILURE','COMMAND_FAILED','CHILD_TARGET_DETACHED','REQUEST_DECISION',
    'TARGET_DETACHED','TARGET_DESTROYED','BROWSER_CREATED','NAVIGATION_COMPLETED','DOCUMENT_NAVIGATED',
    'FINAL_OBSERVATION_ATTEMPTED','STALE_COMMAND_PREVENTED'}
IMPORTANT.update({'LOGIN_CONTEXT_READ','LOGIN_SCOPE_CERTIFIED','LOGIN_SCOPE_UNAVAILABLE'})
IMPORTANT.update({'LOGIN_SUBMITTED','TRANSITIONING','APPLICATION_LOADING',
    'APPLICATION_READY_AUTH_UNVERIFIED','AUTHENTICATION_EVIDENCE_PENDING',
    'AUTHENTICATED_AND_VERIFIED','AUTH_FAILED','HUMAN_ACTION_REQUIRED','SESSION_EXPIRED'})
CODES.update({'primary_document_coherent','sdk_page_active','expected_host','document_generation',
    'primary_target_alias','primary_session_alias','certified','inventory_complete','inventory_passes',
    'document_current','negative_scope_complete'})
CODES.update({'inaccessible_shadow_roots','inventory_changed','inventory_open_shadow_roots',
    'user_agent_shadow_roots','unknown_shadow_roots','inventory_limit_reached',
    'inventory_unavailable','inventory_nodes_inspected','inventory_documents_inspected',
    'inventory_commands','inventory_duration_ms','polls','elapsed_ms','deadline_extended',
    'observations','source','destination','authenticated_indicator_count','exact_tenant_indicator_count'})
CODES.update({'relevant_evidence_stable','credential_form','challenge','rejected','expired','account_restricted',
    'status','reviewed','trusted','network_requests_replayed','unreviewed_draft_written','verification_basis'})
CODES.update({'scan_limited','shell_status','security_scope','resource_contract_options'})

def code(value):
    if not isinstance(value,str) or len(value)>100 or not re.fullmatch(r'[A-Za-z0-9_.-]+',value):raise EvidenceError('NON_CANONICAL_EVIDENCE')
    return value

def facts_only(data):
    clean={}
    for key,value in data.items():
        if key not in CODES:continue
        if isinstance(value,str):clean[key]=code(value)
        elif type(value) in (bool,int) or value is None:clean[key]=value
    if isinstance(data.get('guard_failure'),dict):clean['guard_failure']=facts_only(data['guard_failure'])
    if isinstance(data.get('gates'),dict):clean['gates']={code(k):code(v) for k,v in data['gates'].items()}
    for key in ('gate_status','negative_states'):
        if isinstance(data.get(key),dict):clean[key]={code(k):code(v) for k,v in data[key].items()}
    for key in ('scope_certificate','page_context','negative_flags'):
        if isinstance(data.get(key),dict):clean[key]=facts_only(data[key])
    for key in ('resource_status_counts','resource_document_status_counts','json_support_counts'):
        if isinstance(data.get(key),dict):
            clean[key]={code(k):v for k,v in data[key].items() if type(v) is int}
    for key in ('issues','missing','gaps'):
        if isinstance(data.get(key),list):clean[key]=[code(v) for v in data[key][:16]]
    for key in ('dashboard_candidates','tenant_candidates','navigation_candidates','supported_json_resources',
        'authenticated_boolean_resources','matching_tenant_resources','inaccessible_or_unknown_hosts'):
        if type(data.get(key)) is int:clean[key]=data[key]
    return clean

def source_events(events):
    # Review starts after outcome/cleanup; exclude any appended previous Teacher activity.
    for i,event in enumerate(events):
        modern=event.get('agent_execution') or {}
        if event.get('tool_role')=='teacher' or event.get('action_category')=='teacher' or modern.get('role')=='teacher' or modern.get('phase')=='teacher':return events[:i]
    return list(events)


def execution_facts(events,step_facts=()):
    """Deterministic counts of observed activity; never model reasoning."""
    events=source_events(events)
    browser_names={'navigate_approved','inspect_page','interact_observed','submit_login','verify_dashboard','browser_action'}
    executed={e['agent_execution'].get('invocation_id') for e in events if
        (e.get('agent_execution') or {}).get('kind')=='tool_result' and (e.get('agent_execution') or {}).get('status') in {'RETURNED','FAILED'}}
    modern=[e['agent_execution'] for e in events if (e.get('agent_execution') or {}).get('kind')=='tool_invocation'
        and (e.get('agent_execution') or {}).get('tool') in browser_names and e['agent_execution']['id'] in executed]
    calls=[{'tool':e['tool_name'],'phase':'task_doer' if e['tool_name']=='browser_action' else 'login_doer'}
        for e in events if e.get('tool_phase')=='started' and e.get('tool_name') in browser_names] or modern
    return {'browser_creation_observed':any(e.get('action_category')=='initialize' or
            (e.get('diagnostic_stage')=='BROWSER' and (e.get('diagnostic') or {}).get('state')=='BROWSER_CREATED') for e in events),
        'login_tool_calls':sum(e.get('phase')!='task_doer' for e in calls),
        'task_tool_calls':sum(e.get('phase')=='task_doer' for e in calls),
        'credential_submissions_observed':len({f.get('event_index') for f in step_facts if f.get('kind')=='SUBMITTED_ONCE'}),
        'observation_records':sum(e.get('observation') is not None for e in events)}


def tool_result_facts(data):
    """Only fixed categories and strict observation flags cross into Teacher."""
    result={}
    for key in ('ok','state','error','verification','terminal','retry_safe','submitted'):
        value=data.get(key)
        if type(value) is bool or value is None:result[key]=value
        elif isinstance(value,str):result[key]=code(value)
    if isinstance(data.get('observation'),dict):
        from app.state import Observation
        result['observation']=Observation.model_validate(data['observation']).model_dump()
    return result

def compile_evidence(result,events,adapter,history=None,max_tokens=6000):
    events=source_events(events)
    groups={};stages={};stage='initialization'
    def card(kind,index,data):
        key=(kind,canonical(data))
        if key not in groups:groups[key]={'kind':kind,'events':[],'data':data}
        groups[key]['events'].append(index)
    for index,event in enumerate(events):
        stage=adapter.stage(event,stage)
        current=stages.setdefault(stage,{'stage':stage,'first_event':index,'last_event':index,'event_count':0})
        current['last_event']=index;current['event_count']+=1
        category=event.get('action_category')
        if event.get('observation') is not None:
            card('observation',index,{'flags':adapter.observation(event['observation']),'result':code(event['step_result'])})
        if category=='tool' and event.get('tool_phase') in {'returned','failed','denied'}:
            card('doer_action',index,{'tool':code(event['tool_name']),'phase':code(event['tool_phase'])})
        modern=event.get('agent_execution') or {}
        if modern.get('kind')=='tool_result':
            card('tool_result',index,{'tool':code(modern['tool']),'status':code(modern['status']),
                'role':code(modern['role']),'result':tool_result_facts(modern['result']) if isinstance(modern.get('result'),dict) else None,
                'error_class':code(modern['error']) if modern.get('error') else None})
        if category in {'entry','verify','outcome','close','error','recover'}:
            data={'result':code(event.get('step_result','NOT_TESTED'))}
            if event.get('stop_code'):data['stop']=code(event['stop_code'])
            if event.get('error_category'):data['error']=code(event['error_category'])
            card(category,index,data)
        diagnostic=event.get('diagnostic') or {}
        if diagnostic.get('state') in IMPORTANT or diagnostic.get('status')=='ERROR' or diagnostic.get('code') or event.get('diagnostic_stage') in {'LOGIN_READINESS','DISCOVERY_RESOURCE'}:
            card('transition' if diagnostic.get('state')!='GUARD_FAILURE' and diagnostic.get('status')!='ERROR' else 'failure',index,facts_only(diagnostic))
        elif event.get('diagnostic_stage') in {'DISCOVERY','PREVIEW','APPROVAL','RESOURCE_OPTIONS','SELECTION'}:
            card('authentication_review',index,{'stage':code(event['diagnostic_stage']),**facts_only(diagnostic)})
        if diagnostic and event.get('diagnostic_stage') in {'LOGIN_CONTEXT','LOGIN_SCOPE','LOGIN_READINESS','DISCOVERY','PREVIEW','APPROVAL','RESOURCE_OPTIONS','SELECTION','DISCOVERY_RESOURCE'}:
            # Digest the original sanitized event, not a reconstructed narrative.
            card('source_digest',index,{'sha256':digest(event)})
        if modern.get('kind')=='graph_route':
            card('graph_route',index,facts_only(modern))
    facts=[]
    for fact in result.get('step_facts',[]):
        index=fact.get('event_index')
        if type(index) is not int or not 0<=index<len(events):raise EvidenceError('INVALID_FACT_REFERENCE')
        facts.append({'kind':code(fact['kind']),'event':index})
        card('fact',index,{'kind':code(fact['kind'])})
    comparison=[]
    if history is None:history=(result.get('teacher_review_context') or {}).get('comparison_history',[])
    if len(history or [])>12:raise EvidenceError('COMPARISON_CONTEXT_BUDGET_EXCEEDED')
    for previous in history or []:
        assessment=previous.get('strategy_assessment',{})
        comparison.append({'run_id':code(previous['run_id']),'verification':code(previous['verification']),
            'decision':code(previous.get('decision',assessment.get('decision','NOT_AVAILABLE'))),
            'rules':previous.get('rules',assessment.get('next_strategy',{}).get('rules',[]))})
    metrics={k:v for k,v in result.get('metrics',{}).items() if k in {'browser_actions','doer_duration_seconds','inspections','redundant_inspections','recovery_used'} and type(v) in (int,float,bool)}
    metrics['doer_requests']=result.get('role_usage',{}).get('doer',{}).get('requests',sum(e.get('tool_phase')=='requested' and e.get('tool_role')=='doer' for e in events))
    metrics['tool_errors']=sum(e.get('action_category')=='error' for e in events)
    strategy=result.get('review_previous_strategy') or {'version':code(result.get('strategy_version','NOT_RETAINED')),'rules':None}
    package=ReviewPackage(run_id=code(result.get('source_run',result.get('run_id','NOT_RETAINED'))),workflow=adapter.identity,
        verified_result=code(result['verification']),outcome=adapter.outcome(result['verification']),reason_code=code(result.get('reason_code','NOT_RETAINED')),
        module_evidence=adapter.context(result),facts=facts,stages=list(stages.values()),evidence=[EvidenceCard(**g) for g in groups.values()],metrics=metrics,
        previous_strategy=strategy,comparison=comparison,source_event_count=len(events))
    body=package.model_dump()
    if tokens(body)>max_tokens:raise EvidenceError('EVIDENCE_CONTEXT_BUDGET_EXCEEDED')
    # Independent ref integrity after deterministic aggregation, not offsets in a new list.
    if any(not 0<=i<len(events) for c in package.evidence for i in c.events):raise EvidenceError('INVALID_CANONICAL_REFERENCE')
    return package

def retrieve_evidence(package,indices,max_references=8,max_tokens=1500):
    requested=set(indices)
    if len(requested)>max_references:raise EvidenceError('RETRIEVAL_REFERENCE_BUDGET')
    offered={i for c in package.evidence for i in c.events}
    if not requested<=offered:raise EvidenceError('UNKNOWN_CANONICAL_REFERENCE')
    selected=[c.model_dump() for c in package.evidence if requested.intersection(c.events)]
    if tokens(selected)>max_tokens:raise EvidenceError('RETRIEVAL_TOKEN_BUDGET')
    return selected


def retrieve_source_evidence(events,indices,adapter,max_references=8,max_tokens=1500):
    """Bounded additional archived evidence, including omitted protocol details.

    This function cannot invoke a model. A caller must explicitly budget a later review.
    """
    source=source_events(events);requested=sorted(set(indices))
    if len(requested)>max_references or any(type(i) is not int or not 0<=i<len(source) for i in requested):
        raise EvidenceError('RETRIEVAL_REFERENCE_BUDGET')
    output=[]
    for index in requested:
        event=source[index]
        item={'event_index':index,'action':code(event['action_category']),
            'result':code(event.get('step_result','NOT_TESTED'))}
        if event.get('diagnostic'):item['diagnostic']=facts_only(event['diagnostic'])
        if event.get('observation') is not None:item['observation']=adapter.observation(event['observation'])
        output.append(item)
    if tokens(output)>max_tokens:raise EvidenceError('RETRIEVAL_TOKEN_BUDGET')
    return output
