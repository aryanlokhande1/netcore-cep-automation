"""Login-specific eligibility for another fresh authenticated session."""
from app.state import Result

def login_safety_stop(result,live=False):
    if result.get('login_submissions',0)>1 or result.get('submission_state')=='UNCERTAIN':return 'UNCERTAIN_SUBMISSION_STATE'
    if result.get('error'):return 'EXECUTION_ERROR'
    if result.get('cleanup_result')!='CLOSED_AND_PROFILE_REMOVED':return 'CLEANUP_UNCONFIRMED'
    if live and result.get('navigation_guard_status',{}).get('failed'):return 'NAVIGATION_GUARD_UNAVAILABLE'
    if live and result.get('approval_required_hosts'):return 'ORIGIN_POLICY_STOP'
    if result['verification'] in {Result.AUTH_FAILED,Result.SESSION_EXPIRED}:return 'AUTHENTICATION_SAFETY_STOP'
    observation=next((e['observation'] for e in reversed(result.get('_events',[])) if e.get('observation') is not None),{})
    if observation.get('challenge') or observation.get('rejected') or observation.get('expired') or observation.get('account_restricted'):return 'HUMAN_OR_ACCOUNT_ACTION_REQUIRED'
    if observation.get('allowed') is False:return 'ORIGIN_POLICY_STOP'
    if live and (result['verification']!=Result.AUTHENTICATED_AND_VERIFIED or result.get('login_submissions')!=1):
        return 'LIVE_STATE_NOT_INDEPENDENTLY_VERIFIED_NO_FURTHER_SUBMISSION'
    if live and result.get('teacher_status')!='ANALYZED':return 'TEACHER_ANALYSIS_FAILED_NO_FURTHER_SUBMISSION'
    return None
