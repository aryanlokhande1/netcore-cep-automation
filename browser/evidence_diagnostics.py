"""Reusable, sanitized observation/review diagnostics. No exception text is serialized."""
from app.state import Result

GATES=('expected_host','dashboard','tenant_matches','readonly_access','evidence_configured')

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
    if isinstance(error,EvidenceFailure):code=error.code
    elif isinstance(error,ValidationError):code='INVALID_EVIDENCE_SCHEMA'
    elif isinstance(error,FileNotFoundError):code='MISSING_EVIDENCE_CONFIGURATION'
    elif isinstance(error,TimeoutError):code='BROWSER_OBSERVATION_UNAVAILABLE'
    elif isinstance(error,PermissionError):code='OBSERVATION_POLICY_BLOCKED'
    elif stage in {'DISCOVERY','PREVIEW','APPROVAL','FINAL_READ_ONLY_OBSERVATION'}:code='BROWSER_OBSERVATION_UNAVAILABLE'
    else:code='INTERNAL_VERIFIER_EXCEPTION'
    guard=getattr(browser,'guard',None)
    if guard and guard.failed:code='NAVIGATION_GUARD_UNAVAILABLE'
    return {'status':'ERROR','stage':stage,'code':code,'gates':{key:'NOT_CHECKED' for key in GATES},
        'guard_failure':getattr(guard,'failure_diagnostic',None) if guard else None,'trusted':False}

def review_event(browser,stage,data):
    """Persist canonical gates/codes only, never a document, selector or resource path."""
    from app.config import ROOT
    permitted={'status','stage','code','verification','gates','gate_status','missing','issues','trusted','digest',
        'dashboard_candidates','tenant_candidates','observed_json_resources','supported_json_resources',
        'unreviewed_draft_written','operator_approval_required','network_requests_replayed','gaps','guard_failure',
        'authenticated_boolean_resources','matching_tenant_resources','cached_response_unavailable','transport_status'}
    permitted.update({'closed_root_coverage','open_shadow_roots_inspected','inaccessible_or_unknown_hosts','shadow_roots_uninspected','node_limit_reached','shadow_limit_reached','query_resources_observed','negative_states','resource_status_counts','documents_inspected','permitted_frames_unavailable',
        'navigation_candidates','observation_samples','confidence_limitations','next_steps','verification_basis',
        'resource_document_status_counts','json_support_counts','browser_actions_remaining','candidate_type_counts'})
    permitted.update({'page_context','scope_certificate','provenance','options','meaning','resource_contract_options'})
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
