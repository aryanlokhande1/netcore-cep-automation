"""Login interpretation of the generic Teacher review contract."""
from app.state import Result,Observation
from deep_agents.schemas import TeacherProposal
from deep_agents.review_contract import EvidenceError

RULES=('prefer_semantic_roles','reinspect_before_submit','verify_after_action','wait_for_stable_controls')
class LoginReviewAdapter:
    identity={'module':'netcore.login','task':'authenticate_and_verify_destination',
        'expected_outcomes':['AUTHENTICATED_AND_VERIFIED'],'supported_rules':list(RULES)}
    def outcome(self,result):
        return 'success' if result==Result.AUTHENTICATED_AND_VERIFIED else 'blocked' if result in {Result.NOT_TESTED,Result.HUMAN_ACTION_REQUIRED} else 'failure'
    def stage(self,event,current):
        if event.get('tool_phase')=='requested':
            return {'navigate_approved':'navigation','inspect_page':'observation','submit_login':'submission',
                'verify_dashboard':'verification','DoerReport':'finalization'}.get(event.get('tool_name'),current)
        return current
    def observation(self,data):return Observation.model_validate(data).model_dump()
    def context(self,result):
        from deep_agents.evidence import facts_only
        return {'guard_failure':facts_only((result.get('navigation_guard_status') or {}).get('failure') or {}),'login_submissions':result.get('login_submissions'),'submission_state':result.get('submission_state','NOT_RETAINED'),
            'cleanup_result':result.get('cleanup_result','NOT_RETAINED'),
            'verification_snapshot_available':bool(result.get('verification_snapshot'))}

    def validate(self,decision,package):
        if decision.outcome!=package.outcome:raise EvidenceError('OUTCOME_MISMATCH')
        if decision.strategy_rule is not None and decision.strategy_rule not in RULES:raise EvidenceError('UNSUPPORTED_STRATEGY_RULE')
        if package.verified_result!=Result.AUTHENTICATED_AND_VERIFIED and package.reason_code in {'NAVIGATION_GUARD_UNAVAILABLE','VERIFICATION_UNAVAILABLE'}:
            if decision.decision not in {'evidence_gap','repair','insufficient_evidence'}:raise EvidenceError('UNSUPPORTED_OPTIMIZATION_OR_SUCCESS')
        if decision.decision=='no_change' and package.outcome!='success':raise EvidenceError('INCOMPLETE_WORKFLOW_IS_NOT_SUCCESSFUL_NO_CHANGE')
        # Machine status, not model prose, controls acceptance and skill certification.
        if decision.outcome=='success' and package.verified_result!=Result.AUTHENTICATED_AND_VERIFIED:raise EvidenceError('UNSUPPORTED_AUTHENTICATION_CLAIM')
        offered_guard={i for c in package.evidence if c.data.get('state')=='GUARD_FAILURE' for i in c.events}
        if offered_guard and not offered_guard.intersection(decision.evidence_events):raise EvidenceError('UNCITED_GUARD_FAILURE')
        for kind in ('LOGIN_RECOGNIZED','SUBMITTED_ONCE'):
            facts={f['event'] for f in package.facts if f['kind']==kind}
            if facts and not facts.intersection(decision.evidence_events):raise EvidenceError('UNCITED_COMPLETED_STEP')
        TeacherProposal.model_validate(self.proposal(decision))
    def accepted_proposal(self,decision):return TeacherProposal.model_validate(self.proposal(decision))
    def offline_proposal(self,response):return TeacherProposal.model_validate(response)
    proposal_fields=set(TeacherProposal.model_fields)

    def proposal(self,d):
        return {'outcome':d.outcome,'change_required':d.decision=='optimize','finding':d.finding,
            'proposed_change':d.proposed_change if d.decision=='optimize' else '',
            'evidence_events':d.evidence_events,'strategy_rule':d.strategy_rule,
            'analysis_kind':'evidence_gap' if d.decision in {'evidence_gap','insufficient_evidence','repair'} else 'no_change' if d.decision in {'no_change','reject_optimization'} else 'policy',
            'worked':'','inefficiency':'','comparison':''}
