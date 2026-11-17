"""Single deterministic lifecycle controller around genuine Deep Agents."""
import asyncio
import json
from openai import APIError, APIConnectionError, AuthenticationError, PermissionDeniedError, NotFoundError, RateLimitError
from pathlib import Path
from dataclasses import replace
from uuid import uuid4
from time import monotonic
from app.config import ROOT, OriginPolicy, ConfigurationError
from app.state import Result
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server, SCENARIOS
from modules.login.verifier import verify
from deep_agents.schemas import DEFAULT_TASK, validate_task, TeacherProposal
from deep_agents.browser_tools import BrowserTools
from deep_agents.doer import create_doer
from deep_agents.teacher import create_teacher
from deep_agents.offline import SyntheticChatModel
from deep_agents.models import openai_model
from deep_agents.permissions import InferenceBudget


def outcome_for(verification):
    if verification == Result.AUTHENTICATED_AND_VERIFIED:
        return 'success'
    if verification in {Result.NOT_TESTED,Result.HUMAN_ACTION_REQUIRED}:
        return 'blocked'
    return 'failure'

class ProposalValidationError(ValueError):
    """Only a fixed application code, never model text or validation inputs."""
    def __init__(self,code):
        self.code=code
        super().__init__(code)


def write_candidate(proposal,events,verification,run_id,directory=None):
    # Application writes only a bounded validated proposal, never executable instructions.
    if not isinstance(proposal,TeacherProposal):
        proposal=TeacherProposal.model_validate(proposal)
    if proposal.outcome != outcome_for(verification):
        raise ValueError('Teacher outcome contradicts deterministic verification')
    if any(index < 0 or index >= len(events) for index in proposal.evidence_events):
        raise ValueError('Teacher cited an event that does not exist')
    directory=directory or ROOT/'learning'/'pass1_candidates'
    directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{run_id}.md'
    text=('# Pass 1 candidate analysis\n\n'
        f'Run: {run_id}\nVerification: {verification}\n'
        f'Outcome: {proposal.outcome}\nChange required: {proposal.change_required}\n'
        f'Finding: {proposal.finding}\nProposed change: {proposal.proposed_change or "None"}\n'
        f'Evidence event indices: {proposal.evidence_events}\n'
        'Evaluation: analysis only; no candidate strategy has been applied.\n'
        'Accepted for execution: false. Approved skill changed: false.\n')
    path.write_text(text,encoding='utf-8')
    return path


async def capture_verification(browser,tools,result):
    """Current independent observation; historical snapshots never override a lost guard."""
    from browser.evidence_diagnostics import evidence_diagnostic,review_event
    trace=browser.trace
    trace.lifecycle('BROWSER',{'state':'FINAL_OBSERVATION_ATTEMPTED',
        'guard':browser.guard.snapshot() if browser.guard and hasattr(browser.guard,'snapshot') else None})
    try:
        observation=await browser.observe()
        verification=verify(observation)
        if tools.terminal_error and verification==Result.AUTHENTICATED_AND_VERIFIED:
            verification=Result.UNEXPECTED_PAGE
        result['verification']=str(verification)
        trace.add('verify',observation,verification)
        if verification==Result.AUTHENTICATED_AND_VERIFIED:trace.fact('DESTINATION_VERIFIED')
        details=browser.verification_details or {}
        result['reason_code']=details.get('reason',{
            Result.AUTHENTICATED_AND_VERIFIED:'VERIFIED_SUCCESS',Result.AUTH_FAILED:'AUTHENTICATION_REJECTED',
            Result.SESSION_EXPIRED:'SESSION_EXPIRED',Result.HUMAN_ACTION_REQUIRED:'HUMAN_ACTION_REQUIRED'}.get(verification,'UNEXPECTED_PAGE'))
        result['missing_evidence']=details.get('missing',[])
        if tools.terminal_error:
            result['verification']=str(Result.UNEXPECTED_PAGE)
            result['reason_code']='TERMINAL_BROWSER_POLICY_STOP'
    except Exception as error:
        result['verification']=str(Result.UNEXPECTED_PAGE)
        result['reason_code']='VERIFICATION_UNAVAILABLE'
        diagnostic=evidence_diagnostic(error,'FINAL_READ_ONLY_OBSERVATION',browser)
        diagnostic['last_observation_retained']=trace.current_observation is not None
        result['verification_diagnostic']=diagnostic
        result['missing_evidence']=['CURRENT_INDEPENDENT_OBSERVATION']
        review_event(browser,'FINAL_READ_ONLY_OBSERVATION',diagnostic)
    if browser.guard and (browser.guard.failed or browser.guard.blocking_hosts):
        result['verification']=str(Result.UNEXPECTED_PAGE)
        result['reason_code']='NAVIGATION_GUARD_UNAVAILABLE' if browser.guard.failed else 'UNAPPROVED_ORIGIN_APPROVAL_REQUIRED'


