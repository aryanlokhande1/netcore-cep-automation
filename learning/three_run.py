"""Exactly three controller-owned iterations; Teacher cannot launch any live session."""
import json
from pathlib import Path
from time import monotonic
from uuid import uuid4
from dataclasses import replace
from app.config import ROOT,ConfigurationError
from app.state import Result
from deep_agents.schemas import DEFAULT_TASK,validate_task,TeacherProposal
from learning.policies import Strategy,proposal_rule
from learning.autonomous import EXPECTED,save_attempt
from learning.skill_storage.registry import SkillStore
from learning.skill_storage.procedure import render_procedure
from learning.workflow import EligibilityPolicy

class EvaluationStop(RuntimeError):
    pass

CASES=('valid','changed','invalid','already','expired','challenge','wrong_tenant','unknown','aria_variant')

def attach_events(result):
    if '_events' in result:return result
    return {**result,'_events':json.loads((ROOT/'outputs'/f"{result['run_id']}.json").read_text())}

def safe_history(records):
    history=[]
    for r in records:
        item={k:r.get(k) for k in ('run_id','verification','reason_code','strategy_version','metrics','model_requests','actual_api_requests',
            'token_usage','token_usage_available','role_usage','rediscovery_used','teacher_status')}
        a=r['assessment']
        item['strategy_assessment']={k:a.get(k) for k in ('input_rules','proposed_rule','decision','next_strategy')}
        item['proposal_test']={k:a['test_result'].get(k) for k in ('accepted','correctness_improved','deterministic_cost_improved','regressions','same_task_verified','status')}
        history.append(item)
    return history

def safety_stop(result,live=False):
    from modules.login.policy import login_safety_stop
    return login_safety_stop(result,live)

def quality(rows,baseline):
    by_case={r['scenario']:r for r in baseline}
    regressions=[r['scenario'] for r in rows if by_case[r['scenario']]['correct'] and not r['correct']]
    gain=sum(r['correct'] for r in rows)>sum(r['correct'] for r in baseline)
    comparable=all(r['correct']==by_case[r['scenario']]['correct'] for r in rows)
    no_more=all(r['requests']<=by_case[r['scenario']]['requests'] and r['actions']<=by_case[r['scenario']]['actions'] for r in rows)
    cheaper=any(r['requests']<by_case[r['scenario']]['requests'] or r['actions']<by_case[r['scenario']]['actions'] for r in rows)
    return {'accepted':all(r['correct'] for r in rows) and not regressions and (gain or (comparable and no_more and cheaper)),
        'correctness_improved':gain,'deterministic_cost_improved':comparable and no_more and cheaper,
        'regressions':regressions,'latency_is_not_acceptance_evidence':True}

async def fixture_trial(config,scenario,strategy,store,candidate=None):
    from deep_agents.controller import run_synthetic
    result=await run_synthetic(replace(config,headless=True),scenario=scenario,review=False,strategy=strategy,skill_store=store,
        candidate_version=candidate,_evaluation=True,learning=False)
    return attach_events(result)

async def matrix(config,strategy,store,include_semantic=False):
    print('OFFLINE STRATEGY EVALUATION STARTED: headless loopback fixtures; not live iterations.',flush=True)
    rows=[]
    for scenario in (CASES if include_semantic else CASES[:-1]):
        r=await fixture_trial(config,scenario,strategy,store)
        if r.get('error') or r.get('cleanup_result')!='CLOSED_AND_PROFILE_REMOVED':
            print('OFFLINE STRATEGY EVALUATION COMPLETED: SAFETY_STOP',flush=True)
            raise EvaluationStop('EVALUATION_EXECUTION_OR_CLEANUP_STOP')
        rows.append({'scenario':scenario,'run_id':r['run_id'],'verification':r['verification'],
            'correct':r['verification']==EXPECTED[scenario] and not r.get('error'),
            'requests':r['role_usage']['doer']['requests'],'actions':r['metrics']['browser_actions'],
            'duration_seconds':r['metrics']['doer_duration_seconds']})
    print('OFFLINE STRATEGY EVALUATION COMPLETED',flush=True)
    return rows

