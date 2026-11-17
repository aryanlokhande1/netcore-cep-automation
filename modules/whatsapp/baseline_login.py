"""Login-only adapter; the existing controller owns inference, not final cleanup."""
from modules.whatsapp.browser_baseline import BaselineStop

SECURITY_STOP_REASONS=frozenset({'INCORRECT_TENANT','AUTHENTICATION_REJECTED','ACCOUNT_RESTRICTED',
    'SESSION_EXPIRED','BROWSER_SESSION_EXPIRED','TERMINAL_BROWSER_POLICY_STOP','NAVIGATION_GUARD_UNAVAILABLE',
    'UNAPPROVED_ORIGIN_APPROVAL_REQUIRED'})


async def validate_handoff(browser,login_result):
    from modules.whatsapp.session import healthy_observation
    from modules.whatsapp.contract import SetupStop
    if not isinstance(login_result,dict):raise BaselineStop('LOGIN_RESULT_REQUIRED')
    if login_result.get('error'):
        raise BaselineStop('LOGIN_DOER_STOPPED',login_error=login_result['error'])
    if login_result.get('reason_code') in SECURITY_STOP_REASONS:
        raise BaselineStop('AUTHENTICATION_OR_SECURITY_STOP')
    if browser.close_started:raise BaselineStop('LOGIN_BROWSER_ALREADY_CLOSING')
    # A known negative cannot disappear during a later read and become consent.
    for event in browser.trace.events:
        observation=event.get('observation') or {}
        if any(observation.get(k) is True for k in ('challenge','rejected','expired','account_restricted')):
            raise BaselineStop('AUTHENTICATION_OR_SECURITY_STOP')
        if observation.get('allowed') is False:raise BaselineStop('LOGIN_POLICY_STOP')
        if event.get('tool_phase')=='denied' or event.get('tool_name')=='FORBIDDEN':
            raise BaselineStop('LOGIN_POLICY_STOP')
    observation=await browser.observe()
    if (getattr(browser,'verification_details',None) or {}).get('reason') in SECURITY_STOP_REASONS:
        raise BaselineStop('AUTHENTICATION_OR_SECURITY_STOP')
    try:return healthy_observation(browser,observation)
    except SetupStop as error:raise BaselineStop(error.code) from None


async def execute_login(browser,tools,model,budget,*,offline=False,login_seconds=180,skill_snapshot=None):
    """The same Login Doer as app.cli; never run_live/Teacher or another browser."""
    from deep_agents.controller import _execute_doer
    from deep_agents.schemas import DEFAULT_TASK
    from langsmith import tracing_context
    if offline:
        from app.safety import require_fixture_browser
        require_fixture_browser(browser)
    elif browser.fixture:raise BaselineStop('LIVE_LOGIN_REQUIRES_REAL_PROVIDER')
    if browser.browser is not None or browser.login_submissions or getattr(browser,'login_closed',False):
        raise BaselineStop('LOGIN_ATTEMPT_ALREADY_STARTED')
    try:
        with tracing_context(enabled=False):
            result=await _execute_doer(browser,tools,DEFAULT_TASK,model,budget,offline,skill_snapshot,
                login_timeout=login_seconds,close_session=False)
    finally:
        # A failed/uncertain submission is consumed. No automatic manual fallback,
        # second Doer, credential retry or later Setup action can reopen login.
        browser.login_closed=True
        tools.references.clear()
    if tools.terminal_error:raise BaselineStop('LOGIN_TERMINAL_POLICY_STOP')
    route=await validate_handoff(browser,result)
    print('LOGIN_DOER_COMPLETED: HUMAN_C_CHECKPOINT_REQUIRED',flush=True)
    return {'error':None,'verification':result['verification'],'reason_code':result['reason_code'],
        'missing_evidence':list(result.get('missing_evidence',[])),'route':route,
        'login_submissions':browser.login_submissions,'model_requests':budget.requests,
        'actual_api_requests':browser.trace.openai_api_calls}
