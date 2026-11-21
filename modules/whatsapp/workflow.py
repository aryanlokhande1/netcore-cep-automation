"""Canonical V2 graph lifecycle; guarded historical Phase 1 source retained below."""
import asyncio
from dataclasses import replace
import json
import re
from pathlib import Path
from time import monotonic
from app.config import ROOT,OriginPolicy,ConfigurationError
from app.safety import authorize_live
from app.state import Result
from app.trace import Trace
from deep_agents.controller import _execute_doer,_finish_result
from modules.whatsapp.login_handoff import HandoffLoginTools
from deep_agents.models import openai_model
from deep_agents.schemas import DEFAULT_TASK
from deep_agents.review import review_evidence
from learning.policies import Strategy
from modules.whatsapp.contract import Phase1Limits,Page1Limits,SetupStop,ORDER
from modules.whatsapp.session import TaskSession,Phase1Budget,PocGrant,confirm_poc,healthy_observation,source_signature
from modules.whatsapp.tools import SetupTools
from modules.whatsapp.agent import create_setup_doer
from modules.whatsapp.persistence import PersistenceObserver
from modules.whatsapp.review import WhatsAppReviewAdapter


_SAFE_FAILURE_CODE = re.compile(r"[A-Z][A-Z0-9_]{0,79}\Z")
_SAFE_FAILURE_STAGE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")
_SAFE_FAILURE_OWNERS = {
    "authentication", "authorization", "browser", "controller",
    "model", "recognition", "tool", "uncertain_write",
}


def diagnostic_failure_metadata(error, failing_stage):
    """Keep stable failure codes and stages; never serialize exception messages."""
    code = getattr(error, "code", None)
    timeout_owner = None
    operation = getattr(error, "guard_method", None)
    if isinstance(error, TimeoutError) and code is None:
        if isinstance(operation, str) and re.fullmatch(
            r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+", operation
        ):
            code, timeout_owner = "BROWSER_COMMAND_TIMEOUT", "browser"
        else:
            timeout_codes = {
                "setup_observation": ("SETUP_OBSERVATION_TIMEOUT", "recognition"),
                "post_observation_protection": ("SETUP_HANDOFF_TIMEOUT", "browser"),
                "setup_navigation": ("SETUP_NAVIGATION_TIMEOUT", "browser"),
                "provider_model_configuration": ("PROVIDER_MODEL_CONFIGURATION_TIMEOUT", "model"),
                "doer_construction_and_registration": ("DOER_CONSTRUCTION_TIMEOUT", "tool"),
                "task_doer_invocation": ("WHATSAPP_DOER_INVOCATION_TIMEOUT", "model"),
            }
            code, timeout_owner = timeout_codes.get(
                failing_stage, ("DIAGNOSTIC_OPERATION_TIMEOUT", "controller")
            )
    if code is None and isinstance(error, RuntimeError):
        candidate = str(error)
        if candidate in {
            "OUTPUT_LIMIT_INCOMPLETE",
            "PROVIDER_RESPONSE_INCOMPLETE",
            "MODEL_BUDGET_EXHAUSTED",
            "TOTAL_TOKEN_BUDGET_EXHAUSTED",
            "FINAL_TEACHER_TOKEN_RESERVE",
            "DOER_TOOL_CALL_BUDGET_EXHAUSTED",
            "MODEL_BOUNDARY_CONTEXT_BUDGET_OR_HISTORY",
        }:
            code = candidate
    defect=isinstance(error,(TypeError,AttributeError,KeyError,NameError,NotImplementedError))
    if defect:code='CODE_FIX_REQUIRED'
    if not isinstance(code, str) or not _SAFE_FAILURE_CODE.fullmatch(code):
        code = "BROWSER_PERMISSION_DENIED" if isinstance(error,PermissionError) else "WORKFLOW_EXCEPTION"
    stage = failing_stage if isinstance(failing_stage, str) and _SAFE_FAILURE_STAGE.fullmatch(failing_stage) else "workflow"
    owner = getattr(error, "owner", None)
    if owner not in _SAFE_FAILURE_OWNERS:
        if timeout_owner is not None:
            owner = timeout_owner
        elif isinstance(error, SetupStop) and code.startswith("CONTROL_IDENTITY_"):
            owner = "recognition"
        elif isinstance(error, SetupStop) and code.startswith("PROTECTED_"):
            owner = "browser"
        else:
            owner = "tool" if defect else "controller"
    exception_class = type(error).__name__
    if exception_class=='GraphRecursionError':code='DOER_REASONING_TURN_LIMIT'
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", exception_class):
        exception_class = "RuntimeError"
    failure = {
        "code": code,
        "owner": owner,
        "failing_stage": stage,
        "exception_class": exception_class,
    }
    if isinstance(operation, str) and re.fullmatch(
        r"[A-Za-z][A-Za-z0-9]*(?:\.[A-Za-z][A-Za-z0-9]*)+", operation
    ):
        failure["operation"] = operation
    budget_kind = getattr(error, "budget_kind", None)
    if budget_kind in {"ACTION_LIMIT", "SESSION_DEADLINE", "REVIEW_DEADLINE"}:
        failure["budget_kind"] = budget_kind
        for attribute, key in (("actions_used", "actions_used"), ("actions_limit", "actions_limit")):
            value = getattr(error, attribute, None)
            if type(value) is int and value >= 0:
                failure[key] = value
    return failure


def human_assisted_timeout_metadata(error,failing_stage,*,session_deadline_expired):
    failure=diagnostic_failure_metadata(error,failing_stage)
    if session_deadline_expired:
        failure.update(code='HUMAN_ASSISTED_SESSION_TIMEOUT',owner='controller')
    return failure


def teacher_factual_contradiction(prose,actual,authentication_verified,*,task_invoked=None):
    """Conservative prose guard; deterministic facts remain authoritative."""
    if not authentication_verified and re.search(
        r'\bauthentication (?:occurred|succeeded|passed)|\b(?:login|session|tenant|account) (?:was|is|has been) (?:independently )?authenticated\b|\bauthenticated (?:successfully|and verified)\b',prose,re.I):
        return 'TEACHER_ASSERTED_UNVERIFIED_AUTHENTICATION'
    if task_invoked is None:task_invoked=bool(actual['task_tool_calls'])
    if not task_invoked and re.search(
        r'\bwhatsapp doer (?:executed|ran|started|was invoked)|\btask doer (?:executed|ran|was invoked)\b',prose,re.I):
        return 'TEACHER_ASSERTED_UNOBSERVED_TASK_EXECUTION'
    if not actual['task_tool_calls'] and re.search(
        r'\b(?:whatsapp|task) doer (?:clicked|typed|filled|selected|used browser|invoked browser)\b',prose,re.I):
        return 'TEACHER_ASSERTED_UNOBSERVED_TASK_BROWSER_EXECUTION'
    return None


def task_invocation_observed(events):
    from deep_agents.evidence import source_events
    activities=[event.get('agent_execution') or event for event in source_events(events)]
    return any(e.get('kind')=='task_doer_invoked' or
        e.get('kind')=='model_invocation' and e.get('phase')=='task_doer' for e in activities)


def login_execution_stop(result):
    from modules.whatsapp.contract import V2Stop
    code=result.get('error') or 'LOGIN_TERMINAL_POLICY_STOP'
    owner='model' if code in {'OUTPUT_LIMIT_INCOMPLETE','PROVIDER_RESPONSE_INCOMPLETE','MISSING_STRUCTURED_REPORT'} or code.startswith('OPENAI_') else 'controller'
    return V2Stop(code,owner=owner,unsafe=True)


def request_usage(trace,start=0,stop=None,*,role='doer'):
    """Stage totals exist only when every request in that stage reported usage."""
    records=[d for d in trace.model_diagnostics[start:stop] if d.get('role')==role and d.get('stage')=='MODEL_CALL']
    result={'requests':len(records)}
    for key in ('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens'):
        result[key]=(sum(d[key] for d in records) if records and
            all(d.get('usage_recorded') is True and type(d.get(key)) is int for d in records) else None)
    return result