def learning_report(cycle):
    runs=cycle['iterations']
    text='# Three-run Doer-Teacher learning report\n\n'
    text+=f"Cycle: {cycle['cycle_id']}; mode: {cycle['mode']}; completed: {len(runs)}/3.\n"
    text+='Offline model-double requests are orchestration measurements, not paid inference. Latency is descriptive.\n\n'
    def values(function):return [str(function(r)) for r in runs]+['NOT RUN']*(3-len(runs))
    rows=[('Verification result',lambda r:r['verification']),('Strategy version',lambda r:r['strategy_version']),
        ('Model requests',lambda r:r['model_requests']),('Actual API requests',lambda r:r['actual_api_requests']),
        ('Input/output tokens',lambda r:f"{r['token_usage']['input_tokens']}/{r['token_usage']['output_tokens']}" if r.get('token_usage_available') else 'unavailable (offline)'),
        ('Browser actions',lambda r:r['metrics']['browser_actions']),('Duration seconds',lambda r:r['metrics']['duration_seconds']),
        ('Teacher finding',lambda r:(r.get('teacher_proposal') or {}).get('finding',r.get('teacher_error','not available'))),
        ('Improvement accepted',lambda r:r['assessment']['decision']),('Recovery used',lambda r:r.get('rediscovery_used')),
        ('Inspections (total)',lambda r:r['metrics']['inspections']),('Redundant pre-navigation inspections',lambda r:r['metrics'].get('redundant_inspections',0)),('Trace',lambda r:r['run_id']+'.json')]
    text+='| Metric | Run 1 | Run 2 | Run 3 |\n|---|---|---|---|\n'
    for label,function in rows:text+='| '+label+' | '+' | '.join(values(function))+' |\n'
    for i,r in enumerate(runs,1):
        a=r['assessment']
        text+=f"\n## Run {i}\n\nInput rules: {a['input_rules']}; proposed rule: {a['proposed_rule']}; next rules: {a['next_strategy']['rules']}.\n"
        test=a['test_result']
        text+=f"Decision: {a['decision']}; Teacher status: {r.get('teacher_status')}.\n"
        text+=f"Correctness improved: {test.get('correctness_improved','not proposed')}; deterministic cost improved: {test.get('deterministic_cost_improved','not proposed')}; regressions: {test.get('regressions',[])}.\n"
        if test.get('candidate'):
            baseline={row['scenario']:row for row in test['baseline']}
            text+='\n| Case | Baseline correct | Candidate correct | Baseline/candidate Doer turns | Baseline/candidate actions |\n|---|---|---|---|---|\n'
            for row in test['candidate']:
                before=baseline[row['scenario']]
                text+=f"| {row['scenario']} | {before['correct']} | {row['correct']} | {before['requests']}/{row['requests']} | {before['actions']}/{row['actions']} |\n"
        text+='\nSuccessful facts: '+(', '.join(dict.fromkeys(f['kind'] for f in r.get('step_facts',[]))) or 'none established')+'.\n'
        if r.get('teacher_diagnostic'):text+='Sanitized Teacher diagnostic: '+json.dumps(r['teacher_diagnostic'])+'\n'
        text+='Evidence: outputs/'+r['run_id']+'.json; iteration record '+a['record_id']+'.json.\n'
    text+='\n## Comparison and graduation\n\n'+cycle['progression']+'\n'
    text+='No optimization is inferred from fewer calls on incomplete tasks or from timing noise. Correctness and safety precede cost.\n'
    text+=f"Skill: {cycle.get('candidate_skill')}; graduation: {cycle['graduation']['state']}; live-certified: {cycle['graduation']['live_certified']}.\n"
    text+=f"Safety stop: {cycle.get('stop_reason') or 'none'}. Active approved skill was not overwritten.\n"
    return text

