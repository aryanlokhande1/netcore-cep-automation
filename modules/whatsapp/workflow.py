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


def teacher_factual_contradiction(prose,actual,authentication_verified):
    """Conservative prose guard; deterministic facts remain authoritative."""
    if not authentication_verified and re.search(
        r'\bauthentication (?:occurred|succeeded|passed)|\b(?:login|session|tenant|account) (?:was|is|has been) (?:independently )?authenticated\b|\bauthenticated (?:successfully|and verified)\b',prose,re.I):
        return 'TEACHER_ASSERTED_UNVERIFIED_AUTHENTICATION'
    if not actual['task_tool_calls'] and re.search(
        r'\bwhatsapp doer (?:executed|ran|started|was invoked)|\btask doer (?:executed|ran|was invoked)\b',prose,re.I):
        return 'TEACHER_ASSERTED_UNOBSERVED_TASK_EXECUTION'
    return None


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
            trace.agent_event('skill_selected',version=state.get('candidate'),content_sha256=ctx['skill'].digest,
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
            current=await tools.observe()
            agent=create_v2_doer(ctx['doer'],tools,ctx['trace'],ctx['budget'],ctx['skill'])
            ctx['trace'].agent_event('task_doer_initialized',role='doer',
                authentication_verified=ctx['authentication_verified'],status='CONSTRUCTED_NOT_YET_INVOKED')
            ctx['task_skill_read']=True
            ctx['trace'].agent_event('skill_loaded',role='doer',version=state.get('candidate'),
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
        tools=ctx.get('tools');trace=ctx['trace']
        if state.get('failure',{}).get('code')=='OPERATOR_CANCELLED':
            trace.teacher_result='DEFERRED_OPERATOR_CANCELLED';return {'route':'candidate'}
        try:
            if ctx.get('teacher') is None:raise V2Stop('TEACHER_CONFIGURATION_UNAVAILABLE')
            from deep_agents.evidence import compile_evidence,execution_facts
            from modules.login.review import LoginReviewAdapter
            actual=execution_facts(trace.events,trace.step_facts)
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
            package={'task_scope':'APPROVED_SAVED_DRAFT_ONLY' if authorization.edits and authorization.persistence else 'AUTHENTICATED_READ_ONLY_DISCOVERY',
                'goal':state.get('stage','login'),'execution_facts':actual,'login_execution_evidence':compiled,
                'login_verification':ctx.get('login_verification'),
                'login_execution_failure':retained.get('execution_failure'),
                'authentication_verified':ctx['authentication_verified'],
                'controller_state':{'route_source':ctx.get('route_source'),'route':state.get('route'),
                    'failure':state.get('failure')},
                'status':state['status'],'events':selected_events,'observer':ctx['findings'][-6:],
                'omitted_older_tool_events':max(0,len(tools.events)-12) if tools else 0,
                'all_execution_evidence_preserved_in_run_trace':True,
                'failure':state.get('failure'),'evidence_ids':ids,'skill_sha256':ctx.get('skill').digest if ctx.get('skill') else None,
                'candidate_version':state.get('candidate'),'skill':ctx['skill'].content if ctx.get('skill') and ctx['task_skill_read'] else None,
                'limitations':['No publication, activation, test-send or actual scheduling','No Python edits','Uncertain writes cannot be retried']}
            package=trace.redact_agent(package)
            evidence_bytes=json.dumps(package,sort_keys=True).encode('utf-8')
            (ctx['directory']/'teacher-input-evidence.json').write_bytes(evidence_bytes)
            trace.agent_event('teacher_evidence_package',role='teacher',execution_facts=actual,
                evidence_sha256=hashlib.sha256(evidence_bytes).hexdigest(),
                source_event_count=compiled['source_event_count'],evidence_refs=ids)
            agent,_=create_teacher(ctx['teacher'],trace,ctx['budget'],schema=V2Lesson,
                prompt_override='Review real execution evidence only. Echo the supplied deterministic execution_facts exactly. Empty WhatsApp events do not mean Login/browser actions did not occur: inspect login_execution_evidence and login_verification. Cite evidence_ids, including at least one actual Login action when present; trace:N refers to original event index N. Never deny recorded browser activity. Classify defects honestly; tool/controller defects require code_fix_required. Propose concise procedural candidate instructions only when justified. Recommend code repairs as bounded file/function descriptions in recommended_code_repairs; never apply them. No selectors, URLs, private values, source edits, task changes or permission relaxation. No executable tools. Never claim an unconsumed candidate improved execution.')
            from langsmith import tracing_context
            with tracing_context(enabled=False):
                reviewed=await agent.ainvoke({'messages':[{'role':'user','content':json.dumps(package)}]},{'recursion_limit':6})
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
            rejection=teacher_factual_contradiction(prose,actual,ctx['authentication_verified']) or rejection
            if rejection:
                ctx['teacher_rejection']=trace.redact_agent({'code':rejection,'response':lesson.model_dump(),
                    'actual_execution_facts':actual,'source_response_sha256':hashlib.sha256(lesson.model_dump_json().encode()).hexdigest()})
                trace.agent_event('teacher_rejected',role='teacher',rejection=ctx['teacher_rejection'])
                raise V2Stop(rejection,owner='controller')
            if state.get('failure',{}).get('code')=='CODE_FIX_REQUIRED':
                lesson=lesson.model_copy(update={'code_fix_required':True,'safe_next_iteration':False})
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

    async def candidate(state):
        tools=ctx.get('tools');lesson=ctx.get('lesson')
        if authorization.learning and lesson and not (tools and (tools.terminal or tools.pending_write)) and not state.get('failure',{}).get('unsafe'):
            try:
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
