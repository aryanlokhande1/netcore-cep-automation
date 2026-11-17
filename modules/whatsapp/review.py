"""Module adapter for the unchanged production Teacher and teacher-evidence/v1."""
from app.state import Result
from modules.login.review import LoginReviewAdapter
from deep_agents.review_contract import EvidenceError
import re


LOGIN_STAGES={'navigate_approved':'login_navigation','inspect_page':'login_observation',
    'submit_login':'login_submission','verify_dashboard':'login_verification',
    'read_approved_skill':'login_skill_loading','DoerReport':'login_report'}
LOGIN_FACT_STAGES={'LOGIN_RECOGNIZED':'login_observation','FIELD_ROLES_VALIDATED':'login_field_validation',
    'SUBMITTED_ONCE':'login_submission','EXISTING_SESSION_VERIFIED':'login_verification'}


def claims_setup_submission(text):
    """Reject clear positive Setup/campaign-submission claims, preserving negations."""
    subject=r'(?:(?:whatsapp\s+)?(?:campaign\s+)?setup(?:\s+form)?|(?:whatsapp\s+)?campaign(?:\s+form)?)'
    pattern=re.compile(r'\b'+subject+r'\s+(?:(?:was|is|has|had|been|successfully|then|also)\s+)*submitted\b'
        +r'|\bsubmitted\s+(?:(?:the|a|an)\s+)?'+subject+r'\b',re.I)
    for match in pattern.finditer(text):
        prefix=text[max(0,match.start()-40):match.start()]
        if not re.search(r'\b(?:no|never|not)\s+(?:(?:the|a|an)\s+)?$',prefix,re.I):return True
    return False


