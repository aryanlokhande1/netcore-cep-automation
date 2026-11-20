"""Reusable, sanitized observation/review diagnostics. No exception text is serialized."""
from app.state import Result
import re

GATES=('expected_host','dashboard','tenant_matches','readonly_access','evidence_configured')
_OPERATOR_ID=re.compile(r'[a-z][a-z0-9_]{0,47}\Z')
_CANDIDATE_REFERENCE=re.compile(r'candidate-[0-9a-f]{32}-(dashboard|tenant|navigation|sample)-[1-9][0-9]*\Z')
_SHA256=re.compile(r'[0-9a-f]{64}\Z')
_OPERATOR_CHOICES={
    'inspect_dashboard','inspect_tenant','inspect_navigation','inspect_whatsapp',
    'inspect_resource','inspect_sample','review_candidate','review_resource',
    'preview_evidence','approve_evidence','select_candidate','start_diagnostic',
    'continue','abort','stop','yes','no',
}
_OPERATOR_PROMPTS={
    'inspect_dashboard','inspect_tenant','inspect_navigation','inspect_whatsapp',
    'review_candidate','review_resource','approve_evidence','whatsapp_diagnostic',
    'whatsapp_diagnostic_consent','login_evidence_review','operator_menu',
    'resource_contract','candidate_review',
}
_DYNAMIC_CANDIDATE_CHOICE=re.compile(r'candidate_[1-9][0-9]?\Z')
_OPERATOR_FIELDS={
    'event_id','timestamp','prompt_id','choice','status','elapsed_seconds',
    'invalid_attempts','timeout_extensions','reference','kind','state','visible',
    'document_generation','scope','collector_gate','selector_sha256',
}
_OPERATOR_STAGES={'DECISION','CANDIDATE_BINDING'}

class EvidenceFailure(ValueError):
    def __init__(self,code):
        self.code=code
        super().__init__(code)

class ApprovalFailure(EvidenceFailure,PermissionError):
    pass

def gate_summary(observation=None):
    return {key:('PASS' if getattr(observation,key) else 'FAIL') if observation is not None else 'NOT_CHECKED' for key in GATES}

def evidence_diagnostic(error,stage,browser=None):
    from pydantic import ValidationError
    from browser.stagehand_client import BrowserBudgetExceeded

    budget = {}
    if isinstance(error, BrowserBudgetExceeded):
        code = error.code
        budget = {'budget_kind': error.budget_kind, 'actions_used': error.actions_used,
                  'actions_limit': error.actions_limit}
    elif isinstance(error,EvidenceFailure):code=error.code
    elif isinstance(error,ValidationError):code='INVALID_EVIDENCE_SCHEMA'
    elif isinstance(error,FileNotFoundError):code='MISSING_EVIDENCE_CONFIGURATION'
    elif isinstance(error,TimeoutError):code='BROWSER_OBSERVATION_UNAVAILABLE'
    elif isinstance(error,PermissionError):code='OBSERVATION_POLICY_BLOCKED'
    elif stage in {'DISCOVERY','PREVIEW','APPROVAL','FINAL_READ_ONLY_OBSERVATION'}:code='BROWSER_OBSERVATION_UNAVAILABLE'
    else:code='INTERNAL_VERIFIER_EXCEPTION'
    guard=getattr(browser,'guard',None)
    if guard and guard.failed:code='NAVIGATION_GUARD_UNAVAILABLE'
    return {'status':'ERROR','stage':stage,'code':code,'gates':{key:'NOT_CHECKED' for key in GATES},
        'guard_failure':getattr(guard,'failure_diagnostic',None) if guard else None,'trusted':False,**budget}