def validate_human_assisted_authorization(args,configured_request_cap):
    """Require explicit one-run permissions and allocate only the fixed request caps."""
    required=('authorized_live','authorized_inference','authorized_candidate_evaluation')
    if any(getattr(args,name,False) is not True for name in required):
        raise ConfigurationError('Explicit live, inference and candidate-evaluation authorization is required')
    if type(configured_request_cap) is not int or configured_request_cap < 1:
        raise ConfigurationError('Configured request cap is invalid')
    if getattr(args,'authorized_learning',False) or hasattr(args,'total_api_budget'):
        teacher=int(getattr(args,'authorized_learning',False))
        total=min(getattr(args,'total_api_budget',20),configured_request_cap)
        login=getattr(args,'login_api_budget',6)
        task=getattr(args,'task_api_budget',12)
        if any(type(n) is not int or n<1 for n in (total,login,task)) or total<2+teacher:
            raise ConfigurationError('Learning requires finite Login, task and reserved Teacher requests')
        login=min(login,total-1-teacher);task=min(task,total-login-teacher)
        return {'login':login,'task':task,'observer':0,'teacher':teacher,'total':login+task+teacher}
    total=min(12,configured_request_cap)
    if total<2:
        raise ConfigurationError('Configured request cap must allow Login and WhatsApp Doer requests')
    login=min(6,max(1,(total*6+11)//12))
    task=min(4,max(1,(total*4+11)//12))
    if login+task>total:
        login,total_for_task=total-1,1
        task=total_for_task
    observer=min(2,total-login-task)
    return {'login':login,'task':task,'observer':observer,'total':total}


def human_assisted_output_budget(settings, caps, *, writable=False):
    reserved = caps['login'] * settings.login_output + caps['teacher'] * settings.teacher_output
    task_output = min(2048, (settings.cycle_output - reserved) // caps['task'])
    minimum = 1536 if writable else 1
    if task_output < minimum:
        required = reserved + caps['task'] * minimum
        raise ConfigurationError(
            f'LEARNING_MAX_OUTPUT_TOKENS must be at least {required} for the selected request plan '
            f'with {minimum} output tokens per Setup request and full Login and Teacher reserves.')
    return task_output


def human_assisted_login_evidence(path):
    from browser.evidence_diagnostics import EvidenceFailure
    from modules.login.evidence import EvidenceConfig

    if not path:
        return None, 'LOGIN_EVIDENCE_FILE_NOT_CONFIGURED'
    try:
        evidence = EvidenceConfig.load(path, allow_unreviewed=True)
    except EvidenceFailure as error:
        if error.code in {'MISSING_EVIDENCE_CONFIGURATION', 'UNREVIEWED_IDENTITY_MAPPING'}:
            return None, error.code
        return None, 'INVALID_LOGIN_EVIDENCE_DOCUMENT'
    except OSError:
        return None, 'LOGIN_EVIDENCE_FILE_UNREADABLE'
    except (TypeError, ValueError):
        return None, 'INVALID_LOGIN_EVIDENCE_DOCUMENT'
    if not evidence.reviewed:
        return None, 'LOGIN_EVIDENCE_DOCUMENT_UNREVIEWED'
    return evidence, 'REVIEWED_LOGIN_EVIDENCE_LOADED'


def human_assisted_browser_config(config,*,session_seconds,evidence_file,browser_actions=300):
    """Keep operator review visible and bound to the diagnostic's single session."""
    if type(browser_actions) is not int or not 30<=browser_actions<=1000:
        raise ConfigurationError('Browser action budget must be 30-1000')
    return replace(config,headless=False,max_actions=browser_actions,
        timeout_seconds=session_seconds,evidence_file=evidence_file)


def human_assisted_remaining_seconds(session_seconds,started,*,now=None):
    elapsed=(monotonic() if now is None else now)-started
    return max(0,session_seconds-max(0,elapsed))


def require_setup_observation(form):
    from modules.whatsapp.contract import V2Stop
    if not isinstance(form,dict) or form.get("stage")!="setup" or form.get("stage_visible") is not True:
        raise V2Stop("WHATSAPP_SETUP_FORM_NOT_VERIFIED",owner="recognition")
    return form


def human_assisted_resource_summary(options):
    return {
        key:options.get(key) for key in ('status','digest','meaning')
    } | {
        'options':[
            {key:item.get(key) for key in ('option','resource','key','value_sha256')}
            for item in options.get('options',[])[:9]
        ]
    }


async def human_assisted_navigation_candidates(review):
    candidates=await review.operator_candidates('navigation')
    if candidates:
        return candidates,False
    samples=await review.operator_candidates('sample')
    links=[candidate for candidate in samples
        if candidate.get('visible') is True and
        (candidate.get('element_type')=='a' or candidate.get('role') in {'link','menuitem'})]
    return links,True


def human_assisted_route(observation,*,diagnostic_consent,session_safe):
    """Route only independent verifier success to the verified graph branch."""
    from modules.login.verifier import verify
    if observation is None or not session_safe or not observation.allowed or not observation.expected_host:
        return 'BLOCKED'
    if any(getattr(observation,name) for name in
            ('credential_form','challenge','rejected','expired','account_restricted','login_entry')):
        return 'BLOCKED'
    if verify(observation)==Result.AUTHENTICATED_AND_VERIFIED:
        return 'VERIFIED'
    return 'DIAGNOSTIC' if diagnostic_consent else 'BLOCKED'


def operator_tenant_route_allowed(url):
    from urllib.parse import unquote, urlsplit
    from modules.whatsapp.contract import SETUP_URL

    try:
        current, setup = urlsplit(url), urlsplit(SETUP_URL)
        boundary = setup.path.split('/campaign/', 1)[0]
        decoded = unquote(current.path)
        return (current.scheme == 'https' and current.netloc == setup.netloc and
                (current.path == boundary or current.path.startswith(boundary + '/')) and
                not any(part in {'.', '..'} for part in decoded.split('/')) and
                '\\' not in decoded and not any(ord(char) < 32 for char in url))
    except (TypeError, ValueError):
        return False


async def human_assisted_session_safety(browser, *, operator_route=False):
    """Observable ownership predicates, separate from production authentication.

    Keep URL admission conservative. A query rejection is not evidence of lost
    ownership and cannot be silently discharged from historical UI appearance.
    """
    from browser.discovery import login_page_context
    from urllib.parse import urlsplit
    current=await login_page_context(browser)
    first=await browser.page.url()
    active=await browser.browser.context.active_page() if browser.browser else None
    last=await browser.page.url()
    final=await login_page_context(browser)
    identity_keys=('primary_target_alias','primary_session_alias','generation',
        'document_generation','page_path_sha256')
    identity=lambda c:tuple(c.get(k) for k in identity_keys)
    parsed=urlsplit(last);guard=browser.guard
    from modules.whatsapp.contract import SETUP_URL
    tenant=urlsplit(SETUP_URL).path.strip('/').split('/')[0]
    lifecycle=browser.lifecycle_context()
    predicates={
        'owned_process_alive':lifecycle.get('process_state')=='ALIVE' and not lifecycle.get('owned_close_started',True),
        'owned_document_current':current.get('primary_document_coherent') is True
            and final.get('primary_document_coherent') is True and identity(current)==identity(final) and first==last,
        'authorized_origin':final.get('expected_host') is True and browser.policy.permits(last),
        'authorized_tenant_route': (operator_tenant_route_allowed(last) if operator_route else
            bool(tenant) and parsed.path.strip('/').split('/')[0]==tenant),
        'unambiguous_url':not parsed.username and not parsed.password and not parsed.fragment,
        'guard_healthy':guard is not None and not guard.failed and not guard.blocking_hosts,
        'protected_page_active':bool(active and browser.login_page and
            active.page_id==browser.login_page.page_id==browser.page.page_id and final.get('sdk_page_active')),
    }
    failures=[k for k,v in predicates.items() if not v]
    return {'diagnostic_session_safe':not failures,'predicates':predicates,
        'failed_predicates':failures,'query_present':bool(parsed.query),
        'page_context':final,'authentication_grant':False}


def human_assisted_run_preconditions(context):
    if not context.get('session_safe'):return 'CURRENT_AUTHORIZED_DOCUMENT_NOT_PROVEN'
    if not context.get('selected_navigation'):return 'OPERATOR_OBSERVED_NAVIGATION_REQUIRED'
    if context['selected_navigation'].get('state')!='CURRENT_VISIBLE_DOM_REFERENCE':
        return 'CANDIDATE_REFERENCE_STALE'
    observation=context.get('observation')
    if observation is not None and human_assisted_route(observation,
            diagnostic_consent=True,session_safe=True)=='BLOCKED':
        return 'DIAGNOSTIC_SECURITY_STATE_UNRESOLVED'
    return None


def request_diagnostic_consent(console,*,timeout_seconds,maximum_seconds):
    selected=console.choose('whatsapp_diagnostic_consent',
        {'yes':'YES â€” run the supervised read-only WhatsApp Doer diagnostic',
         'no':'NO â€” do not start the WhatsApp Doer diagnostic'},
        timeout_seconds,maximum_seconds,default='no')
    return selected=='yes'


def human_assisted_prompt_limits(deadline,*,now=None):
    remaining=int(deadline-(monotonic() if now is None else now))
    if remaining<1:
        raise TimeoutError('Operator review deadline expired')
    return min(300,remaining),min(600,remaining)


def build_human_assisted_graph(login_node,verified_doer,diagnostic_doer,blocked_node,finish_node,*,trace=None):
    from typing import TypedDict
    from langgraph.graph import StateGraph,START,END

    class State(TypedDict,total=False):
        route:str
        authentication_verified:bool
        diagnostic_consent:bool
        status:str
        failure:dict

    def recorded(name,callback):
        async def invoke(state):
            started=monotonic()
            if trace:trace.agent_event('graph_node_started',role='controller',node=name)
            try:result=await callback(state)
            except BaseException as error:
                if trace:
                    failure=diagnostic_failure_metadata(error,name)
                    details={key:failure[key] for key in
                        ('operation','budget_kind','actions_used','actions_limit') if key in failure}
                    trace.agent_event('graph_node_finished',role='controller',node=name,
                        status='FAILED',error=type(error).__name__,error_code=failure['code'],
                        failing_stage=failure['failing_stage'],owner=failure['owner'],
                        **details,
                        duration_ms=round((monotonic()-started)*1000,3))
                raise
            if trace:
                trace.agent_event('graph_node_finished',role='controller',node=name,
                    status=result.get('status'),duration_ms=round((monotonic()-started)*1000,3))
                if name!='login':trace.agent_event('graph_route',role='controller',source=name,
                    destination='end' if name=='finish' else 'finish',reason='NODE_COMPLETED')
            return result
        return invoke
    routes={'VERIFIED':'verified_doer','OPERATOR_CONFIRMED':'verified_doer','DIAGNOSTIC':'diagnostic_doer','BLOCKED':'blocked','VERIFIED_ONLY':'finish'}
    def select_route(state):
        route=state.get('route','BLOCKED')
        if trace:trace.agent_event('graph_route',role='controller',source='login',
            destination=routes.get(route,'blocked'),reason=route)
        return route
    graph=StateGraph(State)
    for name,callback in (('login',login_node),('verified_doer',verified_doer),
            ('diagnostic_doer',diagnostic_doer),('blocked',blocked_node),('finish',finish_node)):
        graph.add_node(name,recorded(name,callback))
    graph.add_edge(START,'login')
    graph.add_conditional_edges('login',select_route,routes)
    for node in ('verified_doer','diagnostic_doer','blocked'):
        graph.add_edge(node,'finish')
    graph.add_edge('finish',END)
    return graph.compile()


async def continue_setup(browser,login_tools,result,model,budget,limits,*,checkpoint=confirm_poc,hold=None,
    authorized_mutations=False,mutation_review=None,native_skill=False):
    raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp')
    from browser.inspection import inspection_hold
    diagnostic_start=len(browser.trace.model_diagnostics)
    provider_before=browser.trace.openai_api_calls
    from modules.whatsapp.failure_inspection import capture_setup_failure
    result.update(authentication_verification=result['verification'],authentication_reason_code=result['reason_code'],
        authentication_missing_evidence=list(result.get('missing_evidence',[])),poc_checkpoint='NOT_ACCEPTED',
        setup_status='BLOCKED',setup_gates={},setup_gates_current=False,explicit_commit_actions=0,
        persistence={'server_persistence':'UNDETERMINED','observation_status':'NOT_STARTED'},
        live_certified=False,automatic_graduation=False)
    observer=None;observer_start=None;tools=None
    try:
        if result.get('error') or login_tools.terminal_error or any(e.get('tool_phase')=='denied' or e.get('tool_name')=='FORBIDDEN' for e in browser.trace.events):raise SetupStop('LOGIN_EXECUTION_STOPPED')
        browser.begin_phase('checkpoint',limits.checkpoint_seconds,4)
        initial=await browser.observe();route=healthy_observation(browser,initial)
        signature=source_signature(browser);source_url=await browser.page.url()
        grant=await checkpoint(browser,limits.checkpoint_seconds)
        if not isinstance(grant,PocGrant) or grant.browser is not browser or grant.run_id!=browser.trace.run_id:raise SetupStop('POC_GRANT_REQUIRED')
        observed=await browser.observe()
        if healthy_observation(browser,observed)!=route or source_signature(browser)!=signature or await browser.page.url()!=source_url:raise SetupStop('CHECKPOINT_CONTEXT_CHANGED')
        grant.route=route;grant.source_signature=signature;grant.source_url=source_url
        result['login_handoff_route']=route
        if route=='EXISTING_SESSION_VERIFIED':
            browser.trace.add('verify',observed,Result.AUTHENTICATED_AND_VERIFIED)
            browser.trace.fact('EXISTING_SESSION_VERIFIED')
        result['poc_checkpoint']='HUMAN_CONFIRMED_FOR_POC'
        from modules.login.verifier import verify
        result['authentication_mode']=('INDEPENDENTLY_CERTIFIED' if
            verify(observed)==Result.AUTHENTICATED_AND_VERIFIED and browser.evidence is not None and browser.evidence.reviewed
            else 'SUPERVISED_OPERATOR_CONFIRMED')
        browser.trace.lifecycle('WHATSAPP',{'state':'POC_CHECKPOINT','code':'HUMAN_CONFIRMED_FOR_POC'})
        browser.trace.fact('HUMAN_CONFIRMED_FOR_POC')
        browser.trace.lifecycle('WHATSAPP',{'state':'LOGIN_HANDOFF_ACCEPTED','code':route})
        print('LOGIN_HANDOFF_ACCEPTED',flush=True)
        print('HUMAN_CONFIRMED_FOR_POC: independent authentication status remains '+result['authentication_verification'],flush=True)
        browser.begin_phase('setup',limits.setup_seconds,36);budget.phase='setup'
        tools=SetupTools(browser,grant,authorized_mutations=authorized_mutations,mutation_review=mutation_review)
        async with asyncio.timeout(limits.setup_seconds):
            initial=await tools.open()
            observer=PersistenceObserver(browser)
            observer.operation_source=tools
            observer.active=True
            agent,_=create_setup_doer(model,tools,browser.trace,budget,**({'native_skill':True} if native_skill else {}))
            tools.milestone('SETUP_DOER_STARTED')
            async def start_observer():
                try:
                    async with asyncio.timeout(2):await observer.start()
                except Exception:browser.trace.lifecycle('WHATSAPP',{'state':'PERSISTENCE_OBSERVER_UNAVAILABLE'})
            observer_start=asyncio.create_task(start_observer())
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                state=await agent.ainvoke({'messages':[{'role':'user','content':
                    'Observe Setup first. Campaign name is the first action milestone. '+
                    'Use only the seven scoped tools; separate action permission is controller-owned. '+
                    'Record each verified field and stop unsaved.\n'+json.dumps(initial)}]},
                    {'configurable':{'thread_id':browser.trace.run_id+'-whatsapp'},'recursion_limit':80})
            report=state.get('structured_response')
            if report is None:raise SetupStop('MISSING_SETUP_REPORT')
            if native_skill and not tools.skill_provenance:raise SetupStop('SKILL_READ_REQUIRED')
            result['setup_agent_report']=report.model_dump()
            if report.status!='SETUP_FILLED_UNSAVED':
                diagnostic=tools.blocked_diagnostic()
                result['setup_blocked_diagnostic']=diagnostic
                raise SetupStop(diagnostic['reason'])
            # The model report cannot establish completion.
            verified=await tools.verify()
            if not verified['ok']:raise SetupStop(verified['error'])
        result['setup_status']='SETUP_FILLED_UNSAVED';result['setup_gates']=dict(tools.final_gates)
        print('SETUP_FILLED_UNSAVED: all four values verified; no explicit save/advance action. Server persistence remains undetermined.',flush=True)
        browser.begin_phase('inspection',limits.hold_seconds+10,3)
        try:await (hold or inspection_hold)(limits.hold_seconds)
        except TimeoutError:result['inspection_status']='HOLD_DEADLINE_REACHED'
        verified=await tools.verify()
        if not verified['ok']:raise SetupStop(verified['error'])
        result['setup_gates']=dict(tools.final_gates)
        result['setup_gates_current']=True
        result['verification']=str(Result.SETUP_FILLED_UNSAVED);result['reason_code']='SETUP_VALUES_VERIFIED_UNSAVED'
    except (KeyboardInterrupt,asyncio.CancelledError):
        result['error']='OPERATOR_CANCELLED';result['setup_stop_reason']='OPERATOR_CANCELLED'
        result['setup_status']='BLOCKED';result['verification']=str(Result.UNEXPECTED_PAGE)
    except Exception as error:
        known_runtime={'MODEL_BUDGET_EXHAUSTED','TOTAL_TOKEN_BUDGET_EXHAUSTED','OUTPUT_LIMIT_INCOMPLETE','PROVIDER_RESPONSE_INCOMPLETE'}
        code=(error.code if isinstance(error,SetupStop) else 'PHASE_DEADLINE_REACHED' if isinstance(error,TimeoutError)
            else 'CODE_FIX_REQUIRED' if isinstance(error,(TypeError,AttributeError,KeyError,NotImplementedError))
            else str(error) if isinstance(error,RuntimeError) and str(error) in known_runtime
            else 'SETUP_EXECUTION_UNCERTAIN_NO_RETRY')
        result.update(setup_status='BLOCKED',setup_stop_reason=code,verification=str(Result.UNEXPECTED_PAGE),reason_code=code)
        stopped=result.get('setup_blocked_diagnostic') or {}
        result['setup_failure_phase']=tools.last_stop['phase'] if tools and tools.last_stop else stopped.get('phase',tools.phase if tools else 'BEFORE_OBSERVATION')
        result['setup_failed_predicate']=tools.last_stop['failed_predicate'] if tools and tools.last_stop else stopped.get('failed_predicate',getattr(error,'predicate',code))
        result['setup_failure_owner']=stopped.get('failure_owner','tool' if code=='CODE_FIX_REQUIRED' else 'unknown')
        print('SETUP_STOP phase='+result['setup_failure_phase']+' reason='+code+' predicate='+result['setup_failed_predicate'],flush=True)
        if tools and tools.final_gates:
            result['setup_gates']=dict(tools.final_gates);result['setup_gates_current']=tools.final_gates_current
        if tools and tools.last_preflight_failure:
            result['setup_failure_diagnostic']=dict(tools.last_preflight_failure)
        elif getattr(error,'diagnostic',None):result['setup_failure_diagnostic']=dict(error.diagnostic)
        browser.trace.add('error',error='POLICY');browser.trace.events[-1]['stop_code']=code
        if tools:tools.terminal_error=True
        result['setup_failure_evidence']=await capture_setup_failure(browser,tools,usage=request_usage(browser.trace,diagnostic_start),
            failure={'phase':result['setup_failure_phase'],'reason':code,'failed_predicate':result['setup_failed_predicate']})
        from modules.whatsapp.failure_inspection import inspect_failed_setup
        await inspect_failed_setup(browser,result,limits,hold=hold)
    finally:
        if result['setup_status']!='SETUP_FILLED_UNSAVED' and 'setup_failure_evidence' not in result:
            result['setup_failure_evidence']=await capture_setup_failure(browser,tools,usage=request_usage(browser.trace,diagnostic_start),
                failure={'phase':tools.phase if tools else 'BEFORE_OBSERVATION','reason':result.get('setup_stop_reason','BLOCKED')})
        if tools:
            result['setup_verified_fields']=sorted(tools.completed)
            result['setup_observation']={'page_observed':'SETUP_PAGE_OBSERVED' in tools.milestones,
                'form_recognized':tools.form_identity is not None,'read_only_before_authorization':True,
                'dom_form_recognized':bool(tools.current and tools.current.get('form_recognized')),
                'action_form_identity_bound':tools.form_identity is not None,
                'form_recognized_semantics':'ACTION_FORM_IDENTITY_BOUND','snapshot':tools.snapshot}
            result['setup_safety_status']=tools.safety_status
            result['mutation_authorization']=tools.authorization_summary()
            if tools.risk_summary is not None:result['closed_root_investigation']=tools.risk_summary
            if tools.review_evidence is not None:result['scoped_mutation_review']=tools.review_evidence
            result['setup_execution']=tools.execution_summary()
            if result['setup_status']=='BLOCKED':
                result['setup_execution']['controller_stop']={'reason':result.get('setup_stop_reason','BLOCKED'),
                    'failed_predicate':result.get('setup_failed_predicate'),'failure_owner':result.get('setup_failure_owner','unknown')}
        else:
            result['setup_execution']={'status':'BLOCKED','field_facts':{},'remaining_fields':list(ORDER),
                'last_verified_field':None,'skill_provenance':None,'tool_results':[],'per_field_seconds':{f:None for f in ORDER}}
        delta=request_usage(browser.trace,diagnostic_start)
        result['setup_execution']['usage']=delta
        result['setup_execution']['actual_provider_requests']=browser.trace.openai_api_calls-provider_before
        result['session_usage_roles']={'login_doer':request_usage(browser.trace,0,diagnostic_start),
            'task_doer':delta,'teacher':request_usage(browser.trace,role='teacher')}
        if observer:
            if observer_start and not observer_start.done():
                observer_start.cancel()
                try:await observer_start
                except asyncio.CancelledError:pass
            result['persistence']=observer.summary()
            try:await asyncio.wait_for(observer.close(),2)
            except Exception:browser.trace.lifecycle('WHATSAPP',{'state':'PERSISTENCE_OBSERVER_CLOSE_WARNING'})
        result['setup_tab_status']='PROTECTED_SAME_CONTEXT_TAB' if browser.setup_binding is not None else 'TAB_CREATION_FAILED_CLOSED' if browser.setup_tab_attempted else 'NOT_OPENED'
        result['setup_navigation']=tools.navigation_evidence if tools else None
        result['original_tab_retained']=bool(browser.login_page is not None)
        # Preserve the actual authentication certificate separately. Human confirmation
        # never writes reviewed evidence or satisfies any login verifier gate.
        result['missing_evidence']=list(result['authentication_missing_evidence'])


def write_draft(result,events,root=None):
    if not re.fullmatch(r'[a-f0-9]{32}',result['run_id']):raise SetupStop('INVALID_DRAFT_RUN_ID')
    root=Path(root or ROOT/'learning'/'whatsapp_setup'/'drafts')/result['run_id']
    root.mkdir(parents=True,exist_ok=False)
    facts=[dict(f) for f in result.get('step_facts',[]) if 0<=f['event_index']<len(events)]
    artifact={'schema':'whatsapp-setup-draft/v1','state':'DRAFT','source':result.get('source','NOT_RETAINED'),'run_id':result['run_id'],
        'scope':'FOUR_FIELDS_SETUP_ONLY_UNSAVED','setup_status':result.get('setup_status','BLOCKED'),
        'authentication_verification':result.get('authentication_verification',result['verification']),
        'login_handoff_route':result.get('login_handoff_route','NOT_ESTABLISHED'),'login_submissions':result.get('login_submissions',0),
        'setup_tab_status':result.get('setup_tab_status','NOT_OPENED'),'original_tab_retained':result.get('original_tab_retained',False),
        'poc_checkpoint':result.get('poc_checkpoint','NOT_ACCEPTED'),'gates':result.get('setup_gates',{}),'gates_current':result.get('setup_gates_current',False),
        'teacher_status':result.get('teacher_status'),'teacher_decision':result.get('teacher_decision'),
        'evidence_events':facts,'cleanup_result':result['cleanup_result'],'persistence':result.get('persistence'),
        'campaign_creation_operation':'NOT_AUTHORIZED_OR_INVOKED','server_creation_state':'UNDETERMINED','explicit_commit_actions':0,
        'live_certified':False,'runtime_loading_allowed':False,'graduation_allowed':False,'promotion_allowed':False}
    (root/'DRAFT.json').write_text(json.dumps(artifact,indent=2),encoding='utf-8')
    history='# WhatsApp Setup execution history - DRAFT\n\n'
    history+='Only four-field Setup completion is in scope. No campaign-creation or authentication certificate is conferred. Server persistence is undetermined.\n\n'
    history+=''.join('- '+f['kind']+': event '+str(f['event_index'])+'\n' for f in facts)
    (root/'execution-history.md').write_text(history,encoding='utf-8')
    return {'state':'DRAFT','path':str(root.relative_to(ROOT)) if root.is_relative_to(ROOT) else str(root),
        'runtime_loading_allowed':False,'graduation_allowed':False}


async def _retired_phase1_source(config,authorized_live=False,authorized_setup=False,limits=None,*,doer_only=False):
    raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp; historical body retained for source provenance')
    limits=(limits or (Page1Limits() if doer_only else Phase1Limits())).validate()
    if doer_only and limits.teacher_requests!=0:raise ConfigurationError('Doer-only mode requires zero Teacher requests')
    if not authorized_live or not authorized_setup:raise ConfigurationError('Separate explicit live-login and four-field Setup authorization required')
    import sys
    if not sys.stdin.isatty():raise ConfigurationError('Phase 1 requires an interactive headed operator terminal')
    original_timeout=config.timeout_seconds
    session_config=replace(config,max_actions=config.max_actions+43,
        timeout_seconds=original_timeout+limits.checkpoint_seconds+limits.setup_seconds+limits.hold_seconds+10)
    # The existing login grant/budget stays <=8; Setup has its own explicit stage cap.
    permit=authorize_live(session_config,True,limits.login_requests+limits.teacher_requests)
    from app.model_settings import ModelSettings
    settings=ModelSettings.load()
    if limits.teacher_requests>settings.teacher_requests or limits.total_requests>settings.cycle_requests:
        raise ConfigurationError('Phase 1 budgets exceed configured model caps')
    output_bound=(limits.login_requests+limits.setup_requests)*settings.doer_output+limits.teacher_requests*settings.teacher_output
    if output_bound>settings.cycle_output:raise ConfigurationError('Phase 1 maximum output exceeds configured token cap')
    print(f'PHASE 1 BUDGET: login={limits.login_requests}, Setup={limits.setup_requests}, Teacher={limits.teacher_requests}; total={limits.total_requests}; maximum output tokens={output_bound}.',flush=True)
    from modules.login.credentials import LocalCredentials,CredentialBroker
    from modules.login.evidence import EvidenceConfig
    from learning.skill_storage.registry import SkillStore
    credentials=LocalCredentials.load()
    evidence=EvidenceConfig.for_session(config.evidence_file,review_mode=True)
    model=openai_model('doer',settings)
    teacher=None if doer_only else openai_model('teacher',settings)
    try:await model.root_async_client.models.retrieve('gpt-6-luna')
    except Exception:raise ConfigurationError('Live model availability could not be established; browser was not launched') from None
    snapshot=SkillStore().snapshot()
    strategy=Strategy.model_validate(snapshot.metadata['strategy']) if snapshot else Strategy()
    trace=Trace(strategy_version=strategy.version);trace.reasoner_mode='deepagents_openai_gpt-6-luna'
    budget=Phase1Budget(limits);budget.max_input_tokens=settings.cycle_input;budget.max_output_tokens=output_bound
    model.bind_meter(trace,budget,'doer')
    browser=TaskSession(session_config,OriginPolicy(set(permit.origins)),trace,fixture=False,
        evidence=evidence,live_permit=permit,login_seconds=original_timeout)
    login_tools=HandoffLoginTools(browser,CredentialBroker(browser,credentials));login_tools.strategy=strategy
    async def after_login(session,tools,result):
        await continue_setup(session,tools,result,model,budget,limits,authorized_mutations=authorized_setup,native_skill=doer_only)
    result=None
    try:
        result=await _execute_doer(browser,login_tools,DEFAULT_TASK,model,budget,False,snapshot,
            after_login=after_login,login_timeout=original_timeout,close_session=False)
    finally:
        # Only this outer WhatsApp lifecycle owns final process/profile cleanup.
        try:await browser.close()
        except Exception:
            if trace.cleanup_result not in {'CLOSED_AND_PROFILE_REMOVED','CLEANUP_FAILED','CLEANUP_UNCONFIRMED'}:trace.cleanup_result='CLEANUP_UNCONFIRMED'
            if result is not None:result['cleanup_error']=trace.cleanup_result
    result['cleanup_result']=trace.cleanup_result
    result['cleanup_diagnostic']=trace.cleanup_diagnostic
    result.update(source='live',workflow='whatsapp_setup_phase1',automatic_graduation=False,live_certified=False)
    if trace.cleanup_result!='CLOSED_AND_PROFILE_REMOVED':
        result.update(setup_status='BLOCKED',verification=str(Result.UNEXPECTED_PAGE),reason_code='CLEANUP_NOT_CONFIRMED')
    trace.final_outcome={'verification':result['verification'],'reason_code':result['reason_code'],
        'execution_error':result['error'],'cleanup_result':trace.cleanup_result}
    trace.add('outcome',result=result['verification'])
    result['review_previous_strategy']=strategy.model_dump()
    _finish_result(result,trace,browser,budget)
    print('LIVE DOER COMPLETED: '+result.get('setup_status','BLOCKED')+'; cleanup='+trace.cleanup_result,flush=True)
    if doer_only:
        trace.teacher_result='DISABLED_DOER_ONLY';result['teacher_status']=trace.teacher_result
        result['learning']={'state':'DISABLED_DOER_ONLY','promotion_allowed':False}
        result['stage_requests']=dict(budget.stage_requests)
        result['step_facts']=list(trace.step_facts)
        _finish_result(result,trace,browser,budget)
        complete=request_usage(trace)
        result['token_usage']={k:complete[k] for k in ('input_tokens','output_tokens','total_tokens')}
        result['token_usage_available']=complete['total_tokens'] is not None
        (ROOT/'outputs'/f'{trace.run_id}-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        return result
    if result.get('error')=='OPERATOR_CANCELLED':
        trace.teacher_result='DEFERRED_OPERATOR_CANCELLED';result['teacher_status']=trace.teacher_result
    else:await review_evidence(result,trace,teacher,budget,False,adapter=WhatsAppReviewAdapter(result,trace.events))
    print('TEACHER REVIEW COMPLETED: '+result['teacher_status'],flush=True)
    result['step_facts']=list(trace.step_facts)
    result['learning']=write_draft(result,trace.events)
    result['stage_requests']=dict(budget.stage_requests)
    _finish_result(result,trace,browser,budget)
    return result


async def run_phase1(*args,**kwargs):
    raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp')


def compact_human_assisted_teacher_evidence(package):
    # Full redacted evidence is retained separately. Cite the actual tool events
    # and a small set of genuine Login actions in the provider-facing summary.
    login = package.get('login_execution_evidence', {})
    prioritized_tools = ('submit_login', 'verify_dashboard', 'inspect_page', 'navigate_approved')
    cards = login.get('evidence', [])
    login_actions = []
    for tool in prioritized_tools:
        matching = [card for card in cards if card.get('kind') in {'doer_action', 'tool_result'}
                    and card.get('data', {}).get('tool') == tool and card.get('events')]
        if matching:
            card = matching[-1]
            login_actions.append({'ref': 'trace:' + str(card['events'][-1]), 'tool': tool,
                                  'kind': card['kind']})
    event_keys = ('id', 'trace_ref', 'stage', 'field', 'action', 'approach', 'attempted',
                  'valid_approach', 'ok', 'progress', 'error', 'owner', 'unsafe', 'verification', 'evidence_refs')
    facts = ('stage_visible', 'field_evidence', 'unresolved_fields', 'recognition_status',
             'dropdown_open_verified', 'search_text_verified', 'selection_verified', 'fields')
    events = [{**{key: event[key] for key in event_keys if key in event},
               'result': {key: event['result'][key] for key in facts if key in event.get('result', {})}}
              for event in package['events'][-12:]]
    result = {key: package.get(key) for key in (
        'task_scope', 'goal', 'execution_facts', 'login_verification', 'authentication_verified',
        'task_doer_invoked', 'analysis_only', 'status', 'failure', 'skill_sha256', 'candidate_version',
        'review_kind', 'pre_cleanup_evidence', 'tool_schema_rejections')}
    observer = package.get('observer', [])[-1:]
    strategy_lines = (package.get('skill') or '').splitlines()
    excerpt = []
    for heading in ('## Setup goal', '## Recovery and STUCK', '## Evidence-grounded candidate guidance'):
        if heading in strategy_lines:
            pos = strategy_lines.index(heading)
            excerpt.extend(strategy_lines[pos:pos+6])
    evidence_ids = [card['ref'] for card in login_actions] + [event['id'] for event in events]
    browser_cards = package.get('browser_observer_evidence', [])
    browser_ids = {event['id'] for event in browser_cards if event.get('id')}
    for event in events:
        event['evidence_refs'] = [ref for ref in event.get('evidence_refs', ()) if ref in browser_ids]
    evidence_ids += sorted(browser_ids)
    evidence_ids += [event['trace_ref'] for event in events if event.get('trace_ref')]
    evidence_ids += [event['id'] for event in (package.get('tool_schema_rejections') or []) if event.get('id')]
    evidence_ids += [finding['readback']['evidence_ref'] for finding in observer
                     if isinstance(finding, dict) and finding.get('readback', {}).get('evidence_ref')]
    if package.get('failure'):
        evidence_ids.append('controller_failure')
    result.update(events=events, login_execution_evidence={'actions': login_actions},
        observer=observer, browser_observer_evidence=browser_cards, strategy_excerpt='\n'.join(excerpt)[:1400],
        evidence_ids=list(dict.fromkeys(evidence_ids)))
    return result


async def review_v2_attempt(ctx,state,authorization,payload,*,analysis_only=False):
    """Shared genuine Teacher path; consumes sanitized evidence, never browser actions."""
    import hashlib
    from deep_agents.teacher import create_teacher
    from modules.whatsapp.contract import V2Stop,V2Lesson
    tools=ctx.get('tools');trace=ctx['trace']
    if (state.get('failure') or {}).get('code')=='OPERATOR_CANCELLED':
        trace.teacher_result='DEFERRED_OPERATOR_CANCELLED';return {'route':'candidate'}
    try:
        if ctx.get('teacher') is None:raise V2Stop('TEACHER_CONFIGURATION_UNAVAILABLE')
        from deep_agents.evidence import compile_evidence,execution_facts,canonical,source_events
        from modules.login.review import LoginReviewAdapter
        actual=execution_facts(trace.events,trace.step_facts)
        task_invoked=ctx.get('task_doer_invoked') is True or task_invocation_observed(trace.events)
        retained=dict(ctx.get('login_result') or {'run_id':trace.run_id,'verification':str(Result.NOT_TESTED),
            'reason_code':state.get('failure',{}).get('code','NOT_RETAINED'),'step_facts':list(trace.step_facts)})
        retained.update(login_submissions=browser.login_submissions if (browser:=ctx.get('browser')) else 0,
            role_usage={'doer':(ctx.get('login_usage') or {}).get('doer',{})})
        compiled=compile_evidence(retained,trace.events,LoginReviewAdapter(),max_tokens=5000).model_dump()
        source_indices={index for card in compiled['evidence'] for index in card['events']}
        source_indices.update(f['event'] for f in compiled['facts'])
        selected_events=tools.events[-12:] if tools else []
        ids=[e['id'] for e in selected_events]+['trace:'+str(i) for i in sorted(source_indices)]
        if state.get('failure'):ids.append('controller_failure')
        ctx['evidence_ids']=ids
        login_action_refs={'trace:'+str(i) for card in compiled['evidence'] if card['kind'] in {'doer_action','tool_result'}
            and card['data'].get('tool') in {'navigate_approved','inspect_page','interact_observed','submit_login','verify_dashboard'} for i in card['events']}
        ctx['trace'].agent_event('teacher_invocation',role='teacher',goal=state.get('stage','login'),
            failure=state.get('failure'),evidence_refs=ids,status=state['status'])
        package={'task_scope':('HUMAN_SUPERVISED_SETUP_FIELD_TEST' if authorization.manual_form_test else
            'APPROVED_SAVED_DRAFT_ONLY' if authorization.edits and authorization.persistence else 'AUTHENTICATED_READ_ONLY_DISCOVERY'),
            'goal':state.get('stage','login'),'execution_facts':actual,'login_execution_evidence':compiled,
            'login_verification':ctx.get('login_verification'),
            'login_execution_failure':retained.get('execution_failure'),
            'authentication_verified':ctx['authentication_verified'],
            'task_doer_invoked':task_invoked,'analysis_only':analysis_only,
            'controller_state':{'route_source':ctx.get('route_source'),'route':state.get('route'),
                'failure':state.get('failure')},
            'status':state['status'],'events':selected_events,'observer':ctx['findings'][-6:],
            'omitted_older_tool_events':max(0,len(tools.events)-12) if tools else 0,
            'all_execution_evidence_preserved_in_run_trace':True,
            'failure':state.get('failure'),'evidence_ids':ids,'skill_sha256':ctx.get('skill').digest if ctx.get('skill') else None,
            'candidate_version':state.get('candidate'),'skill':ctx['skill'].content if ctx.get('skill') and ctx['task_skill_read'] else None,
            'limitations':['No publication, activation, test-send or actual scheduling','No Python edits','Uncertain writes cannot be retried']}
        if ctx.get('human_assisted'):
            package.update(review_kind=ctx['review_kind'],
                pre_cleanup_evidence=ctx.get('pre_cleanup_evidence'),
                tool_schema_rejections=[{key: event['agent_execution'].get(key) for key in
                    ('id', 'action', 'reason', 'unexpected', 'missing')}
                    for event in trace.events if event.get('agent_execution', {}).get('kind') == 'browser_action_rejected'][-4:])
            decisive = {'campaign_audience_menu_close','campaign_audience_summary_readback',
                        'campaign_field_readback','campaign_header_binding','page1_transition_unresolved','campaign_exclude_chip_verified',
                        'setup_evidence_saved','content_template_target_ownership','content_template_results_settled'}
            task_refs = {ref for event in selected_events for ref in event.get('evidence_refs', ())}
            browser_cards = []
            for entry in trace.events:
                event = entry.get('agent_execution') or {}
                kind = event.get('kind')
                if kind not in decisive and event.get('id') not in task_refs:
                    continue
                if kind == 'dom_observation':
                    observed = event.get('result', {})
                    card = {key: observed[key] for key in ('stage','field_evidence','binding_failures','scope_basis',
                            'audience_picker_evidence','audience_section_evidence','content_preview_evidence') if key in observed}
                else:
                    card = {key: event[key] for key in ('stage','field','status','reason','code','results_hidden',
                            'source_step','destination_step','readback','expected_count','matched_count','evidence_ref','approaches',
                            'panel_count','competing_owner_count','owned_search_delegate','visible_option_count','exact_option_count')
                            if key in event}
                browser_cards.append({'id':event['id'],'kind':kind,'facts':card})
            latest = [card for card in browser_cards if card['kind'] in decisive][-7:]
            selected_refs = [card for card in browser_cards if card['kind'] not in decisive][-6:]
            package['browser_observer_evidence'] = selected_refs + latest
        package=trace.redact_agent(package)
        evidence_bytes=json.dumps(package,sort_keys=True).encode('utf-8')
        (ctx['directory']/'teacher-input-evidence.json').write_bytes(evidence_bytes)
        trace.agent_event('teacher_evidence_package',role='teacher',execution_facts=actual,
            evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
            source_event_count=compiled['source_event_count'],evidence_refs=ids)
        sent = package
        if ctx.get('human_assisted'):
            sent = compact_human_assisted_teacher_evidence(package)
            ids = sent['evidence_ids']
            ctx['evidence_ids'] = ids
            (ctx['directory']/'teacher-request-evidence.json').write_bytes(canonical(sent).encode('utf-8'))
        agent,_=create_teacher(ctx['teacher'],trace,ctx['budget'],schema=V2Lesson,
            prompt_override='For a completed run, summarize what worked and return no instructions. For an analysis_only run, return no instructions. Review real execution evidence only. Echo the supplied deterministic execution_facts exactly. Empty WhatsApp events do not mean Login/browser actions did not occur: inspect login_execution_evidence and login_verification. Cite evidence_ids, including at least one actual Login action when present; trace:N refers to original event index N. Never deny recorded browser activity. Classify defects honestly; tool/controller defects require code_fix_required. Propose concise procedural candidate instructions only when justified. Recommend code repairs as bounded file/function descriptions in recommended_code_repairs; never apply them. No selectors, URLs, private values, source edits, task changes or permission relaxation. No executable tools. Never claim an unconsumed candidate improved execution.')
        from langsmith import tracing_context
        with tracing_context(enabled=False):
            reviewed=await agent.ainvoke({'messages':[{'role':'user','content':canonical(sent)}]},
                {'configurable':{'thread_id':trace.run_id+'-teacher'},'recursion_limit':6})
        lesson=V2Lesson.model_validate(reviewed['structured_response'])
        rejection='TEACHER_EVIDENCE_INVALID' if not set(lesson.evidence_refs)<=set(ids) else None
        if lesson.execution_facts is None or lesson.execution_facts.model_dump()!=actual:
            rejection='TEACHER_EXECUTION_FACTS_CONTRADICT_EVIDENCE'
        if actual['login_tool_calls'] and not set(lesson.evidence_refs)&login_action_refs:
            rejection='TEACHER_OMITTED_ACTUAL_LOGIN_ACTIONS'
        prose=' '.join([lesson.finding,lesson.change_summary,*lesson.recommended_code_repairs])
        denial=r'\bno browser (?:interaction|actions?)(?: or task evidence)? (?:was|were|occurred|took place|recorded)|\b(?:occurred|happened) before (?:any )?browser interaction\b|\bbefore any browser (?:interaction|actions?)\b|\bbrowser (?:was|has) not (?:started|launched)\b'
        if (actual['login_tool_calls'] or actual['task_tool_calls']) and re.search(denial,prose,re.I):
            rejection='TEACHER_DENIED_RECORDED_BROWSER_ACTIVITY'
        rejection=teacher_factual_contradiction(prose,actual,ctx['authentication_verified'],task_invoked=task_invoked) or rejection
        if rejection:
            ctx['teacher_rejection']=trace.redact_agent({'code':rejection,'response':lesson.model_dump(),
                'unlisted_evidence_refs':sorted(set(lesson.evidence_refs)-set(ids)),
                'actual_execution_facts':actual,'source_response_sha256':hashlib.sha256(lesson.model_dump_json().encode()).hexdigest()})
            trace.agent_event('teacher_rejected',role='teacher',rejection=ctx['teacher_rejection'])
            raise V2Stop(rejection,owner='controller')
        code_defect = (state.get('failure') or {}).get('code') == 'CODE_FIX_REQUIRED'
        if analysis_only or code_defect or ctx.get('review_kind') == 'success_review':
            legacy_analysis = analysis_only and not ctx.get('human_assisted')
            lesson=lesson.model_copy(update={'code_fix_required': code_defect or lesson.code_fix_required or legacy_analysis,
                                            'safe_next_iteration':False,'instructions':[]})
        ctx['lesson']=lesson
        review=lesson.model_dump()
        for key in ('finding','change_summary'):
            for value in payload.model_dump().values():
                if isinstance(value,str) and len(value)>=4:review[key]=review[key].replace(value,'[approved value]')
        review['source_response_sha256']=hashlib.sha256(lesson.model_dump_json().encode()).hexdigest()
        review=trace.redact_agent(review)
        review['private_values_redacted']=True;ctx['teacher_review']=review
        trace.agent_event('teacher_assessment',role='teacher',assessment=review,evidence_refs=lesson.evidence_refs)
        trace.teacher_result='ANALYZED'
    except Exception as error:
        from deep_agents.permissions import model_failure_details
        ctx['teacher_validation']=model_failure_details(error,next((d for d in reversed(trace.model_diagnostics) if d.get('role')=='teacher'),{}))
        trace.teacher_result='ANALYSIS_REJECTED' if ctx.get('teacher_rejection') else 'ANALYSIS_UNAVAILABLE'
        trace.agent_event('teacher_unavailable',role='teacher',error=type(error).__name__,failure=ctx['teacher_validation'],status=trace.teacher_result)
    return {'route':'candidate'}


async def store_v2_candidate(ctx,state,authorization,payload,ui,task_digest):
    """Use the existing WhatsApp registry; never load or promote a new candidate."""
    from learning.skill_storage.registry import save_whatsapp_candidate
    tools=ctx.get('tools');lesson=ctx.get('lesson')
    if authorization.learning and lesson and not (tools and (tools.terminal or tools.pending_write)) and not state.get('failure',{}).get('unsafe'):
        try:
            if authorization.manual_form_test and any(step.goal!='setup' or
                    step.action=='screenshot' and not authorization.vision for step in lesson.instructions):
                ctx['candidate_error']='CANDIDATE_OUTSIDE_MANUAL_SETUP_SCOPE'
                ctx['trace'].agent_event('candidate_update',status=ctx['candidate_error'])
                return {'route':'cleanup'}
            saved=save_whatsapp_candidate(lesson,run_id=ctx['trace'].run_id,parent=ctx['skill'],
                task_digest=task_digest,evidence_refs=ctx['evidence_ids'],payload=payload,ui=ui)
            ctx['candidate_result']=saved
            ctx['trace'].agent_event('candidate_update',candidate=saved,
                change_summary=ctx.get('teacher_review',{}).get('change_summary'),
                status='SAVED_UNAPPROVED' if saved else 'NO_VALID_NEW_CANDIDATE',
                loaded_in_later_iteration='NOT_KNOWN_AT_CREATION')
            if saved:return {'candidate':saved['version'],'route':'cleanup'}
        except Exception:
            ctx['candidate_error']='CANDIDATE_VALIDATION_FAILED'
            ctx['trace'].agent_event('candidate_update',status=ctx['candidate_error'])
    return {'route':'cleanup'}


async def run_v2(args):
    """The only supported WhatsApp lifecycle. No external call before preflight."""
    import hashlib
    from typing import TypedDict
    from langgraph.graph import StateGraph,START,END
    from langgraph.checkpoint.memory import InMemorySaver
    from modules.whatsapp.contract import (V2Authorization,V2Stop,ReviewedUI,load_v2_payload,
        STAGES,STAGE_FIELDS,V2Lesson,GoalFinding,SETUP_URL)
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.agent import create_v2_doer
    from modules.whatsapp.observer import observe_goal,deterministic_readback
    from learning.skill_storage.registry import whatsapp_skill,save_whatsapp_candidate
    from learning.skill_storage.registry import SkillStore
    from app.config import Config
    from learning.browser_runtime import usage
    from deep_agents.permissions import InferenceBudget,TokenPool
    from deep_agents.teacher import create_teacher
    from browser.stagehand_client import StagehandClient
    from browser.protected_tabs import create_protected_tab
    from modules.login.credentials import CredentialBroker,LocalCredentials
    from modules.login.evidence import EvidenceConfig
    from modules.login.verifier import verify
    from app.model_settings import ModelSettings
    from time import monotonic

    authorization=V2Authorization(**{k:getattr(args,'authorized_'+k,False) for k in V2Authorization.model_fields})
    authorization.require_run(args.iterations)
    if args.iterations!=1:
        raise V2Stop('ONE_LIVE_ITERATION_PER_OPERATOR_LAUNCH',owner='authorization')
    payload,task_digest=load_v2_payload(args.payload,approved_digest=args.approve_payload_sha256)
    payload.check_schedule()  # Intentionally unresolved input blocks before config/login/inference.
    ui=ReviewedUI()  # Authorized origin only; no invented field/record evidence.
    if bool(args.ui_evidence)!=bool(args.approve_ui_sha256):raise V2Stop('UI_EVIDENCE_AND_DIGEST_MUST_BE_PAIRED',owner='authorization')
    if args.ui_evidence:
        raw=Path(args.ui_evidence).read_bytes()
        if hashlib.sha256(raw).hexdigest()!=args.approve_ui_sha256:raise V2Stop('UI_EVIDENCE_APPROVAL_MISMATCH',owner='authorization')
        try:ui=ReviewedUI.model_validate_json(raw)
        except ValueError:raise V2Stop('INVALID_INCREMENTAL_UI_EVIDENCE',owner='authorization') from None
    config=Config.load();settings=ModelSettings.load()
    review_login=getattr(args,'review_login_evidence',False)
    login_review_seconds=getattr(args,'login_review_seconds',120)
    if not 1<=login_review_seconds<=300:raise V2Stop('INVALID_LOGIN_REVIEW_DEADLINE')
    if review_login:
        import sys
        if not sys.stdin.isatty():raise V2Stop('LOGIN_EVIDENCE_REVIEW_REQUIRES_INTERACTIVE_TERMINAL',owner='authorization')
    if not 1<=args.login_api_budget<=6 or not 1<=args.max_turns<=30:raise V2Stop('INVALID_V2_BUDGET')
    if not 1<=args.total_api_budget<=settings.cycle_requests:raise V2Stop('CONFIGURED_REQUEST_CAP_EXCEEDED')
    if not 30<=args.session_seconds<=900:raise V2Stop('INVALID_SESSION_DEADLINE')
    # Load the candidate before any provider/browser access, preserving Login store defaults.
    whatsapp_skill(candidate=args.candidate,evaluate=authorization.candidate_evaluation,task_digest=task_digest,ui=ui)
    token_pool=TokenPool(settings.cycle_input,settings.cycle_output)
    records=[];ctx={};total_requests=0;seen_sessions=set()

    def reported_usage(rows):
        # Existing meter counts payloads prepared for dispatch, not HTTP transport.
        return {role:{**row,'dispatch_ready_requests':row.get('provider_requests'),
            'provider_requests':None,'provider_request_count_basis':'TRANSPORT_NOT_INDEPENDENTLY_OBSERVED'}
            for role,row in rows.items()}

    class State(TypedDict,total=False):
        iteration:int
        stage:str
        status:str
        route:str
        candidate:str|None
        turns:int
        failure:dict
        finding:dict
        route_reason:str

    def failure(error):
        if isinstance(error,(KeyboardInterrupt,asyncio.CancelledError)):
            return {'code':'OPERATOR_CANCELLED','owner':'controller','unsafe':True}
        defect=isinstance(error,(TypeError,AttributeError,KeyError,NameError,NotImplementedError))
        return {'code':'CODE_FIX_REQUIRED' if defect else error.code if isinstance(error,V2Stop) else 'MODEL_OR_ENVIRONMENT_FAILURE',
            'owner':'tool' if defect else error.owner if isinstance(error,V2Stop) else 'unknown',
            'unsafe':error.unsafe if isinstance(error,V2Stop) else True}

    async def start(state):
        nonlocal ctx
        ctx={'browser':None,'tools':None,'teacher':None,'lesson':None,'findings':[],
            'stage_records':{},'failed_methods':{},'started':monotonic(),'login_usage':None,
            'task_skill_read':False,'authentication_verified':False,'starting_state':'NOT_ESTABLISHED',
            'login_result':None,'login_verification':None}
        trace=Trace(iteration=state.get('iteration',0)+1,roles=('doer','observer','teacher'))
        private_values=[v for v in payload.model_dump().values() if isinstance(v,str)]
        private_values += [ui.origin,ui.tenant,ui.record_path,ui.evidence_reference,ui.preview_selector,ui.content_preview_selector]
        private_values += list(ui.stage_selectors.values())+list(ui.next_selectors.values())
        private_values += [v for binding in ui.fields.values() for key,v in binding.model_dump().items() if key.endswith('selector') and isinstance(v,str)]
        trace.enable_agent_trace(private_values=private_values)
        trace.agent_event('graph_node_started',node='start')
        ctx['trace']=trace;seen_sessions.add(trace.run_id)
        directory=ROOT/'outputs'/'whatsapp-v2'/trace.run_id;directory.mkdir(parents=True,exist_ok=False);ctx['directory']=directory
        try:
            remaining=args.total_api_budget-total_requests
            if remaining<3:raise V2Stop('FINAL_REVIEW_BUDGET_RESERVED')
            budget=InferenceBudget(remaining,reserve_teacher=True,
                role_limits={'doer':remaining-2,'observer':remaining-2,'teacher':1})
            budget.token_pool=token_pool;budget.teacher_reserve_input=settings.teacher_input;budget.teacher_reserve_output=settings.teacher_output
            ctx['budget']=budget
            ctx['skill']=whatsapp_skill(candidate=state.get('candidate'),evaluate=authorization.candidate_evaluation,task_digest=task_digest,ui=ui)
            (directory/'loaded-SKILL.md').write_bytes(ctx['skill'].content.encode('utf-8'))
            trace.strategy_version=ctx['skill'].digest
            trace.agent_event('skill_selected',version=state.get('candidate') or ctx['skill'].metadata.get('version') or ctx['skill'].metadata.get('source_version'),content_sha256=ctx['skill'].digest,
                approved=ctx['skill'].approved,loaded_into_task_prompt=False)
            ctx['doer']=openai_model('doer',settings,trace,budget)
            ctx['observer']=openai_model('observer',settings,trace,budget)
            ctx['teacher']=openai_model('teacher',settings,trace,budget)
            login_evidence_path=getattr(args,'login_evidence_file',None) or config.evidence_file
            if review_login and not login_evidence_path:
                # Output destination only. Existing Login discovery writes it only
                # from actual observations; no placeholder evidence file is made.
                login_evidence_path=str(ROOT/'credentials'/'whatsapp-v2'/trace.run_id/'login-evidence.json')
            permit_config=replace(config,max_actions=300,timeout_seconds=args.session_seconds,evidence_file=login_evidence_path)
            permit=authorize_live(permit_config,True,args.login_api_budget)
            browser=StagehandClient(permit_config,OriginPolicy(set(permit.origins)),trace,
                evidence=EvidenceConfig.for_session(login_evidence_path,review_mode=review_login),live_permit=permit)
            ctx['browser']=browser
            browser.evidence_review=review_login
            browser.rendering_diagnostics_enabled=authorization.vision
            login=HandoffLoginTools(browser,CredentialBroker(browser,LocalCredentials.load()))
            login.strategy=Strategy()
            # Existing Login agent/broker/verifier; one submission per owned fresh session.
            original_limit=budget.role_limits['doer'];budget.role_limits['doer']=args.login_api_budget
            async def review_existing_login(active_browser,active_tools,login_result):
                if not review_login or login_result.get('error') or active_tools.terminal_error:return
                if verify(await active_browser.observe())==Result.AUTHENTICATED_AND_VERIFIED:return
                from browser.evidence_review import evidence_review_hold
                trace.agent_event('authentication_evidence_review',status='EXPLICIT_OPERATOR_REVIEW_REQUIRED',seconds=login_review_seconds)
                # Reuse the established passive discovery + digest preview/A gate.
                # This is outside the Login Doer; no credential resubmission.
                await evidence_review_hold(active_browser,login_review_seconds)
            try:
                login_model=openai_model('doer',settings,trace,budget,login=True)
                trace.agent_event('login_model_configuration',role='doer',max_output_tokens=login_model.max_tokens,reasoning_effort='low')
                result=await _execute_doer(browser,login,DEFAULT_TASK,login_model,budget,False,
                    SkillStore().snapshot(),close_session=False,login_timeout=config.timeout_seconds,
                    after_login=review_existing_login if review_login else None)
                ctx['login_result']=result
            finally:
                budget.role_limits['doer']=original_limit
                ctx['login_usage']=usage(trace)
            if result.get('error') or login.terminal_error:
                ctx['login_verification']={'verification':result.get('verification'),
                    'observation':trace.current_observation,'observation_basis':'LAST_RECORDED_TERMINAL_STOP_NO_ADDITIONAL_READ',
                    'execution_error':result.get('error'),'terminal_error':login.terminal_error,
                    'execution_failure':result.get('execution_failure'),
                    'findings':browser.verification_details}
                trace.agent_event('login_verification',role='doer',**ctx['login_verification'])
                raise login_execution_stop(result)
            from modules.login.evidence import stable_authentication
            authenticated_observation=await stable_authentication(browser)
            authentication_result=verify(authenticated_observation)
            ctx['login_verification']={'verification':str(authentication_result),
                'observation':authenticated_observation.model_dump(),'findings':browser.verification_details}
            trace.add('verify',authenticated_observation,authentication_result)
            trace.agent_event('login_verification',role='doer',**ctx['login_verification'])
            result.update(verification=str(authentication_result),
                reason_code=(browser.verification_details or {}).get('reason',result.get('reason_code')),
                missing_evidence=(browser.verification_details or {}).get('missing',result.get('missing_evidence',[])))
            if authentication_result!=Result.AUTHENTICATED_AND_VERIFIED:
                raise V2Stop('INDEPENDENT_AUTHENTICATION_NOT_VERIFIED',owner='authentication',unsafe=True)
            ctx['authentication_verified']=True
            trace.agent_event('authentication_verified',role='doer',result='INDEPENDENTLY_VERIFIED',
                evidence_reference='LOGIN_TWO_CURRENT_CONTEXT_OBSERVATIONS',findings=browser.verification_details)
            trace.agent_stage='setup';trace.agent_phase='controller'
            browser.login_page=browser.page
            tools=V2BrowserTools(browser,payload,ui,authorization,directory);ctx['tools']=tools
            trace.agent_event('task_tools_initialized',authentication_verified=True,
                session_reused=True,status='CONSTRUCTED_NOT_YET_INVOKED')
            try:ui.require_record()
            except V2Stop:
                tools.read_only=True;ctx['starting_state']='UNKNOWN_AUTHENTICATED_READ_ONLY_DISCOVERY'
                ctx['discovery_reason']='REVIEWED_CAMPAIGN_RECORD_EVIDENCE_REQUIRED'
                trace.agent_event('campaign_creation_blocked',reason='REVIEWED_CAMPAIGN_RECORD_EVIDENCE_REQUIRED',discovery_permitted=True)
            else:
                if not authorization.edits or not authorization.persistence:
                    tools.read_only=True;ctx['starting_state']='AUTHENTICATED_READ_ONLY_NO_CREATION_AUTHORIZATION'
                    ctx['discovery_reason']='EXPLICIT_CREATION_WRITE_AUTHORIZATION_REQUIRED'
                    trace.agent_event('campaign_creation_blocked',reason=ctx['discovery_reason'],discovery_permitted=True)
                else:
                    existing=await tools.records(on_login=True)
                    if not existing.get('available'):raise V2Stop('CAMPAIGN_START_STATE_UNKNOWN',owner='recognition',unsafe=True)
                    if existing.get('count')!=0:raise V2Stop('EXISTING_CAMPAIGN_REQUIRES_REVIEWED_RESUME',owner='authorization',unsafe=True)
                    tools.write_journal('START_STATE','CHECKED')
                    ctx['starting_state']='VERIFIED_NO_EXISTING_CAMPAIGN';tools.start_state_verified=True
            authenticated_url=await browser.page.url()
            page,binding=await create_protected_tab(browser)
            browser.page=page;browser.setup_page=page;browser.setup_binding=binding
            # No creation route without duplicate proof. Discovery reopens only
            # the actually observed authenticated view in the protected task tab.
            await browser.navigate(authenticated_url if tools.read_only else SETUP_URL)
            await tools.protect();await tools.install_guard()
            return {'iteration':trace.iteration,'stage':'setup','status':'INCOMPLETE','route':'doer','turns':0,'failure':{},'finding':{},
                'route_reason':'VERIFIED_LOGIN_AUTHENTICATED_READ_ONLY_DISCOVERY' if tools.read_only else 'VERIFIED_LOGIN_AND_NO_EXISTING_CAMPAIGN'}
        except (Exception,KeyboardInterrupt,asyncio.CancelledError) as error:
            return {'iteration':trace.iteration,'stage':trace.agent_stage,'status':'BLOCKED',
                'failure':failure(error),'route':'teacher','finding':{}}

    async def doer(state):
        tools=ctx['tools'];tools.stage=state['stage'];tools.mutations=0
        try:
            payload.check_schedule()
            if monotonic()-ctx['started']>args.session_seconds:raise V2Stop('SESSION_DEADLINE',owner='environment',unsafe=True)
            if state['turns']>=args.max_turns:raise V2Stop('DOER_TURN_LIMIT')
            ctx['turn_start']=len(tools.events)
            await tools.protect()
            current=None  # Recognition is performed by the Doer's browser tool.
            agent=create_v2_doer(ctx['doer'],tools,ctx['trace'],ctx['budget'],ctx['skill'])
            ctx['trace'].agent_event('task_doer_initialized',role='doer',
                authentication_verified=ctx['authentication_verified'],status='CONSTRUCTED_NOT_YET_INVOKED')
            ctx['task_skill_read']=True
            ctx['trace'].agent_event('skill_loaded',role='doer',version=state.get('candidate') or ctx['skill'].metadata.get('version') or ctx['skill'].metadata.get('source_version'),
                content_sha256=ctx['skill'].digest,bytes_verified=True,loaded_into_task_prompt=True,
                provider_consumption='SEE_SUBSEQUENT_DISPATCH_AND_MODEL_RESULT')
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                result=await agent.ainvoke({'messages':[{'role':'user','content':json.dumps({
                    'goal':state['stage'],'expected':{k:getattr(payload,k) for k in STAGE_FIELDS[state['stage']]},
                    'observation':current,'feedback':state.get('finding'),'history':tools.events[-12:],
                    'mode':'AUTHENTICATED_READ_ONLY_UI_DISCOVERY' if tools.read_only else 'APPROVED_DRAFT_FIELDS',
                    'failed_approaches':{str(field):sorted(methods) for (stage,field),methods in tools.failed_approaches.items() if stage==state['stage']}})}]},
                    {'configurable':{'thread_id':ctx['trace'].run_id+'-'+str(state['turns'])},'recursion_limit':20})
            ctx['report']=result['structured_response'].model_dump()
            ctx['trace'].agent_event('doer_report',role='doer',result=ctx['report'])
            return {'turns':state['turns']+1,'route':'observer','route_reason':'DOER_TURN_REQUIRES_INDEPENDENT_READBACK'}
        except (Exception,KeyboardInterrupt,asyncio.CancelledError) as error:
            return {'status':'BLOCKED','failure':failure(error),'route':'teacher'}

    async def observer(state):
        tools=ctx['tools']
        try:
            attempted=tools.events[ctx.get('turn_start',0):]
            force=tools.read_only or ctx.get('report',{}).get('status') in {'VERIFY','STUCK','BLOCKED'} or any(not e['ok'] or e['action']=='select' for e in attempted)
            prior=ctx['stage_records'].get(state['stage'],{})
            finding,facts=await observe_goal(tools,ctx['observer'],ctx['trace'],ctx['budget'],attempted,
                force_interpretation=force,prior_facts=prior)
            progressed_fields=[field for field,ok in facts['fields'].items() if ok and prior.get('fields',{}).get(field) is not True]
            for field in progressed_fields:tools.failed_approaches.pop((state['stage'],field),None)
            ctx['stage_records'][state['stage']]=facts
            genuine_stuck={field:sorted(methods) for (stage,field),methods in tools.failed_approaches.items()
                if stage==state['stage'] and len(methods)>=2}
            progress=bool(progressed_fields or any(e.get('progress') for e in attempted))
            finding=finding.model_copy(update={'progress':progress})
            reason='OBSERVER_VERDICT_'+finding.status
            unsafe_category=finding.blocker_category in {'authentication','authorization','browser','uncertain_write'}
            if tools.terminal or tools.pending_write or unsafe_category:
                finding=finding.model_copy(update={'status':'BLOCKED','recoverable':False})
                reason='UNSAFE_OR_TERMINAL_BROWSER_STATE'
            elif genuine_stuck and finding.status!='COMPLETE':
                finding=finding.model_copy(update={'status':'STUCK','recoverable':False})
                reason='TWO_DISTINCT_VALID_FAILED_APPROACHES_SAME_FIELD_WITHOUT_PROGRESS'
            elif finding.status in {'INCOMPLETE','STUCK'}:
                finding=finding.model_copy(update={'status':'INCOMPLETE','recoverable':True})
                reason='VERIFIED_PROGRESS_CONTINUE' if progress else 'INCOMPLETE_VALID_RECOVERY_REMAINS'
            ctx['findings'].append(finding.model_dump())
            ctx['trace'].agent_event('observer_verdict',role='observer',verdict=finding.model_dump(),
                checks=facts,progressed_fields=progressed_fields,failed_approaches=genuine_stuck,reason=reason,
                evidence_refs=finding.evidence_refs)
            if tools.read_only:
                return {'finding':finding.model_dump(),'status':'DISCOVERY_REVIEW_REQUIRED','route':'teacher',
                    'route_reason':'AUTHENTICATED_DISCOVERY_CAPTURED_NO_CAMPAIGN_CREATION_OR_VALUE_WRITES',
                    'failure':{'code':ctx['discovery_reason'],'owner':'authorization','unsafe':True}}
            if finding.status=='COMPLETE':route='teacher' if state['stage']=='preview' else 'advance'
            elif finding.status=='INCOMPLETE' and finding.recoverable:route='doer'
            else:route='teacher'
            stop={}
            if finding.status in {'STUCK','BLOCKED','UNKNOWN'}:
                last=next((e for e in reversed(tools.events) if not e['ok']),{})
                stop={'code':tools.terminal or ('PERSISTENCE_UNCERTAIN' if tools.pending_write else finding.status),
                    'owner':finding.blocker_category if unsafe_category else last.get('owner','unknown'),
                    'unsafe':bool(tools.terminal or tools.pending_write or unsafe_category),
                    'failed_approaches':genuine_stuck}
            return {'finding':finding.model_dump(),'status':'VERIFIED_SAVED_DRAFT' if finding.status=='COMPLETE' and state['stage']=='preview' else finding.status,
                'route':route,'route_reason':reason,'failure':stop}
        except (Exception,KeyboardInterrupt,asyncio.CancelledError) as error:
            return {'status':'BLOCKED','failure':failure(error),'route':'teacher'}

    async def advance(state):
        tools=ctx['tools']
        try:
            # Revalidate after Observer await; controller-only certificate is one-use.
            fresh=await deterministic_readback(tools)
            if not fresh['stage_visible'] or not all(fresh['fields'].values()) or not all(fresh['checks'].values()):raise V2Stop('STAGE_CERTIFICATE_STALE',owner='recognition')
            tools.next_allowed=True
            result=await tools.advance()
            next_stage=STAGES[STAGES.index(state['stage'])+1]
            tools.stage=next_stage;tools.references.clear();tools.images.clear()
            ctx['report']={'status':'VERIFY'};ctx['turn_start']=len(tools.events)-1
            return {'stage':next_stage,'route':'observer','status':'INCOMPLETE',
                'route_reason':'PERSISTENCE_INDEPENDENTLY_VERIFIED_REOBSERVE_NEXT_STAGE'}
        except (Exception,KeyboardInterrupt,asyncio.CancelledError) as error:
            return {'status':'BLOCKED','failure':failure(error),'route':'teacher'}

    async def teacher(state):
        return await review_v2_attempt(ctx,state,authorization,payload)

    async def candidate(state):
        return await store_v2_candidate(ctx,state,authorization,payload,ui,task_digest)

    async def cleanup(state):
        nonlocal total_requests
        browser=ctx.get('browser');trace=ctx['trace'];tools=ctx.get('tools')
        if browser:
            try:await browser.close()
            except BaseException:trace.cleanup_result='CLEANUP_UNCONFIRMED'
        total_requests+=trace.openai_api_calls
        measured=usage(trace)
        login_usage=(ctx['login_usage'] or {}).get('doer')
        task_usage={}
        for key,value in measured['doer'].items():
            prior=login_usage.get(key) if login_usage else (0 if key in {'requests','provider_requests'} else None)
            task_usage[key]=value-prior if value is not None and prior is not None else None
        if not task_usage.get('provider_requests'):
            for key in ('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens'):task_usage[key]=None
        task_skill_read=ctx['task_skill_read'] and bool(task_usage.get('provider_requests'))
        reported=reported_usage(measured)
        separated=reported_usage({'login_doer':login_usage or {},'task_doer':task_usage,
            'observer':measured['observer'],'teacher':measured['teacher']})
        result={'session_id':trace.run_id,'iteration':state['iteration'],'status':state['status'],
            'failure':state.get('failure'),'real_netcore':bool(browser and browser.live_permit.started),
            'authentication_verified':ctx['authentication_verified'],
            'observer_draft_verified':state['status']=='VERIFIED_SAVED_DRAFT',
            'teacher_reviewed':trace.teacher_result=='ANALYZED','cleanup_confirmed':trace.cleanup_result=='CLOSED_AND_PROFILE_REMOVED',
            'teacher_review':ctx.get('teacher_review'),
            'teacher_rejection':ctx.get('teacher_rejection'),
            'teacher_validation':ctx.get('teacher_validation'),
            'login_verification':ctx.get('login_verification'),
            'equivalent_start':ctx['starting_state']=='VERIFIED_NO_EXISTING_CAMPAIGN',
            'starting_state':ctx['starting_state'],'loaded_skill_sha256':ctx['skill'].digest if task_skill_read else None,
            'selected_skill_sha256':ctx['skill'].digest if ctx.get('skill') else None,'task_skill_read':task_skill_read,
            'task_sha256':task_digest,'candidate':ctx.get('candidate_result'),'candidate_error':ctx.get('candidate_error'),
            'observer':ctx['findings'],'events':tools.events if tools else [],'usage':reported,
            'session_usage_roles':separated,
            'login_usage':reported_usage(ctx['login_usage']) if ctx['login_usage'] else None,'elapsed_seconds':round(monotonic()-ctx['started'],3),
            'cleanup':trace.cleanup_result,'live_certified':False,'promotion':'NOT_APPROVED'}
        result['verified']={'authentication':ctx['authentication_verified'],
            'stage_readbacks':[{'stage':stage,'checks':checks} for stage,checks in ctx['stage_records'].items()],
            'saved_draft':result['observer_draft_verified']}
        result['remaining_uncertainty']=[stage+'.'+field for stage,fields in STAGE_FIELDS.items() for field in fields
            if ctx['stage_records'].get(stage,{}).get('fields',{}).get(field) is not True]
        if not result['observer_draft_verified']:result['remaining_uncertainty'].append('PERSISTED_SAVED_DRAFT_NOT_VERIFIED')
        if not result['cleanup_confirmed']:result['remaining_uncertainty'].append('OWNED_CLEANUP_NOT_CONFIRMED')
        result['agent_artifacts']={'trace':f'{trace.run_id}-agent-trace.json','report':f'{trace.run_id}-agent-report.md'}
        ctx['result']=result
        (ctx['directory']/'result.json').write_text(json.dumps(trace.redact_agent(result),indent=2),encoding='utf-8')
        records.append(result)
        safe=bool(ctx.get('lesson') and ctx['lesson'].safe_next_iteration and not ctx['lesson'].code_fix_required and result['cleanup_confirmed'])
        if state.get('failure',{}).get('unsafe') or (tools and (tools.pending_write or tools.terminal)):safe=False
        if tools and tools.read_only:safe=False
        again=safe and authorization.fresh_iterations and state['iteration']<args.iterations and total_requests<args.total_api_budget
        return {'route':'start' if again else 'end','route_reason':'AUTHORIZED_FRESH_ITERATION_SAFE_WITHIN_BUDGET' if again else 'RUN_STOPPED_NO_AUTHORIZED_SAFE_ITERATION'}

    def traced_node(name,node):
        async def invoke(state):
            started=monotonic()
            trace=ctx.get('trace') if name!='start' else None
            if trace:
                trace.agent_stage=state.get('stage','login')
                trace.agent_phase={'doer':'task_doer','observer':'observer','teacher':'teacher'}.get(name,'controller')
                trace.agent_event('graph_node_started',node=name)
            try:update=await node(state)
            except BaseException as error:
                if ctx.get('trace'):ctx['trace'].agent_event('graph_node_failed',node=name,error=type(error).__name__)
                raise
            update.setdefault('route_reason',update.get('failure',{}).get('code') or name.upper()+'_COMPLETED')
            ctx['route_source']=name
            if ctx.get('trace'):ctx['trace'].agent_event('graph_node_finished',node=name,
                duration_ms=round((monotonic()-started)*1000,3),status=update.get('status',state.get('status')))
            return update
        return invoke

    def choose_route(state):
        trace=ctx['trace'];source=ctx['route_source']
        trace.agent_event('graph_route',source=source,destination=state['route'],
            reason=state['route_reason'],status=state.get('status'),failure=state.get('failure'))
        if source=='cleanup':
            trace.final_outcome=ctx['result']['status']
            trace.write(ctx['directory'])
            trace.write_agent_artifacts(ROOT/'outputs',result=ctx['result'])
            ctx['artifacts_written']=True
        return state['route']

    graph=StateGraph(State)
    nodes={'start':start,'doer':doer,'observer':observer,'advance':advance,'teacher':teacher,'candidate':candidate,'cleanup':cleanup}
    for name,node in nodes.items():graph.add_node(name,traced_node(name,node))
    graph.add_edge(START,'start')
    for name in nodes:graph.add_conditional_edges(name,choose_route,{**{n:n for n in nodes},'end':END})
    compiled=graph.compile(checkpointer=InMemorySaver())
    from uuid import uuid4
    lock=ROOT/'outputs'/'whatsapp-v2'/'controller.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    try:
        handle=lock.open('x',encoding='utf-8')
    except FileExistsError:raise V2Stop('ANOTHER_OR_UNCLEAN_V2_RUN_REQUIRES_REVIEW',owner='authorization') from None
    try:
        handle.write(uuid4().hex);handle.flush()
        await compiled.ainvoke({'iteration':0,'candidate':args.candidate},{'configurable':{'thread_id':uuid4().hex},'recursion_limit':500})
    except BaseException as error:
        ctx['graph_failure']=failure(error)
        if ctx.get('trace'):ctx['trace'].agent_event('graph_aborted',failure=ctx['graph_failure'])
        raise
    finally:
        try:
            browser=ctx.get('browser')
            if browser and not browser.close_started:await browser.close()
        finally:
            try:
                trace=ctx.get('trace')
                if trace and not ctx.get('artifacts_written'):
                    measured=reported_usage(usage(trace))
                    partial=ctx.get('result') or {'status':'BLOCKED','failure':ctx.get('graph_failure'),
                        'cleanup':trace.cleanup_result,'verified':{'authentication':ctx.get('authentication_verified'),
                            'stage_readbacks':[{'stage':stage,'checks':checks} for stage,checks in ctx.get('stage_records',{}).items()]},
                        'remaining_uncertainty':['GRAPH_DID_NOT_REACH_NORMAL_FINALIZATION','TASK_DOER_USAGE_SPLIT_UNAVAILABLE'],
                        'session_usage_roles':{'login_doer':reported_usage(ctx['login_usage'])['doer'] if ctx.get('login_usage') else None,
                            'task_doer':None,'observer':measured['observer'],'teacher':measured['teacher']},
                        'aggregate_doer_usage':measured['doer']}
                    trace.agent_event('abnormal_finalization',status='BLOCKED',cleanup=trace.cleanup_result)
                    trace.write(ctx['directory']);trace.write_agent_artifacts(ROOT/'outputs',result=partial)
            finally:
                handle.close();lock.unlink()
    return {'workflow':'whatsapp_v2','iterations':records,'actual_provider_requests':None,
        'provider_dispatch_ready_requests':total_requests,'provider_request_count_basis':'TRANSPORT_NOT_INDEPENDENTLY_OBSERVED',
        'graduated':False,'live_certified':False,'status':records[-1]['status'] if records else 'BLOCKED'}


def setup_field_write_status(event):
    from modules.whatsapp.contract import STAGE_FIELDS

    field = event.get('field')
    if event.get('stage') != 'setup' or event.get('action') not in {'type', 'select'} or field not in STAGE_FIELDS['setup']:
        return None
    actual = (event.get('verification') or {}).get('actual_match')
    verified = event.get('ok') is True and actual is True
    attempted = event.get('attempted') is True
    if attempted:
        write = 'WRITTEN' if verified else 'UNCERTAIN'
    else:
        write = 'ALREADY_MATCHED' if verified else 'NOT_ATTEMPTED'
    return {'field': field, 'write': write,
            'readback': 'VERIFIED' if verified else 'MISMATCH' if actual is False else 'UNVERIFIED'}


def setup_attempt_verdict(tools,facts):
    """Deterministic verdict; a model STUCK/VERIFY report grants no state fact."""
    from modules.whatsapp.contract import STAGE_FIELDS

    fields = facts.get('fields', {})
    verified = sorted(field for field, ok in fields.items() if ok is True)
    write_results = [record for event in getattr(tools, 'events', ())
                     if (record := setup_field_write_status(event)) is not None]
    written = sorted({record['field'] for record in write_results if record['write'] == 'WRITTEN'})
    campaign_write_pending = getattr(tools, 'human_assisted_setup', False) and 'campaign_name' not in written
    failed = {field: sorted(methods) for (stage, field), methods in tools.failed_approaches.items()
              if stage == tools.stage and len(methods) >= 2
              and (fields.get(field) is not True or (field == 'campaign_name' and campaign_write_pending))}
    next_field = 'campaign_name' if campaign_write_pending else next(
        (field for field in STAGE_FIELDS[tools.stage] if field in fields and fields[field] is not True), None)
    if (next_field == 'tag' and 'tag' in failed and fields.get('ga_tracking') is not True
            and 'ga_tracking' not in failed and getattr(tools, 'operator_setup_binding', None) is not None):
        next_field = 'ga_tracking'
    if tools.terminal or tools.pending_write:
        status = 'BLOCKED'
    elif next_field in failed:
        status = 'STUCK'
    elif (not campaign_write_pending and fields and facts.get('stage_visible') is True
          and all(fields.values()) and all(facts.get('checks', {}).values())):
        status = 'COMPLETE'
    else:
        status = 'INCOMPLETE'
    return {'status': status, 'verified_fields': verified, 'written_fields': written,
            'write_results': write_results, 'failed_approaches': failed, 'readback': facts,
            'next_required_field': next_field}

def setup_stopped_verdict(tools, failure):
    # Use readbacks already completed on this document. A controller failure
    # does not grant another browser action or retry a field write.
    facts = tools.last_independent_readback
    verdict = setup_attempt_verdict(tools, facts)
    return {**verdict, 'status': 'BLOCKED', 'failure': failure}


async def run_bounded_setup_doer(agent,tools,trace,*,thread_id,goal,max_turns=20):
    """One live attempt, bounded Deep Agent turns, independent field verification."""
    from modules.whatsapp.observer import deterministic_readback
    from modules.whatsapp.contract import V2Stop
    if type(max_turns) is not int or not 1<=max_turns<=30:
        raise V2Stop('INVALID_DOER_TURN_LIMIT',owner='controller')
    verdict={'status':'INCOMPLETE'}
    feedback=goal
    operator_setup = getattr(tools, 'operator_setup_binding', None) is not None
    for turn in range(1,max_turns+1):
        tools.mutations=0
        tools.turns_remaining=max_turns-turn
        trace.agent_event('task_doer_turn',role='doer',iteration=1,turn=turn,
            turns_remaining=max_turns-turn,live_retry_authorized=False)
        # Keep browser state and the shared request budget, while each
        # operator-approved controller turn starts with current evidence only.
        checkpoint = f'{thread_id}-turn-{turn}' if operator_setup else thread_id
        if operator_setup:
            observation = tools.compact_tool_observation(tools.last_observation or {})
            feedback += ' Current redacted control references: ' + json.dumps(observation, separators=(',', ':'))
        result=await agent.ainvoke({'messages':[{'role':'user','content':feedback}]},
            {'configurable':{'thread_id':checkpoint},'recursion_limit':20})
        report=result.get('structured_response')
        if report is None:raise V2Stop('MISSING_WHATSAPP_DIAGNOSTIC_REPORT',owner='model')
        if tools.terminal or tools.pending_write:
            facts = getattr(tools, 'last_independent_readback', {})
        else:
            facts = await deterministic_readback(tools)
        verdict=setup_attempt_verdict(tools,facts)
        for field in verdict['verified_fields']:
            if field != verdict['next_required_field']:
                tools.failed_approaches.pop((tools.stage,field),None)
        trace.agent_event('setup_attempt_verdict',role='observer',turn=turn,
            model_status=report.status,**verdict)
        print('SETUP_READBACK ' + verdict['status'] + ' ' +
              json.dumps(verdict['verified_fields']), flush=True)
        if verdict['status'] in {'COMPLETE','STUCK','BLOCKED'}:
            if verdict['status']!='COMPLETE' and 'failure' not in verdict:
                verdict['failure']={'code':tools.terminal or ('WRITE_UNCERTAIN' if tools.pending_write else 'STUCK'),
                    'owner':'uncertain_write' if tools.pending_write else getattr(tools,'terminal_owner','recognition'),
                    'unsafe':bool(tools.pending_write or getattr(tools, 'terminal_unsafe', bool(tools.terminal))),
                    'failed_approaches':verdict['failed_approaches']}
            return verdict
        if verdict['next_required_field'] == 'campaign_name' and 'campaign_name' not in verdict['written_fields']:
            next_instruction = 'Write Campaign Name and obtain independent readback, including if it already matches. '
        else:
            next_instruction = ''
        if getattr(tools, 'operator_setup_binding', None) is not None and verdict['next_required_field'] == 'ga_tracking':
            next_instruction += 'Continue with the independently bound GA Tracking control. Keep any exhausted Tag binding unresolved. '
        feedback_facts = {key: verdict[key] for key in ('next_required_field', 'verified_fields',
                          'written_fields', 'failed_approaches')} if operator_setup else verdict
        feedback=('Continue the same authorized Setup attempt. ' + next_instruction + 'Use the latest actionable reference '
            'for the next required field. A successful type or select already includes independent readback. '
            'Observe only when fresh references are needed, then use dom if semantic discovery fails. Inspect takes '
            'reference only. Never retry uncertain writes. Stop before Next Step. Deterministic feedback: '
            + json.dumps(feedback_facts, separators=(',', ':')))
    verdict['failure']={'code':'DOER_TURN_BUDGET_EXHAUSTED','owner':'controller','unsafe':False}
    return verdict


def prompt_for_verified_setup(console, observation, tenant, timeout_seconds):
    from modules.login.verifier import verify
    from modules.whatsapp.contract import STAGE_FIELDS

    if observation is None or verify(observation) != Result.AUTHENTICATED_AND_VERIFIED:
        return False
    print(f'Verified tenant {tenant}. Scope is WhatsApp Setup only. '
          'Approved fields are ' + ', '.join(STAGE_FIELDS['setup']) +
          '. This run stops before Next Step.', flush=True)
    return console.confirm_setup_write(timeout_seconds)


def human_assisted_authorization(args, *, verified, operator_confirmed=False):
    from modules.whatsapp.contract import V2Authorization, V2Stop

    manual = getattr(args, 'manual_form_test', False)
    if type(manual) is not bool:
        raise V2Stop('INVALID_MANUAL_FORM_TEST_FLAG', owner='authorization')
    admitted = verified or (operator_confirmed is True and getattr(args, 'operator_confirmed_login', False) is True)
    return V2Authorization(
        live=getattr(args, 'authorized_live', False),
        inference=getattr(args, 'authorized_inference', False),
        edits=manual and admitted,
        manual_form_test=manual and admitted,
        persistence=False,
        learning=getattr(args,'authorized_learning',False),
        candidate_evaluation=getattr(args,'authorized_candidate_evaluation',False),
    )


def human_assisted_review_kind(state, evaluation):
    failure = state.get('failure') or evaluation.get('failure') or {}
    if (failure.get('unsafe') or failure.get('code') == 'OPERATOR_CANCELLED' or
            failure.get('owner') in {'authorization', 'authentication', 'uncertain_write', 'browser'}):
        return None
    if (failure.get('code') == 'CODE_FIX_REQUIRED' or failure.get('owner') in {'controller', 'tool'}
            or (failure.get('failing_stage') or '').startswith(('audience_', 'content_', 'schedule_'))):
        return 'technical_analysis'
    if evaluation.get('status') == 'COMPLETE':
        return 'success_review'
    if failure.get('code') == 'STUCK' and failure.get('owner') == 'recognition':
        return 'recoverable_strategy'
    return 'analysis_only' if failure else None


async def run_human_assisted(args):
    """Run one Login attempt, then route to verified or separately consented read-only Doer."""
    import sys
    from dataclasses import replace
    from urllib.parse import urlsplit
    from browser.discovery import ReadOnlyDiscovery,login_page_context
    from browser.evidence_review import EvidenceReview
    from browser.operator_console import OperatorConsole
    from browser.protected_tabs import create_protected_tab
    from browser.stagehand_client import BrowserBudgetExceeded,StagehandClient
    from deep_agents.permissions import InferenceBudget,TokenPool,model_failure_details
    from learning.skill_storage.registry import SkillStore, module_baseline, whatsapp_skill
    from modules.login.credentials import CredentialBroker,LocalCredentials
    from modules.login.evidence import stable_authentication
    from modules.login.verifier import verify
    from modules.whatsapp.agent import create_v2_doer
    from modules.whatsapp.contract import SETUP_URL, V2Stop, ReviewedUI, load_v2_payload
    from modules.whatsapp.tools import V2BrowserTools
    from modules.whatsapp.diagnostic_artifacts import append_live_terminal_event,write_human_assisted_artifacts
    from app.config import Config
    from app.model_settings import ModelSettings
    from time import monotonic

    validate_human_assisted_authorization(args,24)
    if not sys.stdin.isatty():
        raise ConfigurationError('Human-assisted diagnostic requires an interactive VS Code terminal')
    review_seconds=getattr(args,'operator_review_seconds',300)
    session_seconds=getattr(args,'session_seconds',900)
    if type(review_seconds) is not int or not 1<=review_seconds<=600:
        raise ConfigurationError('Operator review must be bounded to 1-600 seconds')
    if type(session_seconds) is not int or not 30<=session_seconds<=900:
        raise ConfigurationError('Diagnostic session must be bounded to 30-900 seconds')
    max_turns=getattr(args,'max_turns',20)
    operation_seconds=getattr(args,'browser_operation_seconds',30)
    tool_limit=getattr(args,'doer_tool_budget',160)
    if type(max_turns) is not int or not 1<=max_turns<=30:
        raise ConfigurationError('Doer turn limit must be 1-30')
    if type(operation_seconds) is not int or not 1<=operation_seconds<=30:
        raise ConfigurationError('Browser operation timeout must be 1-30 seconds')
    if type(tool_limit) is not int or not 1<=tool_limit<=240:
        raise ConfigurationError('Doer tool budget must be 1-240')

    config = Config.load()
    manual_requested = getattr(args, 'manual_form_test', False)
    operator_confirmed_mode = getattr(args, 'operator_confirmed_login', False)
    page1 = getattr(args, 'page1_through_audience', False)
    through_schedule = getattr(args, 'through_schedule', False)
    through_audience = getattr(args, 'through_audience', False)
    save_schedule_draft = getattr(args, 'save_schedule_draft', False)
    if save_schedule_draft and not through_schedule:
        raise ConfigurationError('Draft save requires the Through Schedule launcher')
    if (through_schedule or through_audience) and not page1 or through_schedule and through_audience:
        raise ConfigurationError('Select one operator-confirmed campaign walkthrough endpoint')
    if page1 and (not operator_confirmed_mode or not manual_requested or
                  getattr(args, 'operator_confirmed_runs', 1) != 1):
        raise ConfigurationError('Page 1 through Audience requires one operator-confirmed Setup run')
    operator_login = getattr(args, 'operator_login', False) or operator_confirmed_mode
    if operator_login and not manual_requested:
        raise ConfigurationError('OPERATOR_LOGIN_REQUIRES_MANUAL_FORM_TEST')
    private_evidence_path = getattr(args, 'login_evidence_file', None) or config.evidence_file
    if manual_requested and not private_evidence_path:
        private_evidence_path = ROOT / 'credentials' / 'login-evidence-reviewed.json'
    if operator_confirmed_mode:
        private_evidence_path = None
        evidence, evidence_status = None, 'OPERATOR_CONFIRMATION_MODE'
    else:
        evidence, evidence_status = human_assisted_login_evidence(private_evidence_path)
    if manual_requested and evidence is None and not operator_login:
        raise ConfigurationError(
            f'{evidence_status} at {private_evidence_path}. Setup requires a reviewed visible dashboard marker, '
            'a visible exact tenant marker, and a reviewed authenticated read-only resource contract for that tenant. '
            'The launch stopped before Chromium.')
    settings=ModelSettings.load()
    caps=validate_human_assisted_authorization(args,settings.cycle_requests)
    if not settings.api_key:
        raise ConfigurationError('OPENAI_API_KEY is required for the authorized live Doer')
    task_output = human_assisted_output_budget(settings, caps, writable=manual_requested)
    trace=Trace(roles=('doer','observer','teacher'))
    trace.reasoner_mode='deepagents_openai_gpt-6-luna'
    trace.enable_agent_trace()
    trace.teacher_result='NOT_INVOKED' if getattr(args,'authorized_learning',False) else 'NOT_INVOKED_DIAGNOSTIC_ONLY'
    artifact_root=ROOT/'outputs'/'human-assisted'
    artifact_dir=artifact_root/f'login-whatsapp-{trace.run_id}'
    artifact_dir.mkdir(parents=True,exist_ok=False)
    terminal_log_path=artifact_dir/'live-terminal.log'
    terminal_log_path.write_text('',encoding='utf-8')
    quiet_terminal = False

    def stream_event(event):
        line=append_live_terminal_event(terminal_log_path,event,trace)
        if line and not quiet_terminal:
            print(line.rstrip(),flush=True)
        if manual_requested and event.get('kind') == 'browser_action':
            status = setup_field_write_status(event)
            if status:
                print(f"SETUP_FIELD {status['field']} write={status['write']} readback={status['readback']}", flush=True)
    trace.agent_event_sink=stream_event
    trace.stream_browser_diagnostics=True
    trace.agent_event('graph_started',role='controller',workflow='HUMAN_ASSISTED_LOGIN_WHATSAPP')
    trace.agent_event(
        'operator_timeout_config',
        role='controller',
        initial_inactivity_seconds=min(300,review_seconds,session_seconds),
        maximum_review_seconds=min(600,review_seconds,session_seconds),
        session_timeout_seconds=session_seconds,
    )

    print(f'LOGIN_EVIDENCE {evidence_status}', flush=True)
    if operator_login and evidence is None:
        print('Starting manual sign-in. Setup requires independently verified tenant and authenticated access.', flush=True)
    if evidence is None and not operator_login:
        print('Use --login-evidence-file or NETCORE_EVIDENCE_FILE with a reviewed document. '
              'The existing Login evidence review is documented in docs/operations.md '
              'and available through app.cli live --login-only --authorized-live --api-request-budget 6 '
              '--hold-seconds 300 --review-evidence --evidence-review-file <PRIVATE_FILE>.', flush=True)
    trace.agent_event('login_evidence_configuration', role='controller', status=evidence_status)
    trace.agent_event('model_budget_configured', role='controller', requests=caps,
        input_limit=settings.cycle_input, output_limit=settings.cycle_output,
        login_output_cap=settings.login_output, task_output_cap=task_output,
        teacher_input_reserve=caps['teacher'] * settings.teacher_input,
        teacher_output_reserve=caps['teacher'] * settings.teacher_output)
    config=human_assisted_browser_config(config,session_seconds=session_seconds,
        evidence_file=private_evidence_path,browser_actions=getattr(args,'browser_actions',300))
    permit=authorize_live(config,True,caps['login'])
    credentials=LocalCredentials.load()
    skill=module_baseline('whatsapp')
    baseline_sha = getattr(args, 'baseline_sha256', None)
    candidate_version = getattr(args, 'candidate', None)
    candidate_sha = getattr(args, 'candidate_sha256', None)
    if baseline_sha is not None and baseline_sha.lower() != skill.digest:
        raise ConfigurationError('WHATSAPP_BASELINE_SHA256_MISMATCH')
    if bool(candidate_version) != bool(candidate_sha) or candidate_sha and not re.fullmatch(r'[a-fA-F0-9]{64}', candidate_sha):
        raise ConfigurationError('Use --candidate VERSION together with --candidate-sha256 SHA256')
    setup_parts = urlsplit(SETUP_URL)
    setup_ui = ReviewedUI(origin=f'{setup_parts.scheme}://{setup_parts.netloc}',
                          tenant=setup_parts.path.strip('/').split('/')[0])
    approved_payload = ('a47cf5865bfaccfac9b7a4de7a4fa6ddec074aa21f73c52391e0a6ad89b0361d' if page1
                        else '594cfa8b180477ba5f90f279851e8a8f0fbe1b31123a4b6a758950a8c0cc88ac')
    if through_audience or through_schedule:
        approved_payload = 'a4ec605b8f742e8d9406260c8a83a6368614064251089bbc757c088fc6f14302'
    payload_path = ROOT / 'credentials/whatsapp-v2-payload.json'
    payload, digest = load_v2_payload(payload_path, approved_digest=approved_payload)
    if save_schedule_draft:
        resolved_schedule = payload.scheduled_local()
        print('SCHEDULE_PAYLOAD ' + json.dumps({'path':str(payload_path),'schedule':resolved_schedule.isoformat(),
            'timezone':payload.timezone}), flush=True)
    if through_schedule:
        print('CONTENT_PAYLOAD ' + json.dumps({'path': str(payload_path), 'template': payload.template}), flush=True)
    if through_audience or through_schedule:
        print('AUDIENCE_PAYLOAD ' + json.dumps({'path': str(payload_path), 'mode': payload.audience_type,
            'include': payload.audience_values('include_audience'),
            'exclude': payload.audience_values('exclude_audience')}), flush=True)
    selected_candidate = None
    if candidate_version:
        selected_candidate = whatsapp_skill(candidate=candidate_version,
            evaluate=args.authorized_candidate_evaluation, task_digest=digest, ui=setup_ui)
        if selected_candidate.digest != candidate_sha.lower():
            raise ConfigurationError('WHATSAPP_CANDIDATE_SHA256_MISMATCH')
        if any(step.get('goal') != 'setup' or step.get('action') == 'screenshot'
               for step in selected_candidate.metadata.get('procedures', [])):
            raise ConfigurationError('CANDIDATE_OUTSIDE_MANUAL_SETUP_SCOPE')
    budget=InferenceBudget(caps['total'],reserve_teacher=bool(caps.get('teacher')),role_limits={
        'doer':caps['login'],'observer':caps['observer'],'teacher':caps.get('teacher',0)})
    budget.token_pool=TokenPool(settings.cycle_input,settings.cycle_output)
    budget.max_input_tokens=settings.cycle_input
    budget.max_output_tokens=settings.cycle_output
    if caps.get('teacher'):
        budget.teacher_reserve_input=settings.teacher_input
        budget.teacher_reserve_output=settings.teacher_output
    browser=StagehandClient(config,OriginPolicy(set(permit.origins)),trace,
        fixture=False,evidence=evidence,live_permit=permit)
    # Match standalone Login verification for an ordinary reviewed document.
    # Alternative resource contracts still require current passive evidence.
    browser.evidence_review = not operator_confirmed_mode and (
        evidence is None or evidence.authenticated_value_sha256 is not None)
    browser.rendering_diagnostics_enabled=False
    login_tools=HandoffLoginTools(browser,CredentialBroker(browser,credentials))
    login_tools.strategy=Strategy()
    context={
        'browser':browser,'login_tools':login_tools,'login_result':None,
        'observation':None,'session_safe':False,'source_context':None,'source_url':None,
        'diagnostic_consent':False,'doer_requested':False,'selected_navigation':None,
        'selected_candidates':{},'candidate_evidence':{},'resource_evidence':None,
        'shadow_scope':None,'operator_decisions':[],'review':None,'tools':None,
        'task_doer_invoked':False,'task_doer_status':'NOT_INVOKED',
        'failure_stage':'login_handoff',
        'skill':skill,'caps':caps,'started':monotonic(),'status':'BLOCKED_OPERATOR_REVIEW',
    }

    def record_operator_decision(event):
        context['operator_decisions'].append(dict(event))
        trace.agent_event('operator_decision',role='operator',**event)

    def source_identity(page_context):
        keys=('primary_target_alias','primary_session_alias','generation',
            'document_generation','page_path_sha256')
        return tuple(page_context.get(key) for key in keys)

    async def refresh_authentication(*, print_result=True):
        proof=await human_assisted_session_safety(browser)
        current=proof['page_context']
        url=await browser.page.url()
        safe=proof['diagnostic_session_safe']
        context['diagnostic_safety']=proof
        trace.agent_event('diagnostic_session_safety',role='controller',**proof)
        context['session_safe']=safe
        context['source_context']=source_identity(current)
        context['source_url']=url if safe else None
        if safe:
            observation = await stable_authentication(browser)
            current_proof = await human_assisted_session_safety(browser)
            if (not current_proof['diagnostic_session_safe'] or
                    source_identity(current_proof['page_context']) != context['source_context']):
                raise V2Stop('LOGIN_DOCUMENT_CHANGED_DURING_VERIFICATION', owner='authentication', unsafe=True)
            context['observation']=observation
            context['verification']=str(verify(observation))
            context['gate_status']=(browser.verification_details or {}).get('gate_status',{})
            login_result=context.get('login_result')
            if login_result is not None:
                login_result.update(verification=context['verification'],
                    reason_code=(browser.verification_details or {}).get('reason',login_result.get('reason_code')),
                    missing_evidence=(browser.verification_details or {}).get('missing',[]))
            trace.agent_event('login_verification',role='doer',
                verification=context['verification'],session_safe=True,
                gate_status=context['gate_status'])
            if print_result:
                print('LOGIN_VERIFICATION ' + context['verification'] + ' ' +
                      json.dumps(context['gate_status'],sort_keys=True), flush=True)
        else:
            context['observation']=None
            context['verification']='NOT_VERIFIED'
            context['gate_status']=(browser.verification_details or {}).get('gate_status',{})
            trace.agent_event('login_verification',role='doer',
                verification='NOT_VERIFIED',session_safe=False,
                reason='CURRENT_AUTHORIZED_DOCUMENT_NOT_PROVEN')
        return context['observation']

    async def refresh_navigation_binding():
        binding=context['selected_navigation']
        if binding is None:return
        try:
            context['selected_navigation']=await context['review'].bind_candidate(binding['reference'])
        except (PermissionError,TimeoutError,ValueError) as error:
            context['selected_navigation']=None
            trace.agent_event('operator_candidate_rejected',role='operator',
                reason=getattr(error,'code',type(error).__name__),stale=True)

    def prompt_limits(deadline):
        return human_assisted_prompt_limits(deadline)

    def safe_candidate_row(row):
        return {key:row.get(key) for key in (
            'reference','kind','visible','element_type','role','collector_gate',
            'scope','binding_available','confidence')}

    async def inspect_candidates(kind,console,deadline):
        review=context['review']
        if review is None:
            print('Observed candidate references are unavailable; no browser action was taken.',flush=True)
            return
        used_fallback=False
        if kind=='navigation':
            rows,used_fallback=await human_assisted_navigation_candidates(review)
            if used_fallback:
                print('No classified WhatsApp navigation candidate was found; showing current visible link samples.',flush=True)
        else:
            rows=await review.operator_candidates(kind)
        rows=rows[:8]
        context['candidate_evidence'][kind]=[safe_candidate_row(row) for row in rows]
        print(f"Observed {kind} candidates: "+json.dumps(
            context['candidate_evidence'][kind],ensure_ascii=True),flush=True)
        if not rows:
            return
        choices={}
        for index,row in enumerate(rows,1):
            choices[f'candidate_{index}']=(
                f"Bind current visible {kind} candidate #{index}: "
                f"{row.get('element_type','UNKNOWN')} / {row.get('role','UNKNOWN')} / "
                f"{row.get('scope','UNKNOWN')} / {row.get('collector_gate','UNKNOWN')}")
        choices['none']='Do not bind a candidate'
        timeout,maximum=prompt_limits(deadline)
        selected=console.choose('bind_'+kind,choices,timeout,maximum,default='none')
        if selected=='none':
            return
        index=int(selected.split('_',1)[1])-1
        row=rows[index]
        binding=await review.bind_candidate(row['reference'])
        if binding.get('state')!='CURRENT_VISIBLE_DOM_REFERENCE' or binding.get('kind')!=row.get('kind'):
            trace.agent_event('operator_candidate_rejected',role='operator',
                candidate_kind=kind,reason='CURRENT_VISIBLE_BINDING_NOT_CONFIRMED')
            return
        context['selected_candidates'][kind]=binding
        if kind=='navigation':
            context['selected_navigation']=binding
        trace.agent_event('operator_candidate_bound',role='operator',
            reference=binding['reference'],candidate_kind=binding['kind'],
            document_generation=binding['document_generation'],
            scope=binding['scope'],collector_gate=binding['collector_gate'],
            selector_sha256=binding['selector_sha256'],operator_fallback=used_fallback,
            authentication_proof=False)

    async def inspect_resources(console,deadline):
        review=context['review']
        if review is None:
            print('Protected-resource evidence is unavailable.',flush=True)
            return
        options=await review.resource_options()
        safe_options=human_assisted_resource_summary(options)
        context['resource_evidence']=safe_options
        trace.agent_event('protected_resource_review',role='operator',
            status=safe_options.get('status'),digest=safe_options.get('digest'),
            option_count=len(safe_options.get('options',[])),authentication_proof=False)
        print('Protected-resource evidence: '+json.dumps(safe_options,ensure_ascii=True),flush=True)
        available=options.get('options',[])[:8]
        if not available or not options.get('digest'):
            return
        choices={f'resource_{item["option"]}':f"Select observed resource contract #{item['option']}"
            for item in available}
        choices['none']='Do not select a resource contract'
        timeout,maximum=prompt_limits(deadline)
        selected=console.choose('select_resource_contract',choices,timeout,maximum,default='none')
        if selected=='none':
            return
        option=int(selected.split('_',1)[1])
        selected_result=await review.select_resource_contract(
            option,options['digest'],confirmed=True)
        trace.agent_event('resource_contract_selected',role='operator',
            status=selected_result.get('status'),digest=selected_result.get('digest'),
            verification_basis=selected_result.get('verification_basis'),
            authentication_proof=False)
        print('Resource contract selected as an unreviewed draft; this is not authentication proof.',flush=True)

    async def confirm_operator_tenant():
        nonlocal quiet_terminal
        from browser.protected_tabs import bind_confirmed_primary_tab
        from modules.whatsapp.browser_baseline import reject_security_negatives
        from modules.whatsapp.destination import navigation_url_difference

        binding = bind_confirmed_primary_tab(browser, route_allowed=operator_tenant_route_allowed)
        deadline = monotonic() + min(review_seconds, human_assisted_remaining_seconds(session_seconds, context['started']))
        context['last_verified_step'] = 'PROTECTED_TENANT_ROUTE_OBSERVED'
        console = OperatorConsole(event_sink=record_operator_decision)

        async def sample():
            await binding.require_current(browser, allow_document_transition=True)
            url = await browser.page.url()
            if not operator_tenant_route_allowed(url):
                raise V2Stop('OPERATOR_TENANT_ROUTE_CHANGED', owner='authorization', unsafe=True)
            proof = await human_assisted_session_safety(browser, operator_route=True)
            return url, proof

        def changes(previous, current):
            old_url, old = previous
            new_url, new = current
            before, after = old['page_context'], new['page_context']
            properties = [key for key in ('primary_target_alias', 'primary_session_alias', 'generation',
                         'document_generation', 'page_path_sha256', 'primary_document_coherent', 'sdk_page_active')
                          if before.get(key) != after.get(key)]
            return properties + ['url_' + key for key in navigation_url_difference(old_url, new_url)]

        while monotonic() < deadline:
            before = await sample()
            await asyncio.sleep(min(config.login_stable_seconds, max(0, deadline - monotonic())))
            after = await sample()
            if changes(before, after) or not after[1]['diagnostic_session_safe']:
                continue
            await reject_security_negatives(browser)
            # Show the checked origin and path locally. Query content may hold private session data.
            url, proof = after
            parsed = urlsplit(url)
            display_url = parsed._replace(query='', fragment='').geturl() + ('?[redacted]' if parsed.query else '')
            if parsed.fragment:
                display_url += '#[redacted]'
            seconds = int(max(0, deadline - monotonic()))
            quiet_terminal = True
            try:
                confirm = console.confirm_page1_setup if page1 else console.confirm_tenant_route
                decision = await confirm(display_url, seconds) if seconds > 0 else 'timeout'
            finally:
                quiet_terminal = False
            if decision != 'yes':
                context['operator_aborted'] = True
                context['operator_stop_status'] = ('OPERATOR_TENANT_DECLINED' if decision == 'no'
                                                   else 'OPERATOR_TENANT_CONFIRMATION_TIMEOUT')
                return
            current = await sample()
            changed = changes(after, current)
            if changed or not current[1]['diagnostic_session_safe']:
                trace.agent_event('operator_tenant_context_changed', role='controller',
                    code='OPERATOR_TENANT_RECONFIRMATION_REQUIRED', changed_properties=changed,
                    failed_predicates=current[1]['failed_predicates'],
                    before_context=proof['page_context'], after_context=current[1]['page_context'])
                print('OPERATOR_TENANT_RECONFIRMATION_REQUIRED ' + json.dumps({
                    'changed_properties': changed, 'failed_predicates': current[1]['failed_predicates']}), flush=True)
                continue
            await reject_security_negatives(browser)
            await binding.require_current(browser)
            verified_url = await browser.page.url()
            if verified_url != url:
                changed = ['url_' + key for key in navigation_url_difference(url, verified_url)]
                trace.agent_event('operator_tenant_context_changed', role='controller',
                                  code='OPERATOR_TENANT_RECONFIRMATION_REQUIRED', changed_properties=changed)
                print('OPERATOR_TENANT_RECONFIRMATION_REQUIRED ' + json.dumps(changed), flush=True)
                continue
            context.update(operator_tab=binding, source_context=source_identity(proof['page_context']),
                           source_url=url, session_safe=True, verification='OPERATOR_CONFIRMED',
                           last_verified_step='OPERATOR_CONFIRMED')
            if page1:
                context['page1_setup_approval'] = (binding, payload, digest)
                trace.agent_event('page1_setup_approval', role='operator', accepted=True,
                                  payload_sha256=digest, authentication_verified=False)
            trace.agent_event('login_verification', role='operator', verification='OPERATOR_CONFIRMED',
                              authentication_verified=False, session_safe=True, page_context=proof['page_context'])
            print('LOGIN_VERIFICATION OPERATOR_CONFIRMED', flush=True)
            return
        context['operator_stop_status'] = 'OPERATOR_TENANT_CONFIRMATION_TIMEOUT'

    async def review_and_ask_operator(active_browser,active_tools,login_result):
        nonlocal quiet_terminal
        if active_browser is not browser or active_tools is not login_tools:
            raise V2Stop('LOGIN_SESSION_BINDING_CHANGED',owner='browser',unsafe=True)
        from browser.diagnostics import owned_window_observation
        trace.agent_event('browser_window_observation',role='controller',**owned_window_observation(browser))
        if browser.page is None or browser.guard is None or browser.guard.failed or browser.guard.blocking_hosts:
            context['session_safe']=False
            if manual_requested:
                print('LOGIN_CHECK_FAILED CURRENT_PROTECTED_LOGIN_DOCUMENT_UNAVAILABLE', flush=True)
            return
        browser.login_page=browser.page
        console=OperatorConsole(event_sink=record_operator_decision)
        context['login_result']=login_result
        if operator_confirmed_mode:
            if page1:
                if browser.login_submissions != 1 or active_tools.terminal_error:
                    raise V2Stop('LOGIN_DOER_SINGLE_SUBMISSION_NOT_ESTABLISHED', owner='authentication', unsafe=True)
                if login_result.get('verification') in {str(Result.AUTH_FAILED), str(Result.SESSION_EXPIRED),
                                                        str(Result.HUMAN_ACTION_REQUIRED)}:
                    raise V2Stop('LOGIN_DOER_SECURITY_STOP', owner='authentication', unsafe=True)
            await confirm_operator_tenant()
            return
        if manual_requested:
            from modules.whatsapp.browser_baseline import reject_security_negatives

            if active_tools.terminal_error:
                print('LOGIN_CHECK_FAILED TERMINAL_LOGIN_TOOL_STOP', flush=True)
                context['manual_authentication_blocked'] = True
                return
            if operator_login:
                # The first tenant document can commit while its proof elements are still loading.
                # Only the independent verifier can open the write prompt.
                remaining = human_assisted_remaining_seconds(session_seconds, context['started'])
                deadline = monotonic() + min(config.login_ready_seconds, max(0, remaining - 1))
                quiet_terminal = True
                try:
                    while True:
                        observation = await refresh_authentication(print_result=False)
                        details = browser.verification_details or {}
                        retry = (evidence is not None and context['session_safe'] and observation is not None
                                 and observation.allowed and not details.get('tenant_mismatch')
                                 and verify(observation) == Result.UNEXPECTED_PAGE)
                        if not retry or monotonic() + 2 >= deadline:
                            break
                        await asyncio.sleep(2)
                finally:
                    quiet_terminal = False
                print('LOGIN_VERIFICATION ' + context['verification'] + ' ' +
                      json.dumps(context['gate_status'], sort_keys=True), flush=True)
            else:
                observation = await refresh_authentication()
            if (not context['session_safe'] or observation is None or
                    verify(observation) != Result.AUTHENTICATED_AND_VERIFIED):
                details = browser.verification_details or {}
                print('LOGIN_CHECK_FAILED ' + json.dumps({
                    'reason': details.get('reason') if context['session_safe'] else 'CURRENT_AUTHORIZED_DOCUMENT_NOT_PROVEN',
                    'gates': context['gate_status'], 'issues': details.get('issues', []),
                    'session_checks': (context.get('diagnostic_safety') or {}).get('failed_predicates', []),
                }, sort_keys=True), flush=True)
                context['manual_authentication_blocked'] = True
                return
            await reject_security_negatives(browser)
            context['doer_requested'] = True
            return

        trace.agent_event('diagnostic_consent_requested',role='operator',authentication_verified=False)
        accepted=console.confirm_readonly(min(300,review_seconds))
        context['diagnostic_consent']=accepted
        trace.agent_event('diagnostic_consent_decision',role='operator',accepted=accepted,authentication_verified=False)
        if not accepted:
            context['operator_aborted']=True
            return
        await refresh_authentication()
        from modules.whatsapp.browser_baseline import reject_security_negatives
        await reject_security_negatives(browser)
        if not context['session_safe']:
            print('DIAGNOSTIC_SESSION_UNSAFE: '+json.dumps(context['diagnostic_safety']['failed_predicates']),flush=True)
            return
        context['doer_requested']=True

    async def inspect_failed_operator_login():
        from browser.inspection import inspection_hold

        if browser.browser is None:
            return
        remaining = human_assisted_remaining_seconds(session_seconds, context['started'])
        seconds = min(300, review_seconds, int(remaining))
        if not seconds:
            return
        try:
            await inspection_hold(seconds, prompt=(
                f'Chromium is open for inspection. Press Enter to close it, or wait {seconds} seconds.'))
        except TimeoutError:
            pass

    async def report_missing_operator_login_signals():
        discovery = getattr(browser, 'discovery', None)
        if not context['session_safe'] or discovery is None or not discovery.enabled:
            return
        observation = context.get('observation')
        if observation is None or any(getattr(observation, field) for field in
                ('login_entry', 'credential_form', 'challenge', 'rejected', 'expired', 'account_restricted')):
            return
        from browser.evidence_diagnostics import evidence_diagnostic
        try:
            async with asyncio.timeout(15):
                report = await discovery.prepare(calibration_only=True)
            dom = report.get('dom_diagnostics', {})
            details = {
                'failed_checks': report['failed_checks'],
                'scope': report['scope_certificate']['state'],
                'dashboard_rejections': dom.get('dashboard_visibility_rejections', {}),
                'tenant_rejections': dom.get('rejections', {}).get('tenant', {}),
                'resources': report['resource_diagnostics'],
                'authentication_verified': False,
            }
        except Exception as error:
            details = {'failed_checks': [evidence_diagnostic(error, 'DISCOVERY', browser)['code']]}
        trace.lifecycle('OPERATOR_LOGIN_SIGNALS', {'state': 'UNVERIFIED_SIGNAL_CHECK', **details})
        print('LOGIN_SIGNAL_CHECK ' + json.dumps(details, sort_keys=True), flush=True)

    async def run_operator_login(supervised_review):
        nonlocal quiet_terminal
        from browser.evidence_diagnostics import evidence_diagnostic

        result = {'verification': 'NOT_TESTED', 'reason_code': 'NOT_TESTED'}
        trace.agent_event('operator_login_started', role='operator', automated_submissions=0)
        stage = 'BROWSER_START'
        try:
            await browser.start()
            browser.login_page = browser.page
            if browser.evidence_review:
                browser.discovery = ReadOnlyDiscovery(browser)
                try:
                    await browser.discovery.start()
                except Exception as error:
                    details = evidence_diagnostic(error, 'DISCOVERY', browser)
                    trace.lifecycle('OPERATOR_LOGIN_DISCOVERY', details)
                    print('LOGIN_OBSERVER ' + details['code'], flush=True)
            stage = 'LOGIN_ENTRY_NAVIGATION'
            await browser.navigate(login_tools.entry_url)
            stage = 'OPERATOR_SIGN_IN'
            remaining = human_assisted_remaining_seconds(session_seconds, context['started'])
            seconds = min(300, review_seconds, int(remaining))
            next_step = ('Confirm the tenant shown in Netcore when prompted.' if operator_confirmed_mode else
                         'The approval prompt appears only after independent Login verification.')
            print(f'Sign in once in Chromium. Waiting up to {seconds} seconds for tenant {setup_ui.tenant}. '
                  + next_step, flush=True)
            proof = {'failed_predicates': ['protected_document_not_observed']}
            quiet_terminal = True
            try:
                sign_in = asyncio.timeout(seconds)
                async with sign_in:
                    while True:
                        if browser.guard.failed or browser.guard.blocking_hosts:
                            raise V2Stop('LOGIN_NAVIGATION_GUARD_STOP', owner='browser', unsafe=True)
                        proof = await human_assisted_session_safety(browser, operator_route=operator_confirmed_mode)
                        if proof['diagnostic_session_safe']:
                            break
                        await asyncio.sleep(2)
            except TimeoutError:
                if not sign_in.expired():
                    raise
                print('LOGIN_SESSION_CHECKS ' + json.dumps(proof['failed_predicates']), flush=True)
                raise V2Stop('OPERATOR_SIGN_IN_TIMEOUT', owner='browser') from None
            finally:
                quiet_terminal = False
            stage = 'INDEPENDENT_LOGIN_VERIFICATION'
            await supervised_review(browser, login_tools, result)
            if not operator_confirmed_mode and not context.get('doer_requested'):
                if evidence is None:
                    await report_missing_operator_login_signals()
                await inspect_failed_operator_login()
        except Exception as error:
            if not context.get('handoff_failure'):
                details = evidence_diagnostic(error, stage, browser)
                code = getattr(error, 'code', None) or details['code']
                if stage == 'LOGIN_ENTRY_NAVIGATION' and code == 'BROWSER_OBSERVATION_UNAVAILABLE':
                    code = 'LOGIN_ENTRY_NAVIGATION_TIMEOUT'
                context['handoff_failure'] = {
                    'code': code,
                    'owner': getattr(error, 'owner', 'browser'),
                    'unsafe': getattr(error, 'unsafe', True),
                }
                trace.lifecycle('OPERATOR_LOGIN_STOP', {**details, 'state': code,
                    'code': code, 'error_class': type(error).__name__})
                print(f'LOGIN_CHECK_FAILED {stage} {code}', flush=True)
                if getattr(browser, 'page', None) is not None and browser.guard is not None:
                    try:
                        async with asyncio.timeout(3):
                            current = await login_page_context(browser)
                        print('LOGIN_PAGE_CONTEXT ' + json.dumps(current, sort_keys=True), flush=True)
                    except Exception as context_error:
                        details = evidence_diagnostic(context_error, 'FINAL_READ_ONLY_OBSERVATION', browser)
                        print('LOGIN_PAGE_CONTEXT ' + details['code'], flush=True)
            await inspect_failed_operator_login()
        return result

    async def login_node(_state):
        trace.agent_stage='login'
        trace.agent_phase='operator_login' if operator_login and not page1 else 'login_doer'
        login_start=len(trace.model_diagnostics)
        async def supervised_review(*values):
            try:return await review_and_ask_operator(*values)
            except Exception as error:
                code=getattr(error,'code',None) or ('CODE_FIX_REQUIRED' if isinstance(error,(TypeError,AttributeError,NameError)) else 'LOGIN_HANDOFF_FAILED')
                context['handoff_failure']={'code':code,'owner':getattr(error,'owner','controller'),'unsafe':getattr(error,'unsafe',False)}
                if manual_requested:
                    failed_checks = getattr(error, 'diagnostic', {}).get('failed_predicates', [])
                    suffix = ' ' + json.dumps(failed_checks) if failed_checks else ''
                    print('LOGIN_CHECK_FAILED ' + code + suffix, flush=True)
                trace.agent_event('supervised_handoff_failed',role='controller',code=code,error_class=type(error).__name__)
                raise
        try:
            if operator_login and not page1:
                result = await run_operator_login(supervised_review)
            else:
                trace.agent_event('login_doer_started',role='doer',credential_submission_limit=1)
                login_model = openai_model('doer', settings, trace, budget, login=True)
                login_task = DEFAULT_TASK
                if page1:
                    login_task += (' Once the single sign-in submission reaches the Netcore application, '
                                   'finish the Login Doer and report independent evidence accurately. '
                                   'The controller will ask the operator to confirm the dashboard tenant.')
                result=await _execute_doer(browser,login_tools,login_task,login_model,budget,False,
                    SkillStore().snapshot(),close_session=False,login_timeout=session_seconds,
                    after_login=supervised_review)
        finally:
            context['login_usage']=request_usage(trace,login_start,role='doer')
        context['login_result']=result
        if context.get('handoff_failure'):
            failure=context['handoff_failure']
            raise V2Stop(failure['code'],owner=failure['owner'],unsafe=failure['unsafe'])
        if browser.login_submissions>1:
            raise V2Stop('LOGIN_SUBMISSION_LIMIT_EXCEEDED',owner='authorization',unsafe=True)
        observation=context.get('observation')
        route=human_assisted_route(observation,
            diagnostic_consent=context['diagnostic_consent'],
            session_safe=context['session_safe'])
        if operator_confirmed_mode and context.get('operator_tab') is not None:
            route='OPERATOR_CONFIRMED'
        if route=='VERIFIED' and not context['doer_requested']:
            route='VERIFIED_ONLY'
        if route == 'DIAGNOSTIC' and manual_requested:
            context['manual_authentication_blocked'] = True
            route = 'BLOCKED'
        elif route=='DIAGNOSTIC' and not context['doer_requested']:
            route='BLOCKED'
        context['route']=route
        trace.agent_event('graph_route_selected',role='controller',route=route,
            authentication_verified=route in {'VERIFIED','VERIFIED_ONLY'},
            diagnostic_consent=context['diagnostic_consent'],
            login_submissions=browser.login_submissions)
        result = {'route': route, 'authentication_verified': route in {'VERIFIED', 'VERIFIED_ONLY'},
                  'diagnostic_consent': context['diagnostic_consent']}
        if route == 'VERIFIED_ONLY' and manual_requested:
            decision = context.get('setup_write_decision')
            if decision == 'timeout':
                result['status'] = 'SETUP_WRITE_APPROVAL_TIMEOUT'
            elif decision == 'no':
                result['status'] = 'SETUP_WRITE_DECLINED'
        return result

    async def run_whatsapp_doer(state,route):
        nonlocal skill, quiet_terminal
        context['failure_stage']='whatsapp_handoff'
        verified=route=='VERIFIED'
        operator_tab = context.get('operator_tab') if route == 'OPERATOR_CONFIRMED' else None
        if route == 'OPERATOR_CONFIRMED' and (not operator_confirmed_mode or operator_tab is None):
            raise V2Stop('OPERATOR_CONFIRMED_TAB_UNAVAILABLE', owner='authorization', unsafe=True)
        if manual_requested and not (verified or operator_tab is not None):
            raise V2Stop('SETUP_LOGIN_PREREQUISITE_REQUIRED', owner='authorization', unsafe=True)
        if state.get('authentication_verified') is not verified:
            raise V2Stop('HUMAN_ASSISTED_ROUTE_AUTHENTICATION_MISMATCH',
                owner='controller',unsafe=True)
        if route=='DIAGNOSTIC' and not state.get('diagnostic_consent'):
            raise V2Stop('HUMAN_DIAGNOSTIC_CONSENT_REQUIRED',owner='authorization',unsafe=True)
        if not context['session_safe']:
            raise V2Stop('OPERATOR_OBSERVED_NAVIGATION_REQUIRED',owner='authorization',unsafe=True)
        if context['source_url'] is None:
            raise V2Stop('OBSERVED_SOURCE_URL_UNAVAILABLE',owner='browser',unsafe=True)
        source_url=context['source_url']
        parsed=urlsplit(source_url)
        if (f'{parsed.scheme}://{parsed.netloc}'!='https://'+config.expected_host
            or parsed.fragment or parsed.username or parsed.password
            or not browser.policy.permits(source_url)):
            raise V2Stop('OBSERVED_SOURCE_DESTINATION_UNSAFE',owner='browser',unsafe=True)
        current_context=await login_page_context(browser)
        current_page=await browser.page.url()
        active=await browser.browser.context.active_page()
        if (not current_context.get('primary_document_coherent')
            or source_identity(current_context)!=context['source_context']
            or current_page!=source_url or active is None
            or active.page_id!=browser.login_page.page_id
            or browser.guard.failed or browser.guard.blocking_hosts):
            raise V2Stop('LOGIN_DOCUMENT_CHANGED_BEFORE_DIAGNOSTIC',owner='browser',unsafe=True)
        remaining=human_assisted_remaining_seconds(session_seconds,context['started'])
        trace.agent_event('whatsapp_handoff_budget',role='controller',
            session_seconds=session_seconds,session_remaining_seconds=round(remaining,3),
            browser_actions_used=trace.actions,browser_actions_limit=browser.config.max_actions)
        context['failure_stage']='setup_navigation'
        if operator_tab is not None:
            await operator_tab.require_current(browser)
        page, binding = await create_protected_tab(browser)
        browser.page = browser.setup_page = page
        browser.setup_binding = binding
        if operator_tab is not None:
            from modules.whatsapp.destination import (classify_setup_page, DestinationRejected,
                landing_route_parts, same_tenant_landing_path)
            prior_navigation = browser.guard.navigation_observer
            prior_route = landing_route_parts("about:blank")
            def watch_setup_navigation(kind, session, url, **kwargs):
                nonlocal prior_route
                if session == binding.session and kind in {'DOCUMENT_REQUEST', 'DOCUMENT_COMMITTED', 'SAME_DOCUMENT_NAVIGATION'}:
                    route_facts = {}
                    predicate = None
                    try:
                        setup_route = classify_setup_page(url, SETUP_URL)
                        expected = setup_route['route_match']
                        route_facts = setup_route['diagnostic']
                    except DestinationRejected as error:
                        expected = False
                        predicate = error.predicate
                    if page1 and context.get('campaign_transition_destination') and not expected:
                        from modules.whatsapp.destination import campaign_step_route
                        expected = any(campaign_step_route(url, step) for step in
                            (context.get('campaign_transition_source'), context['campaign_transition_destination']))
                    current_route = landing_route_parts(url)
                    before_route = prior_route
                    if kind != 'DOCUMENT_REQUEST':
                        prior_route = current_route
                    if before_route != current_route:
                        trace.agent_event('operator_campaign_route_observed', role='controller',
                            navigation_kind=kind, before=before_route, after=current_route, route_match=expected)
                    signin = current_route['hostname'] == urlsplit(config.login_url).hostname
                    pending = context.get('setup_landing_pending_since')
                    settling = (page1 and not context.get('setup_landing_certified')
                        and kind == 'SAME_DOCUMENT_NAVIGATION' and same_tenant_landing_path(url, SETUP_URL)
                        and (pending is not None or before_route in (landing_route_parts(SETUP_URL),
                                                                     landing_route_parts(SETUP_URL + '#setup'))))
                    if signin:
                        context['operator_setup_stop'] = 'OPERATOR_SETUP_REDIRECTED_TO_SIGN_IN'
                    elif not expected and url != 'about:blank':
                        if settling:
                            if pending is None:
                                context['setup_landing_pending_since'] = monotonic()
                        else:
                            context['operator_setup_stop'] = 'OPERATOR_SETUP_ROUTE_MISMATCH'
                    if not expected and url != 'about:blank':
                        failed = [name for name in ('path_match', 'query_match') if route_facts.get(name) is False]
                        if route_facts.get('fragment_class') == 'UNEXPECTED':
                            failed.append('fragment_class')
                        trace.agent_event('operator_campaign_route_rejected', role='controller',
                            navigation_kind=kind, code=context.get('operator_setup_stop'),
                            before=before_route, after=current_route,
                            disposition='WAIT_READ_ONLY' if settling and not signin else 'STOP',
                            failed_predicates=[predicate] if predicate else failed,
                            fragment_class=route_facts.get('fragment_class'))
                if prior_navigation:
                    prior_navigation(kind, session, url, **kwargs)
            browser.guard.navigation_observer = watch_setup_navigation
        await browser.navigate(SETUP_URL)
        authorization=human_assisted_authorization(args, verified=verified)
        if selected_candidate and authorization.manual_form_test:
            skill = selected_candidate
        elif selected_candidate:
            trace.agent_event('candidate_selection_not_loaded', role='controller',
                              reason='VERIFIED_MANUAL_SETUP_REQUIRED', version=candidate_version)
        context['skill']=skill
        tools=V2BrowserTools(browser,payload,setup_ui,authorization,artifact_dir)
        tools.human_assisted_setup = True
        tools.page1_tag_chip_mode = page1
        if operator_tab is not None:
            tools.operator_login_binding = operator_tab
            tools.operator_setup_binding = binding
            tools.operator_setup_stop = lambda: context.get('operator_setup_stop')
        tools.operation_timeout_ms=operation_seconds*1000
        tools.tool_call_limit=tool_limit
        tools.inference_budget=budget
        tools.session_deadline=context['started']+session_seconds
        # Register payload strings with the existing trace redactor; never add
        # credentials or reset any Login trace/phase state.
        trace._agent_private=tuple(sorted(set(getattr(trace,'_agent_private',()))|
            {v for v in payload.model_dump().values() if isinstance(v,str) and len(v)>=4} |
            {v for field in ('include_audience','exclude_audience') for v in payload.audience_values(field)
             if len(v)>=4},key=len,reverse=True))
        context.update(tools=tools,payload=payload,authorization=authorization,task_digest=digest)
        (artifact_dir/'loaded-task-skill.md').write_bytes(skill.content.encode('utf-8'))
        trace.agent_event('loaded_task_skill',role='controller',content_sha256=skill.digest,
            approved=skill.approved,source_version=skill.metadata.get('version') or skill.metadata.get('source_version'))
        print('SETUP_STRATEGY ' + json.dumps({
            'version': skill.metadata.get('version') or skill.metadata.get('source_version'),
            'sha256': skill.digest, 'path': str(artifact_dir / 'loaded-task-skill.md'),
        }), flush=True)
        tools.read_only = manual_requested or not authorization.manual_form_test
        tools.manual_authentication_verified = verified
        context['failure_stage']='setup_pre_observation_protection'
        if not page1:
            await tools.protect()
        if manual_requested:
            context['failure_stage'] = 'setup_preapproval_visibility'
            quiet_terminal = True
            try:
                visibility_window = asyncio.timeout(operation_seconds)
                try:
                    async with visibility_window:
                        while True:
                            pending = context.get('setup_landing_pending_since')
                            if pending is not None and monotonic() - pending >= 4:
                                trace.agent_event('operator_campaign_landing_timeout', role='observer',
                                    route=landing_route_parts(await browser.page.url()), wait_seconds=4)
                                raise V2Stop('OPERATOR_SETUP_LANDING_DID_NOT_RETURN_TO_VERIFIED_SETUP',
                                             owner='browser', unsafe=True)
                            try:
                                observed_document = await tools.protect()
                                form = await tools.observe(strategy='dom' if page1 else None)
                                if not page1:
                                    break
                                if await tools.protect() != observed_document:
                                    raise V2Stop('SETUP_DOCUMENT_CHANGED_DURING_PREFLIGHT',
                                                 owner='browser', unsafe=True)
                                ready = (form.get('stage_visible') is True and
                                         'campaign_name' in form.get('field_evidence', ()) and
                                         (tools._last_protected_route or {}).get('fragment_class') == 'SETUP')
                                pending = context.get('setup_landing_pending_since')
                                if ready and (pending is None or monotonic() - pending < 4):
                                    context['setup_landing_certified'] = True
                                    trace.agent_event('operator_campaign_landing_verified', role='observer',
                                        route=landing_route_parts(await browser.page.url()),
                                        transient_path_observed=pending is not None,
                                        active_setup=True, form_visible=True)
                                    break
                                context['page_observations'] = form
                            except V2Stop as error:
                                pending = context.get('setup_landing_pending_since')
                                if (pending is None or context.get('operator_setup_stop') or error.code not in
                                        {'OPERATOR_SETUP_ROUTE_MISMATCH','PROTECTED_TASK_FRAGMENT_UNSUPPORTED'}):
                                    raise
                            await asyncio.sleep(.25)
                        require_setup_observation(form)
                except TimeoutError:
                    if not visibility_window.expired():
                        raise
                    raise V2Stop('WHATSAPP_SETUP_FORM_VISIBILITY_TIMEOUT', owner='recognition') from None
                context['page_observations'] = form
                context['last_verified_step'] = 'SETUP_FORM_VISIBLE'
                print('SETUP_FORM_READINESS VISIBLE', flush=True)
                context['failure_stage'] = 'setup_write_scope_preflight'
                async with asyncio.timeout(operation_seconds):
                    setup_document = await tools.check_write_scope(require_setup_route=True, preflight=True,
                                                                     expected_document=observed_document)
                context['last_verified_step'] = 'SETUP_WRITE_SCOPE_CERTIFIED'
                context['failure_stage'] = 'setup_write_approval'
                console = OperatorConsole(event_sink=record_operator_decision)
                seconds = min(review_seconds, int(human_assisted_remaining_seconds(session_seconds, context['started'])))
                if page1:
                    approval = context.get('page1_setup_approval')
                    if approval is None or approval[0] is not operator_tab or approval[1] is not payload or approval[2] != digest:
                        raise V2Stop('PAGE1_SETUP_APPROVAL_BINDING_CHANGED', owner='authorization', unsafe=True)
                    decision = 'yes'
                else:
                    decision = await console.confirm_operator_login(seconds) if seconds > 0 else 'timeout'
            finally:
                quiet_terminal = False
            trace.agent_event('setup_write_consent', role='operator', accepted=decision == 'yes',
                              authentication_verified=verified, login_verification=context.get('verification'),
                              status=decision)
            if decision != 'yes':
                context['operator_aborted'] = True
                return {'status': 'SETUP_WRITE_DECLINED' if decision == 'no' else 'SETUP_WRITE_APPROVAL_TIMEOUT',
                        'authentication_verified': verified, 'task_doer_invoked': False}
            if await tools.protect() != setup_document:
                raise V2Stop('OPERATOR_APPROVED_SETUP_DOCUMENT_CHANGED', owner='browser', unsafe=True)
            context['setup_write_approved'] = True
            context['last_verified_step'] = 'SETUP_WRITE_APPROVED'
            if operator_tab is not None:
                tools.operator_approved_setup_document = setup_document
                tools.operator_approved_payload = payload
            authorization = human_assisted_authorization(args, verified=verified, operator_confirmed=operator_tab is not None)
            tools.authorization = context['authorization'] = authorization
            tools.read_only = False
        route_fragment = (tools._last_protected_route or {}).get('fragment_class', 'UNOBSERVED')
        print(f'SETUP_ROUTE {route_fragment} | SETUP_FORM_READINESS PENDING_DOER_OBSERVATION', flush=True)
        context['failure_stage']='setup_observation'
        # Form recognition belongs to the actual Doer's browser tool. Only
        # ownership/security/transport may gate initialization here.
        remaining=human_assisted_remaining_seconds(session_seconds,context['started'])
        trace.agent_event('whatsapp_setup_handoff_budget',role='controller',
            stage_visible=None,scope_basis='DOER_DISCOVERY_PENDING',
            browser_actions_used=trace.actions,browser_actions_limit=browser.config.max_actions,
            session_remaining_seconds=round(remaining,3))
        trace.agent_stage='whatsapp_diagnostic'
        trace.agent_phase='task_doer'
        budget.role_limits['doer']=budget.role_requests['doer']+caps['task']
        context['failure_stage']='doer_session_budget_preflight'
        remaining=human_assisted_remaining_seconds(session_seconds,context['started'])
        trace.agent_event('whatsapp_doer_start_budget',role='controller',
            session_seconds=session_seconds,session_remaining_seconds=round(remaining,3),
            browser_actions_used=trace.actions,browser_actions_limit=browser.config.max_actions)
        if remaining<1:
            raise V2Stop('SESSION_BUDGET_EXHAUSTED_BEFORE_DOER',owner='controller')
        context['failure_stage']='provider_model_configuration'
        model=openai_model('doer',settings,trace,budget,max_output_tokens=task_output)
        context['failure_stage']='doer_construction_and_registration'
        agent=create_v2_doer(model,tools,trace,budget,skill)
        from modules.whatsapp.contract import v2_action_arguments
        trace.agent_event('setup_tool_contract', role='controller', read_only=tools.read_only,
            actions={action: {'required': required, 'allowed': allowed} for action, (required, allowed)
                     in v2_action_arguments(tools.action_schema()).items()})
        trace.agent_event('task_doer_initialized',role='doer',
            authentication_verified=verified,
            execution_context=('HUMAN_SUPERVISED_NONPERSISTENT_SETUP_EDIT'
                if authorization.manual_form_test else
                'VERIFIED_READ_ONLY' if verified else 'HUMAN_SUPERVISED_DIAGNOSTIC'),
            status='CONSTRUCTED_NOT_YET_INVOKED')
        task_start=len(trace.model_diagnostics)
        from langsmith import tracing_context
        task_goal=('Supervised WhatsApp Setup form-entry test using the approved immutable payload. '
            if authorization.manual_form_test else
            'Supervised read-only WhatsApp Setup inspection. ')
        edit_scope=('Fill approved Setup fields using only fixed payload browser actions, then verify every value by readback. '
            if authorization.manual_form_test else
            'Do not click, type, search or select any campaign field. ')
        context['failure_stage']='doer_session_budget_preflight'
        remaining=human_assisted_remaining_seconds(session_seconds,context['started'])
        if remaining<1:
            raise V2Stop('SESSION_BUDGET_EXHAUSTED_BEFORE_DOER',owner='controller')
        context['failure_stage']='task_doer_invocation'
        try:
            async with asyncio.timeout(remaining):
                with tracing_context(enabled=False):
                    def mark_doer_invoked():
                        context['task_doer_invoked'] = True
                        trace.agent_event('task_doer_invoked', role='doer',
                            authentication_verified=verified, manual_form_test=authorization.manual_form_test,
                            persistence_allowed=authorization.persistence)

                    if page1:
                        from langgraph.errors import GraphRecursionError
                        from modules.whatsapp.page1_fields import run_page1_fields
                        from modules.whatsapp.contract import V2Turn
                        trace.agent_event('page1_field_controller_started', role='controller')
                        async def recover_field(field, failure):
                            observation = tools.compact_tool_observation(tools.last_observation or {})
                            references = [item for item in observation.get('controls', ()) if item.get('field') == field]
                            mark_doer_invoked()
                            trace.agent_event('page1_doer_recovery', role='doer', field=field, failure=failure)
                            current = {'field': field, 'last_action_error': failure.get('error'),
                                       'controls': references, 'searched': failure.get('searched', False),
                                       'option_evidence': failure.get('option_evidence', {}),
                                       'verified_fields': [key for key, ok in
                                           tools.last_independent_readback.get('fields', {}).items() if ok is True],
                                       'failed_approaches': sorted(tools.failed_approaches.get(('setup', field), ()))}
                            message = ('Recover only the current field using the approved payload. Use at most one distinct '
                                       'approach. Inspect the live state before another menu-open attempt. A verified type or '
                                       'select includes independent readback. Never retry an uncertain write. Return promptly '
                                       'when the field is verified or the control is ambiguous. Current evidence: '
                                       + json.dumps(current, separators=(',', ':')))
                            try:
                                await agent.ainvoke({'messages': [{'role': 'user', 'content': message}]},
                                    {'configurable': {'thread_id': trace.run_id + '-page1-' + field},
                                     'recursion_limit': 8})
                            except GraphRecursionError:
                                trace.agent_event('page1_doer_recovery_bounded', role='controller', field=field)
                            except RuntimeError as error:
                                if str(error) not in {'MODEL_BUDGET_EXHAUSTED', 'FINAL_TEACHER_TOKEN_RESERVE'}:
                                    raise
                                trace.agent_event('page1_doer_recovery_bounded', role='controller',
                                                  field=field, code=str(error))
                        verdict = await run_page1_fields(tools, recover_field)
                        context['evaluation'] = verdict
                        result = {'structured_response': V2Turn(status='VERIFY' if verdict['status'] == 'COMPLETE'
                                                                 else verdict['status'])}
                    elif authorization.manual_form_test:
                        mark_doer_invoked()
                        verdict=await run_bounded_setup_doer(agent,tools,trace,
                            thread_id=trace.run_id+'-human-assisted-whatsapp',
                            goal=task_goal+edit_scope+(
                                'Use the fresh preapproval control references. Write and verify Campaign Name first. '
                                'Then WABA, Business Number, Tag, and GA Tracking. After WABA, use the fresh Business Number reference. '
                                'If Tag has exhausted both '
                                'binding approaches, continue with independently bound GA Tracking. Stop before Next Step.'
                                if operator_tab is not None else 'Observe first, then use dom if needed. Stop before Next Step.'),
                            max_turns=getattr(args,'max_turns',20))
                        context['evaluation']=verdict
                        from modules.whatsapp.contract import V2Turn
                        result={'structured_response':V2Turn(status='VERIFY' if verdict['status']=='COMPLETE' else verdict['status'])}
                    else:
                        mark_doer_invoked()
                        result=await agent.ainvoke({'messages':[{'role':'user','content':
                            task_goal+'Use current observed references only. '
                            'Observe Setup in the current protected tab. Use only read-only discovery, inspection and readback. '
                            'For inspect supply a reference only. '+edit_scope+
                            'Do not navigate or advance. The operator-approved scope is diagnostic only. '
                            'Report the observed Setup status and stop before Next Step.'}]},
                            {'configurable':{'thread_id':trace.run_id+'-human-assisted-whatsapp'},
                             'recursion_limit':20})
        finally:
            context['task_usage']=request_usage(trace,task_start,role='doer')
            context['page_observations']=tools.last_observation
        context['failure_stage']='task_doer_result'
        report=result.get('structured_response')
        if report is None:
            raise V2Stop('MISSING_WHATSAPP_DIAGNOSTIC_REPORT',owner='model')
        status=getattr(report,'status','UNKNOWN')
        context['task_doer_status']=status
        trace.agent_event('task_doer_completed',role='doer',status=status,
            authentication_verified=verified,manual_form_test=authorization.manual_form_test,
            tool_event_count=len(tools.events))
        outcome=('HUMAN_SUPERVISED_WHATSAPP_MANUAL_FORM_TEST' if not verified else
            'VERIFIED_AUTHENTICATION_MANUAL_FORM_TEST') if authorization.manual_form_test else (
            'HUMAN_SUPERVISED_WHATSAPP_DIAGNOSTIC' if not verified else 'VERIFIED_READ_ONLY_WHATSAPP_DOER')
        evaluated=context.get('evaluation',{})
        if page1 and evaluated.get('status') == 'COMPLETE':
            from modules.whatsapp.page1_transition import advance_setup_to_audience
            from modules.whatsapp.contract import PAGE1_SETUP_FIELDS
            if set(evaluated.get('verified_fields', ())) != set(PAGE1_SETUP_FIELDS):
                raise V2Stop('PAGE1_ALL_FIELDS_READBACK_REQUIRED', owner='recognition')
            context['last_verified_step'] = 'ALL_SETUP_FIELDS_VERIFIED'
            context['failure_stage'] = 'page1_next_step_approval'
            print('PAGE1_SETUP_VERIFIED ' + json.dumps(evaluated['verified_fields']), flush=True)
            seconds = min(review_seconds, int(human_assisted_remaining_seconds(session_seconds, context['started'])))
            console = OperatorConsole(event_sink=record_operator_decision)
            quiet_terminal = True
            try:
                decision = await console.confirm_page1_next_step(seconds) if seconds > 0 else 'timeout'
            finally:
                quiet_terminal = False
            if decision == 'yes':
                context['failure_stage'] = 'page1_next_step'
                def mark_attempt():
                    context['page1_transition_attempted'] = True
                    context['campaign_transition_source'] = 'setup'
                    context['campaign_transition_destination'] = 'audience'
                context['audience_transition'] = await advance_setup_to_audience(tools, mark_attempt)
                if context['audience_transition']['status'] == 'AUDIENCE_VISIBLE':
                    context['last_verified_step'] = 'AUDIENCE_VISIBLE'
                    outcome = 'PAGE1_AUDIENCE_VISIBLE'
                    if through_audience or through_schedule:
                        from modules.whatsapp.page1_fields import run_campaign_fields
                        from modules.whatsapp.page1_transition import advance_campaign_step
                        transitions = [('audience', 'content')]
                        if through_schedule:
                            transitions.append(('content', 'schedule'))
                        for step, destination in transitions:
                            tools.stage = tools.campaign_verified_stage = step
                            context['failure_stage'] = step + '_fields'
                            try:
                                await run_campaign_fields(tools, step)
                            except V2Stop as error:
                                if step == 'content' and tools.last_independent_readback.get('fields', {}).get('template') is True:
                                    context['last_verified_step'] = 'CONTENT_TEMPLATE_VERIFIED'
                                print(f'CAMPAIGN_STEP_BLOCKED {step} {error.code}', flush=True)
                                raise
                            context['last_verified_step'] = step.upper() + '_FIELDS_VERIFIED'
                            context['failure_stage'] = step + '_next_step_approval'
                            seconds = min(review_seconds, int(human_assisted_remaining_seconds(
                                session_seconds, context['started'])))
                            quiet_terminal = True
                            try:
                                decision = await console.confirm_campaign_next_step(step, destination, seconds) if seconds > 0 else 'timeout'
                            finally:
                                quiet_terminal = False
                            if decision != 'yes':
                                context['operator_aborted'] = True
                                outcome = 'CAMPAIGN_NEXT_STEP_DECLINED' if decision == 'no' else 'CAMPAIGN_NEXT_STEP_APPROVAL_TIMEOUT'
                                break
                            context['failure_stage'] = step + '_next_step'
                            def mark_campaign_attempt():
                                context['campaign_transition_source'] = step
                                context['campaign_transition_destination'] = destination
                            transition = await advance_campaign_step(tools, step, destination, mark_campaign_attempt)
                            context[step + '_transition'] = transition
                            if transition['status'] != destination.upper() + '_VISIBLE':
                                raise V2Stop(transition['status'], owner='recognition',
                                             unsafe=bool(transition.get('click_attempted')))
                            tools.stage = tools.campaign_verified_stage = destination
                            context['last_verified_step'] = destination.upper() + '_VISIBLE'
                            outcome = context['last_verified_step']
                        if save_schedule_draft and outcome == 'SCHEDULE_VISIBLE':
                            from modules.whatsapp.schedule_draft import ScheduleDraft
                            context['failure_stage'] = 'schedule_fields'
                            schedule = ScheduleDraft(tools)
                            await schedule.apply()
                            context['last_verified_step'] = 'SCHEDULE_FIELDS_VERIFIED'
                            context['failure_stage'] = 'schedule_draft_approval'
                            target = await schedule.require_save_control()
                            seconds = min(review_seconds, int(human_assisted_remaining_seconds(
                                session_seconds, context['started'])))
                            quiet_terminal = True
                            try:
                                decision = await console.confirm_save_draft_preview(seconds) if seconds > 0 else 'timeout'
                            finally:
                                quiet_terminal = False
                            if decision == 'yes':
                                context['failure_stage'] = 'schedule_save_and_preview'
                                def mark_save_attempt():
                                    context['campaign_transition_source'] = 'schedule'
                                    context['campaign_transition_destination'] = 'preview'
                                context['schedule_draft_preview'] = await schedule.save_and_preview(target, mark_save_attempt)
                                context['last_verified_step'] = outcome = 'DRAFT_PREVIEW_VERIFIED'
                            else:
                                context['operator_aborted'] = True
                                outcome = 'SCHEDULE_DRAFT_SAVE_DECLINED' if decision == 'no' else 'SCHEDULE_DRAFT_APPROVAL_TIMEOUT'
                else:
                    return {'status': 'PAGE1_TRANSITION_UNRESOLVED', 'authentication_verified': False,
                            'failure': {'code': context['audience_transition']['status'], 'owner': 'uncertain_write',
                                        'failing_stage': 'page1_next_step', 'unsafe': True}}
            else:
                context['operator_aborted'] = True
                outcome = 'PAGE1_NEXT_STEP_DECLINED' if decision == 'no' else 'PAGE1_NEXT_STEP_APPROVAL_TIMEOUT'
        return {'status':outcome if evaluated.get('status','COMPLETE')=='COMPLETE' else evaluated['status'],
            'failure':evaluated.get('failure'),
            'route':route,'task_doer_invoked':True,'task_doer_status':status,
            'authentication_verified':verified,
            'manual_form_test':authorization.manual_form_test,
            'persistence_allowed':authorization.persistence}

    async def verified_doer(state):
        return await run_whatsapp_doer(state,state['route'])

    async def diagnostic_doer(state):
        return await run_whatsapp_doer(state,'DIAGNOSTIC')

    async def blocked_node(_state):
        if context.get('operator_stop_status'):
            return {'status': context['operator_stop_status'], 'authentication_verified': False,
                    'task_doer_invoked': False}
        route=context.get('route','BLOCKED')
        if route=='VERIFIED_ONLY':
            return {'status':'VERIFIED_AUTHENTICATION_ONLY',
                'authentication_verified':True,'task_doer_invoked':False}
        if context.get('manual_authentication_blocked'):
            return {'status': 'BLOCKED_AUTHENTICATION_EVIDENCE', 'authentication_verified': False,
                    'task_doer_invoked': False,
                    'failure': {'code': 'INDEPENDENT_AUTHENTICATION_NOT_VERIFIED',
                                'owner': 'authentication', 'failing_stage': 'login_handoff', 'unsafe': True}}
        if context.get('session_safe') is not True:
            status='BLOCKED_SECURITY_OR_SCOPE'
        elif context.get('diagnostic_consent') is not True and not context.get('observation'):
            status='BLOCKED_OPERATOR_REVIEW'
        else:
            status='BLOCKED_OPERATOR_REVIEW'
        return {'status':status,'authentication_verified':False,
            'task_doer_invoked':False}

    async def finish_node(state):
        return state

    graph=build_human_assisted_graph(login_node,verified_doer,diagnostic_doer,
        blocked_node,finish_node,trace=trace)
    final_state={}
    session_remaining=human_assisted_remaining_seconds(session_seconds,context['started'])
    trace.agent_event('human_assisted_session_budget',role='controller',
        session_seconds=session_seconds,session_elapsed_seconds=round(session_seconds-session_remaining,3),
        session_remaining_seconds=round(session_remaining,3),
        browser_actions_used=trace.actions,browser_actions_limit=browser.config.max_actions)
    session_timeout=asyncio.timeout(session_remaining)
    try:
        async with session_timeout:
            final_state=await graph.ainvoke({},{
                'configurable':{'thread_id':trace.run_id+'-human-assisted'},
                'recursion_limit':50})
    except BrowserBudgetExceeded as error:
        failure=diagnostic_failure_metadata(error,context.get('failure_stage','workflow'))
        final_state={'status':'BLOCKED_WHATSAPP_TOOLS',
            'authentication_verified':context.get('route')=='VERIFIED',
            'task_doer_invoked':context['task_doer_invoked'],'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure,unsafe=False)
    except TimeoutError as error:
        session_expired=session_timeout.expired()
        failure=human_assisted_timeout_metadata(error,
            context.get('failure_stage','workflow'),
            session_deadline_expired=session_expired)
        if session_expired:
            status='BLOCKED_OPERATOR_REVIEW'
            authentication_verified=context.get('route')=='VERIFIED'
            task_doer_invoked=context['task_doer_invoked']
        else:
            status='BLOCKED_WHATSAPP_TOOLS'
            authentication_verified=context.get('route')=='VERIFIED'
            task_doer_invoked=context['task_doer_invoked']
        final_state={'status':status,'authentication_verified':authentication_verified,
            'task_doer_invoked':task_doer_invoked,'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure)
    except (KeyboardInterrupt,asyncio.CancelledError) as error:
        failure=diagnostic_failure_metadata(error,context.get('failure_stage','workflow'))
        failure.update(code='OPERATOR_CANCELLED',owner='controller')
        final_state={'status':'BLOCKED_OPERATOR_REVIEW',
            'authentication_verified':context.get('route')=='VERIFIED',
            'task_doer_invoked':context['task_doer_invoked'],
            'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure)
    except V2Stop as error:
        failure=diagnostic_failure_metadata(error,context.get('failure_stage','workflow'))
        failure['unsafe']=error.unsafe
        status = 'BLOCKED_SECURITY_OR_SCOPE' if error.unsafe else 'BLOCKED_WHATSAPP_TOOLS'
        if operator_login and error.code == 'OPERATOR_SIGN_IN_TIMEOUT':
            status = error.code
        final_state={'status':status,
            'authentication_verified':context.get('route')=='VERIFIED',
            'task_doer_invoked':context['task_doer_invoked'],
            'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure)
    except RuntimeError as error:
        failure=diagnostic_failure_metadata(error,context.get('failure_stage','workflow'))
        final_state={'status':'BLOCKED_WHATSAPP_TOOLS','authentication_verified':context.get('route')=='VERIFIED',
            'task_doer_invoked':context['task_doer_invoked'],'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure,unsafe=False)
    except Exception as error:
        failure=diagnostic_failure_metadata(error,context.get('failure_stage','workflow'))
        final_state={'status':'BLOCKED_WHATSAPP_TOOLS',
            'authentication_verified':context.get('route')=='VERIFIED',
            'task_doer_invoked':context['task_doer_invoked'],'failure':failure}
        trace.agent_event('graph_failed',role='controller',**failure)
    finally:
        try:
            if context['task_doer_invoked'] or (context['tools'] is not None and context.get('page_observations')):
                from modules.whatsapp.diagnostic_artifacts import save_setup_evidence
                tools = context['tools']
                failure = final_state.get('failure') or {}
                if (tools.terminal or tools.pending_write) and failure.get('code') != 'OPERATOR_CANCELLED':
                    terminal_owner = 'uncertain_write' if tools.pending_write else getattr(tools, 'terminal_owner', 'browser')
                    failure = {**failure, 'code': tools.terminal or 'WRITE_UNCERTAIN', 'owner': terminal_owner,
                               'unsafe': bool(tools.pending_write or getattr(tools, 'terminal_unsafe', True))}
                    final_state['failure'] = failure
                if failure and context['authorization'].manual_form_test and 'evaluation' not in context:
                    context['evaluation'] = setup_stopped_verdict(tools, failure)
                    trace.agent_event('setup_attempt_verdict', role='observer', **context['evaluation'])
                screenshot = None
                if (getattr(args, 'capture_audience_failure', False) and through_audience
                        and failure and not failure.get('unsafe') and tools.stage == 'audience'):
                    from browser.diagnostics import ScreenshotDiagnostics
                    result_pixels = await ScreenshotDiagnostics(browser).compare({}, {}, enabled=True, campaign_tools=tools)
                    screenshot = {key: result_pixels[key] for key in ('status','screenshot_status',
                        'screenshot_reference','screenshot_sha256') if key in result_pixels}
                    trace.agent_event('campaign_failure_screenshot', role='observer', **screenshot)
                context['pre_cleanup_evidence'] = save_setup_evidence(artifact_dir, trace,
                    tools, context.get('evaluation'), failure, screenshot=screenshot)
        except Exception as error:
            trace.agent_event('setup_evidence_unavailable', role='observer', error=type(error).__name__)
        finally:
            if (operator_confirmed_mode and (context.get('evaluation') or {}).get('written_fields')
                    and not (final_state.get('failure') or {}).get('unsafe') and not browser.close_started):
                from browser.inspection import inspection_hold
                try:
                    last_step = context.get('last_verified_step', '')
                    page_label = 'Preview' if last_step == 'DRAFT_PREVIEW_VERIFIED' else next(
                        (name.title() for name in ('audience', 'content', 'schedule')
                                       if last_step.startswith(name.upper())), 'Setup')
                    if last_step != 'AUDIENCE_VISIBLE' or through_schedule:
                        await context['tools'].protect()
                    trace.agent_event('setup_review_hold', role='operator', status='OPEN', seconds=300,
                                      review_step=page_label.upper())
                    await inspection_hold(300, prompt=f'{page_label} is open for review. '
                                          'Press Enter here when finished, or wait for the five-minute review window.')
                except (TimeoutError, PermissionError, RuntimeError, V2Stop):
                    pass
            if not browser.close_started:
                try:
                    await browser.close()
                except Exception as error:
                    trace.cleanup_result='CLEANUP_UNCONFIRMED'
                    trace.agent_event('browser_cleanup',role='controller',
                        status='CLEANUP_UNCONFIRMED',error=type(error).__name__)
        if (getattr(args,'authorized_learning',False) and
                (context['task_doer_invoked'] or (page1 and context.get('evaluation'))) and not context.get('operator_aborted')):
            evaluation = context.get('evaluation', {})
            failed = final_state.get('failure') or evaluation.get('failure') or {}
            review_kind = human_assisted_review_kind(final_state, evaluation)
            if review_kind and trace.cleanup_result == 'CLOSED_AND_PROFILE_REMOVED':
                from modules.whatsapp.diagnostic_artifacts import show_setup_candidate
                context.update(trace=trace,directory=artifact_dir,budget=budget,
                    findings=[evaluation] if evaluation else [], login_verification=context.get('verification'),
                    human_assisted=True, review_kind=review_kind,
                    authentication_verified=context.get('route')=='VERIFIED',task_skill_read=True)
                review_stage = context['tools'].stage if through_audience or through_schedule else 'setup'
                review_state={'status':final_state.get('status','BLOCKED'),'stage':review_stage,'failure':failed}
                try:
                    trace.agent_stage=review_stage;trace.agent_phase='teacher'
                    trace.agent_event('graph_route',role='controller',source='setup_evaluator',
                        destination='teacher',reason=failed.get('code', review_kind))
                    context['teacher']=openai_model('teacher',settings,trace,budget)
                    async with asyncio.timeout(60):
                        await review_v2_attempt(context, review_state, context['authorization'],
                                                context['payload'], analysis_only=review_kind != 'recoverable_strategy')
                        if review_kind == 'recoverable_strategy':
                            await store_v2_candidate(context, review_state, context['authorization'],
                                context['payload'], context['tools'].ui, context['task_digest'])
                    context['candidate_review'] = show_setup_candidate(artifact_dir, trace, context)
                except Exception as error:
                    if trace.teacher_result != 'ANALYZED':
                        trace.teacher_result='ANALYSIS_UNAVAILABLE'
                    trace.agent_event('teacher_review_failed',role='teacher',
                        failure=diagnostic_failure_metadata(error,'teacher_review'))
            else:
                trace.teacher_result = 'NOT_ELIGIBLE_UNSAFE_OR_NO_SETUP_VERDICT' if not review_kind else 'DEFERRED_CLEANUP_UNCONFIRMED'
        if 'candidate_review' not in context:
            context['candidate_review'] = {'status': 'NO_CANDIDATE', 'reason': trace.teacher_result}
            print('SETUP_CANDIDATE ' + json.dumps(context['candidate_review']), flush=True)
        trace.final_outcome=final_state.get('status','BLOCKED_SECURITY_OR_SCOPE')
        trace.agent_event('workflow_finished',role='controller',
            status=trace.final_outcome,
            authentication_verified=final_state.get('authentication_verified',False),
            diagnostic_consent=context['diagnostic_consent'],
            login_submissions=browser.login_submissions,
            cleanup=trace.cleanup_result)
        trace.write(ROOT/'outputs')

    result={
        'workflow':'human_assisted_login_whatsapp',
        'run_id':trace.run_id,
        'status':final_state.get('status','BLOCKED_SECURITY_OR_SCOPE'),
        'authentication_verified':final_state.get('authentication_verified',False),
        'login_verification':context.get('verification','NOT_OBSERVED'),
        'login_gate_status':context.get('gate_status',{}),
        'evidence_configuration':evidence_status,
        'login_mode':'operator_confirmed' if operator_confirmed_mode else 'operator' if operator_login else 'doer',
        'last_verified_step': (context['last_verified_step'] if context.get('last_verified_step') in
                               {'ALL_SETUP_FIELDS_VERIFIED','AUDIENCE_VISIBLE','AUDIENCE_FIELDS_VERIFIED',
                                'CONTENT_VISIBLE','CONTENT_TEMPLATE_VERIFIED','CONTENT_FIELDS_VERIFIED','SCHEDULE_VISIBLE',
                                'SCHEDULE_FIELDS_VERIFIED','DRAFT_PREVIEW_VERIFIED'} else
                               'SETUP_FIELD_READBACK' if (context.get('evaluation') or {}).get('verified_fields')
                               else context.get('last_verified_step', 'NOT_ESTABLISHED')),
        'login_doer_outcome': ({key: (context.get('login_result') or {}).get(key)
                               for key in ('verification', 'reason_code', 'error')}
                              if page1 else None),
        'audience_transition': context.get('audience_transition', {'click_attempted': bool(context.get('page1_transition_attempted'))})
                               if page1 else None,
        'operator_confirmed': context.get('verification') == 'OPERATOR_CONFIRMED',
        'login_submissions':browser.login_submissions,
        'diagnostic_consent':context['diagnostic_consent'],
        'diagnostic_session_safety':context.get('diagnostic_safety'),
        'candidate_bindings':context['selected_candidates'],
        'task_doer_invoked':context['task_doer_invoked'],
        'task_doer_status':context['task_doer_status'],
        'operator_aborted':context.get('operator_aborted') is True,
        'task_tool_event_count':len(context['tools'].events) if context['tools'] else 0,
        'login_doer_usage':context.get('login_usage'),
        'task_doer_usage':context.get('task_usage'),
        'selected_skill_sha256':skill.digest,
        'selected_skill_approved':skill.approved,
        'selected_skill_status':skill.metadata.get('status'),
        'selected_skill_version':skill.metadata.get('version') or skill.metadata.get('source_version'),
        'teacher_status':trace.teacher_result,
        'teacher_dispatch_ready':sum(row['role'] == 'teacher' for row in trace.boundary_diagnostics),
        'teacher_response_returned':any(row['role'] == 'teacher' and
            (row.get('status') == 'RETURNED' or row.get('usage_recorded') is True)
            for row in trace.model_diagnostics),
        'candidate_review':context.get('candidate_review'),
        'pre_cleanup_evidence':context.get('pre_cleanup_evidence'),
        'token_reservations':{'input_reserved':budget.reserved_input, 'output_reserved':budget.reserved_output,
                              'input_limit':budget.max_input_tokens, 'output_limit':budget.max_output_tokens},
        'teacher_usage':request_usage(trace,role='teacher'),
        'teacher_review':context.get('teacher_review'),
        'candidate':context.get('candidate_result'),
        'evaluation':context.get('evaluation'),
        'written_fields':(context.get('evaluation') or {}).get('written_fields', []),
        'verified_fields':(context.get('evaluation') or {}).get('verified_fields', []),
        'elapsed_seconds':round(monotonic()-context['started'],3),
        'browser_actions_used':trace.actions,
        'provider_requests':None,
        'provider_dispatch_ready_requests':trace.openai_api_calls,
        'token_usage':trace.token_usage if trace.token_usage_available else None,
        'model_diagnostics':trace.model_diagnostics,
        'operator_decisions':context['operator_decisions'],
        'failure':final_state.get('failure'),
        'cleanup':trace.cleanup_result,
    }
    page_observations={
        'status':'CAPTURED' if context.get('page_observations') is not None else 'NOT_CAPTURED',
        'latest_observation':context.get('page_observations'),
        'operator_navigation_candidates':context['candidate_evidence'].get('navigation',[]),
    }
    artifact_dir=write_human_assisted_artifacts(
        artifact_root,trace=trace,result=result,
        operator_decisions=context['operator_decisions'],
        candidate_evidence=context['candidate_evidence'],
        resource_evidence=context['resource_evidence'],
        shadow_scope=context['shadow_scope'],
        page_observations=page_observations,
        terminal_log=terminal_log_path.read_text(encoding='utf-8'),
    )
    from modules.whatsapp.diagnostic_artifacts import classify_human_assisted_outcome
    result['outcome']=classify_human_assisted_outcome(result,
        [event['agent_execution'] for event in trace.events if 'agent_execution' in event])
    result['artifact_directory']=str(artifact_dir)
    return trace.redact_agent(result)