async def _execute_doer(browser,tools,task,model,budget,offline,skill_snapshot=None,candidate_evaluation=False,hold_seconds=0,diagnostics=False,*,after_login=None,login_timeout=None,close_session=True):
    trace=browser.trace
    result={'run_id':trace.run_id,'mode':'offline' if offline else 'openai','mocked':offline,
        'verification':str(Result.NOT_TESTED),'agent_report':None,'teacher_proposal':None,
        'error':None,'teacher_error':None,'reason_code':'NOT_TESTED','missing_evidence':[]}
    try:
        async with asyncio.timeout(login_timeout or browser.config.timeout_seconds):
            await browser.start()
            if getattr(browser,'evidence_review',False):
                try:
                    from browser.discovery import ReadOnlyDiscovery
                    browser.discovery=ReadOnlyDiscovery(browser)
                    await browser.discovery.start()
                except Exception as error:
                    from browser.evidence_diagnostics import evidence_diagnostic,review_event
                    print(json.dumps(review_event(browser,'DISCOVERY',evidence_diagnostic(error,'DISCOVERY',browser))),flush=True)
                    # Keep the monitor for bounded cleanup even after partial initialization.
            agent,permissions=create_doer(model,tools,trace,budget,offline=offline,
                skill_snapshot=skill_snapshot,candidate_evaluation=candidate_evaluation)
            context={'entry_url':tools.entry_url,'strategy':getattr(tools,'strategy',None).model_dump() if hasattr(tools,'strategy') else None}
            context_name='APPROVED_SYNTHETIC_CONTEXT' if browser.fixture else 'APPROVED_LIVE_CONTEXT'
            user_message=task+'\n\n'+context_name+'\n'+json.dumps(context)
            state=await agent.ainvoke({'messages':[{'role':'user','content':user_message}]},
                {'configurable':{'thread_id':trace.run_id},'recursion_limit':100})
            report=state.get('structured_response')
            if report is None:
                raise RuntimeError('MISSING_STRUCTURED_REPORT')
            result['agent_report']=report.model_dump()
            if skill_snapshot is not None:
                result['skill_digest']=skill_snapshot.digest
                result['skill_loaded']=any(f['kind']=='SKILL_READ' for f in trace.step_facts)
                checkpoint=agent.get_state({'configurable':{'thread_id':trace.run_id}}).values
                metadata=checkpoint.get('skills_metadata',state.get('skills_metadata'))
                result['native_skill_metadata_loaded']=bool(metadata)
                if not result['skill_loaded'] or not result['native_skill_metadata_loaded']:
                    raise RuntimeError('APPROVED_SKILL_NOT_LOADED')
    except TimeoutError:
        trace.add('error',error='BUDGET');result['error']='TIMEOUT'
    except (AuthenticationError,PermissionDeniedError,NotFoundError,RateLimitError,APIConnectionError,APIError) as error:
        trace.add('error',error='MODEL')
        categories={AuthenticationError:'OPENAI_AUTHENTICATION_FAILED',PermissionDeniedError:'OPENAI_ACCESS_DENIED',
            NotFoundError:'OPENAI_MODEL_UNAVAILABLE',RateLimitError:'OPENAI_QUOTA_OR_RATE_LIMIT',APIConnectionError:'OPENAI_CONNECTION_FAILED'}
        result['error']=next((code for kind,code in categories.items() if isinstance(error,kind)),'OPENAI_REQUEST_FAILED')
    except (KeyboardInterrupt,asyncio.CancelledError):
        trace.add('error',error='CANCELLED');result['error']='OPERATOR_CANCELLED'
    except Exception as error:
        from deep_agents.permissions import model_failure_details
        diagnostic=next((d for d in reversed(trace.model_diagnostics) if d.get('role')=='doer'),{})
        details=model_failure_details(error,diagnostic);details['failure_phase']='login_doer'
        trace.add('error',error='MODEL')
        result['error']=details['code'] if details['code']!='MODEL_OR_CONTROLLER_EXCEPTION' else 'EXECUTION_BLOCKED'
        result['execution_failure']=details
        trace.lifecycle('LOGIN_EXECUTION',{'state':'LOGIN_EXECUTION_FAILED',**details})
    finally:
        try:
            # Independent verification is captured before closing, even if the model stopped early.
            if getattr(browser,'page',None) is not None and tools.navigation_count:
                await capture_verification(browser,tools,result)
            if browser.guard and browser.guard.blocking_hosts:
                result['reason_code']='UNAPPROVED_ORIGIN_APPROVAL_REQUIRED'
                result['verification']=str(Result.UNEXPECTED_PAGE)
            if after_login is not None and result['error']!='OPERATOR_CANCELLED':
                # Application-owned continuation, before the one existing cleanup path.
                # Default login runs retain their original observation/hold behavior.
                await after_login(browser,tools,result)
            if diagnostics:
                from browser.diagnostics import ScreenshotDiagnostics
                result['diagnostic']=await ScreenshotDiagnostics(browser).capture(enabled=True)
                trace.add('diagnostic',result=result['verification'])
            if after_login is None and hold_seconds and result['error']!='OPERATOR_CANCELLED':
                try:
                    from browser.inspection import inspection_hold
                    print(f"Workflow result: {result['verification']} ({result['reason_code']}); login submissions: {browser.login_submissions}. Inspection does not resume login.")
                    if getattr(browser,'evidence_review',False) and result['verification']==str(Result.AUTHENTICATED_AND_VERIFIED) and browser.evidence and browser.evidence.reviewed:
                        print('Previously operator-reviewed evidence verified this fresh session; continuing automatically to Teacher.',flush=True)
                    elif getattr(browser,'evidence_review',False):
                        from browser.evidence_review import evidence_review_hold
                        if hasattr(browser,'begin_readonly_review'):browser.begin_readonly_review(hold_seconds)
                        reviewed=await evidence_review_hold(browser,hold_seconds)
                        if reviewed is not None:
                            result['verification']=str(verify(reviewed))
                            details=browser.verification_details or {}
                            result['reason_code']=details.get('reason','UNEXPECTED_PAGE')
                            result['missing_evidence']=details.get('missing',[])
                            if tools.terminal_error:
                                result['verification']=str(Result.UNEXPECTED_PAGE)
                                result['reason_code']='TERMINAL_BROWSER_POLICY_STOP'
                            elif browser.guard and browser.guard.blocking_hosts:
                                result['verification']=str(Result.UNEXPECTED_PAGE)
                                result['reason_code']='UNAPPROVED_ORIGIN_APPROVAL_REQUIRED'
                    else:
                        await inspection_hold(hold_seconds)
                except TimeoutError:
                    result['inspection_status']='HOLD_DEADLINE_REACHED'
                except (KeyboardInterrupt,asyncio.CancelledError):
                    result['error']='OPERATOR_CANCELLED'
                except Exception as error:
                    result['inspection_status']='HOLD_UNAVAILABLE'
                    from browser.evidence_diagnostics import evidence_diagnostic,review_event
                    result['inspection_diagnostic']=review_event(browser,'REVIEW',evidence_diagnostic(error,'REVIEW',browser))
            # Revalidate after review/hold, before close. Never resubmit or resume the Doer.
            if after_login is None and hold_seconds and getattr(browser,'page',None) is not None and tools.navigation_count:
                await capture_verification(browser,tools,result)
        except (Exception,asyncio.CancelledError,KeyboardInterrupt):
            result['verification']=str(Result.UNEXPECTED_PAGE)
            result['reason_code']='FINALIZATION_INTERRUPTED'
        if close_session:
            try:
                await browser.close()
            except Exception:
                if trace.cleanup_result not in {'CLOSED_AND_PROFILE_REMOVED','CLEANUP_FAILED','CLEANUP_UNCONFIRMED'}:
                    trace.cleanup_result='CLEANUP_UNCONFIRMED'
                result['cleanup_error']=trace.cleanup_result
            trace.final_outcome={'verification':result['verification'],'reason_code':result['reason_code'],
                'execution_error':result['error'],'cleanup_result':trace.cleanup_result}
            trace.add('outcome',result=result['verification'])
            trace.write(ROOT/'outputs')
    result['verification_snapshot']=trace.verification_snapshot
    result['cleanup_result']=trace.cleanup_result
    result['cleanup_diagnostic']=getattr(trace,'cleanup_diagnostic',None)
    result['step_facts']=list(trace.step_facts)
    result['strategy_version']=trace.strategy_version
    return result