async def run_three(config,task=DEFAULT_TASK,scenario='valid',mode='controlled',learning_runs=3,
    authorized_live=False,authorized_live_comparison=False,doer_api_budget=6,teacher_api_budget=2,total_api_budget=0,
    extra_origins=(),hold_seconds=0,evidence_review=False,store=None,root=None,
    eligibility_policy:EligibilityPolicy|None=None):
    from deep_agents.controller import run_synthetic,run_live
    validate_task(task)
    eligibility_policy=eligibility_policy or safety_stop
    if learning_runs!=3 or config.learning_runs!=3 or mode not in {'controlled','live'}:
        raise ConfigurationError('Exactly learning_runs=3 and controlled/live mode are required')
    if config.teacher_live_replay_enabled:raise ConfigurationError('Teacher live replay is forbidden')
    per_run=doer_api_budget+teacher_api_budget
    token_pool=None
    if mode=='live':
        if not authorized_live or not authorized_live_comparison or not config.live_comparison_enabled:
            raise ConfigurationError('Live comparison needs separate enablement and explicit authorization for three runs')
        from app.model_settings import ModelSettings
        from deep_agents.permissions import TokenPool
        settings=ModelSettings.load()
        token_pool=TokenPool(settings.cycle_input,settings.cycle_output)
        if total_api_budget>settings.cycle_requests:raise ConfigurationError('Configured cycle request cap exceeded')
        from app.safety import authorize_live
        authorize_live(config,True,per_run,extra_origins)
        if min(doer_api_budget,teacher_api_budget)<1 or not per_run<=8 or not per_run*3<=total_api_budget<=24:
            raise ConfigurationError('Approve explicit Doer/Teacher per-run budgets and total up to 24 requests')
    elif total_api_budget:
        raise ConfigurationError('Controlled mode uses no paid API budget')
    store=store or SkillStore();root=Path(root or ROOT/'learning')
    cycle_id=uuid4().hex;directory=root/'cycles'/cycle_id;directory.mkdir(parents=True,exist_ok=False)
    best=Strategy();records=[];matrix_cache={};stopped=None;actual=0
    started=monotonic()
    semantic_required=scenario=='aria_variant'
    evaluation_safety_reason=None
    async def regression(strategy):
        nonlocal evaluation_safety_reason
        key=(tuple(strategy.rules),semantic_required)
        if key not in matrix_cache:
            try:matrix_cache[key]=await matrix(config,strategy,store,semantic_required)
            except EvaluationStop:
                evaluation_safety_reason='EVALUATION_EXECUTION_OR_CLEANUP_STOP'
                matrix_cache[key]=[]
        return matrix_cache[key]
    for number in range(1,4):
        iteration_started=monotonic()
        print(f'Learning iteration {number}/3: fresh Doer and Teacher; mode={mode}',flush=True)
        if mode=='live':
            if actual+per_run>total_api_budget:stopped='TOTAL_API_BUDGET';break
            result=await run_live(config,task,True,per_run,extra_origins,hold_seconds,False,
                teacher_request_budget=teacher_api_budget,strategy=best,history=safe_history(records),learning=False,evidence_review=evidence_review,token_pool=token_pool)
        else:
            result=await run_synthetic(config,task,scenario,review=True,strategy=best,skill_store=store,
                teacher_request_budget=4,history=safe_history(records))
        r=attach_events(result);actual+=r['actual_api_requests']
        save_attempt(r,r['_events'],root)
        stop=eligibility_policy(r,mode=='live')
        proposal=r.get('teacher_proposal')
        rule=proposal_rule(TeacherProposal.model_validate(proposal)) if proposal and proposal['change_required'] else None
        decision='NO_CHANGE' if proposal else 'ANALYSIS_PENDING'
        test={'same_task_verified':r['verification']==Result.AUTHENTICATED_AND_VERIFIED,'status':'NO_CHANGE_RETAINED' if proposal else 'NO_PROPOSAL'}
        next_rules=best.rules
        if rule and not stop:
            if rule=='prefer_semantic_roles':semantic_required=True
            candidate=Strategy(version=uuid4().hex,rules=tuple(dict.fromkeys((*best.rules,rule))),source_run=r['run_id'])
            baseline=await regression(best)
            evaluated=await regression(candidate) if not evaluation_safety_reason else []
            test={**(quality(evaluated,baseline) if not evaluation_safety_reason else {'accepted':False,'stop_reason':evaluation_safety_reason}),'baseline':baseline,'candidate':evaluated}
            stop=stop or evaluation_safety_reason
            decision='ACCEPTED' if test['accepted'] else 'REJECTED'
            if test['accepted']:next_rules=candidate.rules
        next_strategy=Strategy(version=uuid4().hex,rules=next_rules,source_run=r['run_id'])
        record_id=uuid4().hex
        assessment={'iteration_duration_seconds':round(monotonic()-iteration_started,3),'record_id':record_id,'input_rules':list(best.rules),'proposed_rule':rule,
            'decision':decision,'test_result':test,'next_strategy':next_strategy.model_dump()}
        r['assessment']=assessment;records.append(r)
        (directory/(record_id+'.json')).write_text(json.dumps({k:v for k,v in r.items() if k!='_events'},indent=2),encoding='utf-8')
        best=next_strategy
        if stop:
            if mode=='live':print('LIVE VERIFICATION INCOMPLETE — NO FURTHER LIVE ATTEMPT: '+stop,flush=True)
            stopped=stop;break
    comparisons=await regression(best) if records and not stopped else []
    stopped=stopped or evaluation_safety_reason
    text,history=render_procedure(records,best)
    verified=[r for r in records if r['verification']==Result.AUTHENTICATED_AND_VERIFIED]
    scope='live_verified' if mode=='live' and len(verified)==3 else ('live_partial' if mode=='live' else 'synthetic_only')
    provenance={'cycle_id':cycle_id,'source_run':records[0]['run_id'] if records else None,
        'source_traces':[r['run_id']+'.json' for r in records],'verified_run_ids':[r['run_id'] for r in verified],
        'unverified_context_runs':[r['run_id'] for r in records if r not in verified],
        'evidence_scope':scope,'live_verified':scope=='live_verified','strategy':best.model_dump(),
        'strategy_versions':[r['strategy_version'] for r in records],
        'step_evidence':{r['run_id']:r['step_facts'] for r in records}}
    evaluation={'state':'DRAFT','passed':False,'scope':scope,'comparisons':comparisons,'skill_loading_verified':False}
    artifact=store.draft(uuid4().hex,text,evaluation,provenance,history)
    print('CANDIDATE SKILL GENERATED: DRAFT; evidence scope='+scope,flush=True)
    checks=[]
    load_cases=tuple(dict.fromkeys(('valid','changed',scenario if mode=='controlled' else 'valid')))
    if len(records)==3 and comparisons and all(r['correct'] for r in comparisons):
        print('OFFLINE SKILL LOADING EVALUATION STARTED: headless loopback fixtures; not live iterations.',flush=True)
        for case in load_cases:
            loaded=await fixture_trial(config,case,best,store,artifact['version'])
            if loaded.get('error') or loaded.get('cleanup_result')!='CLOSED_AND_PROFILE_REMOVED':
                stopped='SKILL_VALIDATION_EXECUTION_OR_CLEANUP_STOP'
                break
            baseline=next(r for r in comparisons if r['scenario']==case)
            checks.append({'scenario':case,'run_id':loaded['run_id'],'verification':loaded['verification'],
                'skill_loaded':loaded.get('skill_loaded',False),'native_metadata_loaded':loaded.get('native_skill_metadata_loaded',False),
                'baseline_requests':baseline['requests'],'loaded_requests':loaded['role_usage']['doer']['requests'],
                'baseline_actions':baseline['actions'],'loaded_actions':loaded['metrics']['browser_actions']})
        print('OFFLINE SKILL LOADING EVALUATION COMPLETED',flush=True)
    evaluation['skill_loading_checks']=checks
    evaluation['skill_loading_verified']=len(checks)==len(load_cases) and all(c['verification']==Result.AUTHENTICATED_AND_VERIFIED and c['skill_loaded'] and c['native_metadata_loaded'] for c in checks)
    repeat=len(records)==3 and all(r['verification']==Result.AUTHENTICATED_AND_VERIFIED and tuple(r['assessment']['input_rules'])==best.rules for r in records[-2:])
    gates={'three_iterations':len(records)==3,'all_teacher_reviews':len(records)==3 and all(r.get('teacher_status')=='ANALYZED' for r in records),
        'regressions_passed':bool(comparisons) and all(r['correct'] for r in comparisons),
        'repeatability':repeat,'native_skill_loading':evaluation['skill_loading_verified'],'no_safety_stop':not stopped}
    ready=all(gates.values())
    state='GRADUATION_READY' if ready else ('EVALUATED' if checks else 'DRAFT')
    graduation={'state':state,'transitions':['DRAFT']+(['EVALUATED'] if checks else [])+(['GRADUATION_READY'] if ready else []),
        'gates':gates,'certification_scope':scope,'live_certified':ready and scope=='live_verified',
        'requires_explicit_promotion':True,'immutable':True,'baseline_performance_comparison':checks}
    evaluation.update(state=state,passed=ready)
    bundle=store.version_path('candidates',artifact['version'])
    (bundle/'evaluation.json').write_text(json.dumps(evaluation,indent=2),encoding='utf-8')
    with (bundle/'graduation.json').open('x',encoding='utf-8') as handle:json.dump(graduation,handle,indent=2)
    artifact.update(digest=store.digest(bundle),state=state,live_certified=graduation['live_certified'])
    print(('LIVE' if mode=='live' else 'CONTROLLED')+' GRADUATION REVIEW COMPLETED: '+state+'; promotion requires explicit approval.',flush=True)
    if len(records)==3:
        progression=('Correctness improved from the initial incomplete result to repeatable verified fixture execution.' if records[0]['verification']!=Result.AUTHENTICATED_AND_VERIFIED and records[-1]['verification']==Result.AUTHENTICATED_AND_VERIFIED else 'No demonstrated optimization; the current strategy was retained and repeatability was measured.')
    else:progression='The cycle stopped at a safety boundary; three-run graduation was withheld.'
    cycle={'cycle_id':cycle_id,'mode':mode,'iterations':[{k:v for k,v in r.items() if k!='_events'} for r in records],
        'learning_runs':3,'stop_reason':stopped,'actual_api_requests':actual,'duration_seconds':round(monotonic()-started,3),
        'runtime_budgets':{'doer_per_run':doer_api_budget,'teacher_per_run':teacher_api_budget,'total':total_api_budget,'max_output_tokens_per_request':{'doer':settings.doer_output,'teacher':settings.teacher_output} if mode=='live' else 0,
            'cycle_token_reservations':{'input':token_pool.input_reserved,'output':token_pool.output_reserved,'input_cap':token_pool.input_limit,'output_cap':token_pool.output_limit} if token_pool else None,
            'controlled_model_double_limits':{'doer':28,'teacher':4,'per_run_total':32}},
        'candidate_skill':artifact,'graduation':graduation,'progression':progression}
    with (directory/'cycle.json').open('x',encoding='utf-8') as handle:json.dump(cycle,handle,indent=2)
    report=learning_report(cycle)
    (directory/'THREE_RUN_LEARNING_REPORT.md').write_text(report,encoding='utf-8')
    (root/'THREE_RUN_LEARNING_REPORT.md').write_text(report,encoding='utf-8')
    return cycle