class WhatsAppReviewAdapter(LoginReviewAdapter):
    identity={'module':'netcore.whatsapp.setup','task':'fill_four_setup_fields_and_stop_unsaved',
        'expected_outcomes':['SETUP_FILLED_UNSAVED'],'supported_rules':[],
        'scope':'POC_ONLY_NOT_CAMPAIGN_CREATION_OR_AUTHENTICATION_CERTIFICATION',
        'never_claim':['saved_campaign','published_campaign','scheduled_campaign','sent_message']}
    def __init__(self,result,events):
        from deep_agents.evidence import source_events
        self.result=result;self.events=source_events(events)
        self.login_fact_events=[(self.events[f['event_index']],LOGIN_FACT_STAGES[f['kind']])
            for f in result.get('step_facts',[]) if f['kind'] in LOGIN_FACT_STAGES
            and type(f.get('event_index')) is int and 0<=f['event_index']<len(self.events)]

    @staticmethod
    def citation_groups(facts,success,cleanup,guard):
        kinds=['LOGIN_RECOGNIZED','SUBMITTED_ONCE','EXISTING_SESSION_VERIFIED','HUMAN_CONFIRMED_FOR_POC']
        if success:kinds.append('SETUP_FIELDS_VERIFIED')
        groups={kind:sorted({f['event'] for f in facts if f['kind']==kind}) for kind in kinds}
        if guard:groups['GUARD_FAILURE']=sorted(guard)
        if cleanup:groups['CLEANUP']=sorted(cleanup)
        return {kind:indices for kind,indices in groups.items() if indices}
    def outcome(self,result):return 'success' if result==Result.SETUP_FILLED_UNSAVED else 'blocked'
    def stage(self,event,current):
        if event.get('action_category')=='close':return 'cleanup'
        if event.get('action_category')=='outcome':return 'finalization'
        for source,stage in self.login_fact_events:
            if event is source or event==source:return stage
        if event.get('tool_name') in LOGIN_STAGES:return LOGIN_STAGES[event['tool_name']]
        if event.get('diagnostic_stage')=='WHATSAPP':
            diagnostic=event.get('diagnostic',{})
            if diagnostic.get('state')=='LOGIN_ACTION_REQUIRED':return 'login_submission'
            if diagnostic.get('state')=='PHASE_STARTED':
                return {'checkpoint':'poc_checkpoint','setup':'whatsapp_setup','inspection':'operator_inspection','done':'finalization'}.get(diagnostic.get('stage'),current)
            if diagnostic.get('state')=='POC_CHECKPOINT':return 'poc_checkpoint'
            return current if current in {'poc_checkpoint','operator_inspection'} else 'whatsapp_setup'
        if event.get('tool_phase')=='requested' and event.get('tool_name') in {'inspect_whatsapp_setup','fill_setup_field','verify_whatsapp_setup','SetupReport',
            'observe_page','inspect_control','scroll_to_control','click_observed_control','fill_text','select_observed_option','verify_field'}:return 'whatsapp_setup'
        return super().stage(event,current)
    def context(self,result):
        facts=[{'kind':f['kind'],'event':f['event_index']} for f in result.get('step_facts',[])]
        cleanup={i for i,e in enumerate(self.events) if e.get('action_category')=='close'}
        guard={i for i,e in enumerate(self.events) if e.get('diagnostic',{}).get('state')=='GUARD_FAILURE'}
        context={**super().context(result),'authentication_verification':result.get('authentication_verification'),
            'submission_attribution':{'fact':'SUBMITTED_ONCE','workflow':'netcore.login',
                'operation':'LOGIN_CREDENTIAL_SUBMISSION_ONLY','source_tool':'submit_login',
                'event_refs':sorted({f['event'] for f in facts if f['kind']=='SUBMITTED_ONCE'}),
                'recorded_login_submissions':result.get('login_submissions',0),
                'setup_submission_action':'NOT_AUTHORIZED_OR_INVOKED',
                'instruction':'SUBMITTED_ONCE describes login credentials only. Never describe it as the WhatsApp Setup form or campaign being submitted. Setup preflight or field filling is not campaign submission; server persistence remains undetermined.'},
            'login_handoff_route':result.get('login_handoff_route','NOT_ESTABLISHED'),
            'setup_tab_status':result.get('setup_tab_status','NOT_OPENED'),'original_tab_retained':result.get('original_tab_retained',False),
            'poc_checkpoint':result.get('poc_checkpoint','NOT_ACCEPTED'),'setup_status':result.get('setup_status','BLOCKED'),
            'setup_gates':result.get('setup_gates',{}),'setup_gates_current':result.get('setup_gates_current',False),'explicit_commit_actions':0,
            'setup_preflight_failure':result.get('setup_failure_diagnostic',{'predicate_status':'NOT_RECORDED'}),
            'setup_preflight_scope':'NEGATIVE_AUTHENTICATION_STATES_AND_OBSERVATION_COVERAGE_ONLY',
            'setup_preflight_instruction':'Independent dashboard/tenant/JSON gaps are not Setup negative-state predicates. If the failed predicate was not recorded, report it as unknown; do not invent a login redirect, challenge or rejected authentication.',
            'failure_inspection':result.get('failure_inspection',{'status':'NOT_RECORDED'}),
            'server_persistence':result.get('persistence',{}),'automatic_graduation':False,
            'success_scope':'FOUR_SETUP_FIELDS_ONLY_UNSAVED_NOT_CAMPAIGN_CREATION',
            'required_citation_groups':self.citation_groups(facts,result['verification']==Result.SETUP_FILLED_UNSAVED,cleanup,guard),
            'citation_budget':6,
            'citation_instruction':'Cite at least one actual offered source event from EVERY required_citation_groups entry, including CLEANUP. Use at most six citations; never invent or append a citation.'}
        # Old saved packages retain their exact contract/digest. New scoped POC
        # records expose their uncertainty without altering the shared Teacher.
        for key in ('setup_observation','setup_verified_fields','setup_safety_status','mutation_authorization','closed_root_investigation','scoped_mutation_review','setup_failure_phase'):
            if key in result:context[key]=result[key]
        return context
    def validate(self,decision,package):
        if decision.outcome!=package.outcome:raise EvidenceError('OUTCOME_MISMATCH')
        if claims_setup_submission(decision.finding):raise EvidenceError('UNSUPPORTED_SETUP_SUBMISSION_CLAIM')
        if re.search(r'\b(?:campaign|message)\s+(?:(?:was|is|has been|successfully)\s+)*(?:created|saved|published|scheduled|activated|sent)\b|\b(?:created|saved|published|scheduled|activated|sent)\s+(?:a |the )?(?:campaign|message)\b',decision.finding,re.I):
            raise EvidenceError('UNSUPPORTED_CAMPAIGN_COMMIT_CLAIM')
        if decision.strategy_rule is not None or decision.decision=='optimize':raise EvidenceError('UNSUPPORTED_STRATEGY_RULE')
        if decision.decision=='no_change' and package.outcome!='success':raise EvidenceError('INCOMPLETE_SETUP_IS_NOT_SUCCESSFUL_NO_CHANGE')
        if decision.outcome=='success' and (package.verified_result!=Result.SETUP_FILLED_UNSAVED or
            package.module_evidence.get('poc_checkpoint')!='HUMAN_CONFIRMED_FOR_POC' or
            package.module_evidence.get('cleanup_result')!='CLOSED_AND_PROFILE_REMOVED' or
            not package.module_evidence.get('setup_gates') or not package.module_evidence.get('setup_gates_current') or
            any(v!='PASS' for v in package.module_evidence['setup_gates'].values())):
            raise EvidenceError('UNSUPPORTED_SETUP_SUCCESS')
        # Machine scope is authoritative. Also reject an explicit authentication claim
        # when the independent login gates did not actually establish it.
        if 'AUTHENTICATED_AND_VERIFIED' in decision.finding and package.module_evidence.get('authentication_verification')!=Result.AUTHENTICATED_AND_VERIFIED:
            raise EvidenceError('UNSUPPORTED_AUTHENTICATION_CLAIM')
        guard={i for c in package.evidence if c.data.get('state')=='GUARD_FAILURE' for i in c.events}
        cleanup={i for c in package.evidence if c.kind=='close' for i in c.events}
        groups=self.citation_groups(package.facts,package.outcome=='success',cleanup,guard)
        if package.module_evidence.get('required_citation_groups')!=groups:raise EvidenceError('CITATION_CONTRACT_MISMATCH')
        for kind,indices in groups.items():
            if not set(indices).intersection(decision.evidence_events):
                raise EvidenceError('UNCITED_CLEANUP' if kind=='CLEANUP' else 'UNCITED_GUARD_FAILURE' if kind=='GUARD_FAILURE' else 'UNCITED_COMPLETED_STEP')
        self.accepted_proposal(decision)