async def review_completed(result,trace,model,budget,offline,history=None,*,persist=True,adapter=None):
    from deep_agents.review import review_evidence
    if adapter is None:
        from modules.login.review import LoginReviewAdapter
        adapter=LoginReviewAdapter()
    return await review_evidence(result,trace,model,budget,offline,history,adapter,
        persist_callback=write_candidate if persist else None)


def _finish_result(result,trace,browser,budget):
    result.update(model_requests=budget.requests,actual_api_requests=trace.openai_api_calls,
        max_output_tokens_per_request=0 if result['mocked'] else dict(trace.model_limits),
        login_submissions=browser.login_submissions,token_usage=dict(trace.token_usage))
    result['role_usage']=trace.role_usage
    result['role_evidence']=trace.role_evidence
    result['token_usage_available']=trace.token_usage_available
    result['model_diagnostics']=trace.model_diagnostics
    result['boundary_diagnostics']=trace.boundary_diagnostics
    result['last_recorded_observation']=trace.current_observation
    result['navigation_guard_status']={'failed':bool(browser.guard and browser.guard.failed),
        'failure':getattr(browser.guard,'failure_diagnostic',None) if browser.guard else None}
    facts={f['kind'] for f in trace.step_facts}
    result['submission_state']='COMPLETED' if 'SUBMITTED_ONCE' in facts else ('UNCERTAIN' if browser.login_submissions else 'NOT_ATTEMPTED')
    result['metrics']={'browser_actions':trace.actions,'duration_seconds':round(monotonic()-trace.started,3),
        'doer_duration_seconds':result.get('doer_duration_seconds',0),
        'redundant_inspections':sum(e.get('stop_code')=='INSPECT_BEFORE_NAVIGATION' for e in trace.events),
        'inspections':sum(e.get('tool_phase')=='started' and e.get('tool_name')=='inspect_page' for e in trace.events),
        'recovery_used':result.get('rediscovery_used',False)}
    trace.write(ROOT/'outputs')
    (ROOT/'outputs'/f'{trace.run_id}-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')