def review_event(browser,stage,data):
    """Persist canonical gates/codes only, never a document, selector or resource path."""
    from app.config import ROOT
    permitted={'status','stage','code','verification','gates','gate_status','missing','issues','trusted','digest',
        'dashboard_candidates','tenant_candidates','observed_json_resources','supported_json_resources',
        'unreviewed_draft_written','operator_approval_required','network_requests_replayed','gaps','guard_failure',
        'authenticated_boolean_resources','matching_tenant_resources','cached_response_unavailable','transport_status',
        'budget_kind','actions_used','actions_limit'}
    permitted.update({'closed_root_coverage','open_shadow_roots_inspected','inaccessible_or_unknown_hosts','shadow_roots_uninspected','node_limit_reached','shadow_limit_reached','query_resources_observed','negative_states','resource_status_counts','documents_inspected','permitted_frames_unavailable',
        'navigation_candidates','observation_samples','confidence_limitations','next_steps','verification_basis',
        'resource_document_status_counts','json_support_counts','browser_actions_remaining','candidate_type_counts',
        'external_frames_skipped'})
    permitted.update({'page_context','scope_certificate','provenance','options','meaning','resource_contract_options','dom_diagnostics','resource_diagnostics'})
    permitted.update({'rendered_ui_comparison','render_comparison_status','render_comparison_screenshot','render_comparison_ax_ignored'})
    safe={key:value for key,value in data.items() if key in permitted}
    # Internal preview booleans are prospective; persisted Teacher diagnostics use
    # the existing canonical PASS/FAIL/NOT_CHECKED gate vocabulary.
    if isinstance(safe.get('gates'),dict):
        safe['gates']={key:('PASS' if value else 'FAIL') if type(value) is bool else value
            for key,value in safe['gates'].items() if key in GATES}
    if not getattr(browser,'trace',None):return safe
    browser.trace.add('diagnostic')
    browser.trace.events[-1].update(diagnostic_stage=stage,diagnostic=safe)
    browser.trace.write(ROOT/'outputs')
    return safe

def operator_event(browser,stage,data):
    """Record only bounded operator enums and opaque current-reference evidence."""
    if stage not in _OPERATOR_STAGES or not isinstance(data,dict):
        raise ValueError('Invalid operator event')
    safe={'stage':stage}
    for key,value in data.items():
        if key not in _OPERATOR_FIELDS:continue
        if key in {'prompt_id','choice','kind'}:
            if not isinstance(value,str) or not _OPERATOR_ID.fullmatch(value):continue
            if key=='prompt_id' and value not in _OPERATOR_PROMPTS:continue
            if key=='kind' and value not in {'dashboard','tenant','navigation','sample'}:continue
            if key=='choice' and value not in _OPERATOR_CHOICES and not _DYNAMIC_CANDIDATE_CHOICE.fullmatch(value):continue
        elif key=='reference':
            if not isinstance(value,str) or not _CANDIDATE_REFERENCE.fullmatch(value):continue
        elif key=='selector_sha256':
            if not isinstance(value,str) or not _SHA256.fullmatch(value):continue
        elif key=='status':
            if value not in {'SELECTED','TIMED_OUT','TIMED_OUT_DEFAULT','CURRENT_VISIBLE_DOM_REFERENCE'}:continue
        elif key=='state':
            if value!='CURRENT_VISIBLE_DOM_REFERENCE':continue
        elif key=='scope':
            if value not in {'TOP_LEVEL','SHADOW_OR_FRAME'}:continue
        elif key=='collector_gate':
            if value not in {'ELIGIBLE_PRECLASSIFICATION','NOT_VISIBLE','TRANSIENT_OR_HIDDEN_SCOPE',
                    'NO_UNIQUE_SAFE_SELECTOR','REFERENCE_NOT_CAPTURED'}:continue
        elif key=='visible':
            if type(value) is not bool:continue
        elif key=='timestamp':
            if not isinstance(value,str) or len(value)>40 or not re.fullmatch(r'\d{4}-\d\d-\d\dT[^ ]{1,30}',value):continue
        elif key=='event_id':
            if not isinstance(value,str) or not re.fullmatch(r'[0-9a-f]{32}',value):continue
        elif key in {'elapsed_seconds'}:
            if type(value) not in (int,float) or not 0<=value<=_MAX_OPERATOR_SECONDS:continue
        elif key in {'invalid_attempts','timeout_extensions','document_generation'}:
            if type(value) is not int or not 0<=value<=_MAX_OPERATOR_SECONDS:continue
        safe[key]=value
    if not getattr(browser,'trace',None):return safe
    browser.trace.lifecycle('OPERATOR_'+stage,safe)
    return safe

_MAX_OPERATOR_SECONDS=600