async def run_synthetic(config,task=DEFAULT_TASK,scenario='valid',mode='offline',api_request_budget=0,
                        review=False,doer_model=None,teacher_model=None,strategy=None,skill_store=None,
                        candidate_version=None,_evaluation=False,learning=False,diagnostics=False,
                        teacher_request_budget=4,history=None):
    from learning.policies import Strategy
    from learning.skill_storage.registry import SkillStore
    task=validate_task(task)
    if scenario not in SCENARIOS or mode not in {'offline','openai'}:
        raise ConfigurationError('Only known synthetic scenarios and offline/openai modes are supported')
    if config.teacher_live_replay_enabled:
        raise ConfigurationError('Teacher live replay is forbidden')
    if mode=='openai' and not 1<=api_request_budget<=8:
        raise ConfigurationError('Actual inference requires an explicitly approved API request budget from 1 to 8')
    if mode=='openai' and (doer_model is not None or teacher_model is not None):
        raise ConfigurationError('Actual inference cannot use injected/offline model replacements')
    if candidate_version and not _evaluation:
        raise ConfigurationError('Candidate skills require controlled evaluation')
    offline=mode=='offline'
    cap=32 if offline else api_request_budget
    teacher_cap=teacher_request_budget if offline else min(teacher_request_budget,cap-1)
    budget=InferenceBudget(cap,role_limits={'doer':cap-teacher_cap if review else cap,'teacher':teacher_cap})
    doer_model=doer_model or (SyntheticChatModel(role='doer') if offline else openai_model('doer'))
    teacher_model=teacher_model or (SyntheticChatModel(role='teacher') if offline else openai_model('teacher')) if review else None
    store=skill_store or SkillStore()
    snapshot=store.snapshot(candidate_version,evaluation=_evaluation)
    strategy=strategy or (Strategy.model_validate(snapshot.metadata['strategy']) if snapshot else Strategy())
    trace=Trace(strategy_version=strategy.version)
    trace.reasoner_mode='deepagents_offline_test_double' if offline else 'deepagents_openai_gpt-6-luna'
    with fixture_server() as server:
        browser=StagehandClient(config,OriginPolicy({server.base}),trace,fixture=True)
        tools=BrowserTools(browser,server.base+'/start?scenario='+scenario,strategy=strategy)
        result=await _execute_doer(browser,tools,task,doer_model,budget,offline,snapshot,_evaluation,diagnostics=diagnostics)
        result['rediscovery_used']=tools.recovery_used
        result['doer_duration_seconds']=round(monotonic()-trace.started,3)
        _finish_result(result,trace,browser,budget)
        if review and result['error']!='OPERATOR_CANCELLED':
            await review_completed(result,trace,teacher_model,budget,offline,history)
        result.update(source='synthetic',scenario=scenario,rediscovery_used=tools.recovery_used)
        _finish_result(result,trace,browser,budget)
    if learning:
        from learning.autonomous import process_learning
        result['learning']=await process_learning(config,result,trace.events,store=store,strategy=strategy)
        (ROOT/'outputs'/f'{trace.run_id}-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


async def run_live(config,task=DEFAULT_TASK,authorized=False,api_request_budget=0,
                   extra_origins=(),hold_seconds=0,diagnostics=False,teacher_request_budget=2,
                   strategy=None,history=None,learning=True,evidence_review=False,token_pool=None,review=True):
    from app.safety import authorize_live
    from modules.login.credentials import LocalCredentials,CredentialBroker
    from modules.login.evidence import EvidenceConfig
    from deep_agents.live_tools import LiveBrowserTools
    from learning.policies import Strategy
    from learning.skill_storage.registry import SkillStore
    task=validate_task(task)
    permit=authorize_live(config,authorized,api_request_budget,extra_origins)
    if not review and (learning or teacher_request_budget):
        raise ConfigurationError('Disabled Teacher review requires learning disabled and zero Teacher budget')
    if review and not 1<=teacher_request_budget<api_request_budget:
        raise ConfigurationError('Reserve a separate Teacher budget within the per-run total')
    if evidence_review and (not hold_seconds or not config.evidence_file):
        raise ConfigurationError('Evidence review requires an inspection hold and a configured evidence file')
    if hold_seconds:
        import sys
        if not sys.stdin.isatty() or not 1<=hold_seconds<=300:
            raise ConfigurationError('Inspection hold needs an interactive terminal and 1-300 seconds')
    from app.model_settings import ModelSettings
    settings=ModelSettings.load()
    if teacher_request_budget>settings.teacher_requests:raise ConfigurationError('Teacher request budget exceeds configured cap')
    credentials=LocalCredentials.load()
    evidence=EvidenceConfig.for_session(config.evidence_file,review_mode=evidence_review)
    doer_model=openai_model('doer',settings)
    teacher_model=openai_model('teacher',settings) if review else None
    # Availability preflight happens only after explicit live authorization and before launch.
    try:
        await doer_model.root_async_client.models.retrieve('gpt-6-luna')
    except Exception:
        raise ConfigurationError('Live model availability could not be established; browser was not launched') from None
    store=SkillStore()
    snapshot=store.snapshot()
    strategy=strategy or (Strategy.model_validate(snapshot.metadata['strategy']) if snapshot else Strategy())
    trace=Trace(strategy_version=strategy.version)
    trace.reasoner_mode='deepagents_openai_gpt-6-luna'
    budget=InferenceBudget(api_request_budget,role_limits={'doer':api_request_budget-teacher_request_budget,'teacher':teacher_request_budget})
    budget.token_pool=token_pool
    budget.max_input_tokens=settings.cycle_input;budget.max_output_tokens=settings.cycle_output
    doer_model.bind_meter(trace,budget,'doer')
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),trace,fixture=False,evidence=evidence,live_permit=permit)
    broker=CredentialBroker(browser,credentials)
    tools=LiveBrowserTools(browser,broker)
    tools.strategy=strategy
    if evidence_review:
        browser.evidence_review=True
    result=await _execute_doer(browser,tools,task,doer_model,budget,False,snapshot,hold_seconds=hold_seconds,diagnostics=diagnostics)
    result['review_previous_strategy']=strategy.model_dump()
    print('LIVE DOER COMPLETED: '+result['verification']+'; cleanup='+trace.cleanup_result,flush=True)
    result['rediscovery_used']=tools.recovery_used
    result['doer_duration_seconds']=round(monotonic()-trace.started,3)
    _finish_result(result,trace,browser,budget)
    result.update(source='live',rediscovery_used=tools.recovery_used,
        approval_required_hosts=sorted(browser.guard.blocking_hosts) if browser.guard else [],
        blocked_documents=list(browser.guard.blocked_documents) if browser.guard else [])
    if diagnostics:
        # Live capture is deliberately denied; no credential/account pixels can leave Chromium.
        result['diagnostic']={'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}
    if result['error']=='OPERATOR_CANCELLED':
        trace.teacher_result='DEFERRED_OPERATOR_CANCELLED'
        result['teacher_status']=trace.teacher_result
    elif review:
        await review_completed(result,trace,teacher_model,budget,False,history)
        print('TEACHER REVIEW COMPLETED: '+result['teacher_status'],flush=True)
    else:
        trace.teacher_result='DISABLED_LOGIN_ONLY'
        result['teacher_status']=trace.teacher_result
    _finish_result(result,trace,browser,budget)
    from modules.login.policy import login_safety_stop
    stop=login_safety_stop(result,True)
    if stop:print('LIVE VERIFICATION INCOMPLETE — NO FURTHER LIVE ATTEMPT: '+stop,flush=True)
    from learning.autonomous import process_learning
    if learning:
        result['learning']=await process_learning(config,result,trace.events,store=store,strategy=strategy)
    (ROOT/'outputs'/f'{trace.run_id}-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


async def evaluate_synthetic(config,task=DEFAULT_TASK):
    results=[]
    for scenario in ('valid','invalid','already','expired','changed','challenge','wrong_tenant','unknown'):
        result=await run_synthetic(config,task,scenario,mode='offline',review=True,_evaluation=True)
        results.append({'scenario':scenario,**result})
    return results
