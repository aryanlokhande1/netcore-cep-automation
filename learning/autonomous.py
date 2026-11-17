"""Versioned Teacher records, bounded policy evaluation and candidate generation.

This module never accepts a live browser, credential object or replay URL.
"""
import json
import re
from dataclasses import replace
from pathlib import Path
from uuid import uuid4
from app.config import ROOT
from app.state import Observation,Result
from deep_agents.schemas import TeacherProposal
from learning.policies import Strategy,proposal_rule
from learning.skill_storage.registry import SkillStore
from learning.skill_storage.generation import render_skill,REQUIRED_FACTS

EXPECTED={name:Result.AUTHENTICATED_AND_VERIFIED for name in ('valid','already','changed','challenge','aria_variant')}
EXPECTED.update(invalid=Result.AUTH_FAILED,expired=Result.SESSION_EXPIRED,wrong_tenant=Result.UNEXPECTED_PAGE,unknown=Result.UNEXPECTED_PAGE)
MATRIX=('valid','changed','changed','invalid','already','expired','challenge','wrong_tenant','unknown')


def save_attempt(attempt,events,root=None):
    root=Path(root or ROOT/'learning')
    run_id=attempt['run_id']
    if not re.fullmatch('[a-f0-9]{32}',run_id):
        raise ValueError('Invalid run identifier')
    for event in events:
        if event.get('observation') is not None:
            Observation.model_validate(event['observation'])
    # Only canonical typed application output is persisted, never raw model transcripts.
    record={key:attempt.get(key) for key in ('run_id','source','scenario','verification','reason_code','verification_snapshot',
        'step_facts','strategy_version','cleanup_result','teacher_status','teacher_error','teacher_proposal',
        'actual_api_requests','model_requests','token_usage','mocked','mode','error','teacher_diagnostic',
        'teacher_validation_summary','teacher_rejected_proposal','role_usage','metrics','role_evidence',
        'submission_state','missing_evidence','agent_report','cleanup_diagnostic','verification_diagnostic',
        'inspection_diagnostic','navigation_guard_status','blocked_documents','model_diagnostics',
        'teacher_decision','teacher_evidence','teacher_boundary','boundary_diagnostics','teacher_rejected_decision',
        'teacher_review_context','review_previous_strategy')}
    if record['teacher_proposal'] is not None:
        record['teacher_proposal']=TeacherProposal.model_validate(record['teacher_proposal']).model_dump()
    directory=root/'attempts';directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{run_id}.json'
    path.write_text(json.dumps({'attempt':record,'events':events},indent=2),encoding='utf-8')
    return path


def strategy_record(root,version,data):
    directory=root/'strategies'/'versions';directory.mkdir(parents=True,exist_ok=True)
    path=directory/f'{version}.json'
    if path.exists():
        raise ValueError('Strategy version is immutable')
    path.write_text(json.dumps(data,indent=2),encoding='utf-8')
    status=data['decision']
    text=f'# Candidate strategy\n\nVersion: {version}\nDecision: {status}\nEvaluation scope: controlled fixtures only.\n'
    text+=f"Source run: {data['source_run']}\n\n"+data['strategy']['instructions']+'\n'
    text+='\nHypotheses are not approved instructions. Active skills require reviewed promotion.\n'
    (root/'strategies'/'strategy.md').write_text(text,encoding='utf-8')


async def _trial(config,scenario,strategy,store,candidate=None):
    from deep_agents.controller import run_synthetic
    # URL cannot be supplied. run_synthetic constructs a new exact loopback fixture.
    result=await run_synthetic(replace(config,headless=True),scenario=scenario,mode='offline',review=True,
        strategy=strategy,skill_store=store,candidate_version=candidate,_evaluation=True,learning=False)
    return result


async def process_learning(config,attempt,events,store=None,strategy=None,root=None,max_iterations=2):
    if config.teacher_live_replay_enabled or not 1<=max_iterations<=2:
        raise ValueError('Evaluation is fixture-only and bounded to two proposals')
    root=Path(root or ROOT/'learning')
    store=store or SkillStore()
    strategy=strategy or Strategy()
    save_attempt(attempt,events,root)
    if attempt.get('teacher_proposal') is None:
        from learning.skill_storage.procedure import partial_candidate
        return {'decision':'ANALYSIS_PENDING','candidate_skill':partial_candidate(attempt,events,store)}
    if (attempt.get('teacher_decision') or {}).get('decision')=='repair':
        from learning.skill_storage.procedure import partial_candidate
        return {'decision':'CODE_REPAIR_REQUIRES_REVIEW','candidate_skill':partial_candidate(attempt,events,store),
            'evaluation_passed':False,'promotion':'REQUIRES_EXACT_REVIEWED_APPROVAL'}
    proposal=TeacherProposal.model_validate(attempt['teacher_proposal'])
    if attempt.get('source')=='live':
        from modules.login.policy import login_safety_stop
        stop=login_safety_stop(attempt,True)
        if stop or not proposal.change_required:
            # A diagnostic live record is not an invitation to replay fixtures.
            from learning.skill_storage.procedure import partial_candidate
            artifact=partial_candidate(attempt,events,store)
            if artifact:print('CANDIDATE SKILL GENERATED: DRAFT from recorded live steps; three-run graduation remains pending.',flush=True)
            print('OFFLINE STRATEGY EVALUATION SKIPPED: '+('LIVE_SAFETY_STOP' if stop else 'NO_CHANGE')+'; graduation gates remain pending.',flush=True)
            return {'decision':'NO_CHANGE' if not proposal.change_required else 'DEFERRED_LIVE_SAFETY_STOP',
                'strategies':[],'candidate_skill':artifact,'evaluation_passed':False,
                'evaluation_status':'NOT_RUN','reason':stop or 'NO_CHANGE',
                'promotion':'REQUIRES_EXACT_REVIEWED_APPROVAL'}
    print('OFFLINE STRATEGY EVALUATION STARTED: headless loopback fixtures; not live iterations.',flush=True)
    decisions=[]
    verified_records=[]
    chosen=strategy
    rows=[]
    for iteration in range(max_iterations):
        version=uuid4().hex
        rule=proposal_rule(proposal) if proposal.change_required else None
        rules=tuple(dict.fromkeys((*strategy.rules,*([rule] if rule else []))))
        candidate=Strategy(version=version,rules=rules,source_run=attempt['run_id'])
        rows=[]
        cases=list(MATRIX)
        target=attempt.get('scenario') if attempt.get('source')=='synthetic' else None
        if rule=='prefer_semantic_roles':
            target='aria_variant'
        if target=='aria_variant':
            cases.append(target)
        for scenario in cases:
            trial=await _trial(config,scenario,candidate,store)
            trial_events=json.loads((ROOT/'outputs'/f"{trial['run_id']}.json").read_text())
            save_attempt(trial,trial_events,root)
            correct=trial['verification']==EXPECTED[scenario] and trial.get('error') is None
            rows.append({'scenario':scenario,'run_id':trial['run_id'],'verification':trial['verification'],
                'expected':str(EXPECTED[scenario]),'passed':correct,'step_facts':trial['step_facts'],
                'model_mode':'offline_test_double','skill_loaded':trial.get('skill_loaded',False)})
        passed=all(row['passed'] for row in rows)
        decision='NO_CHANGE' if not proposal.change_required else ('ACCEPTED_FOR_FIXTURES' if passed else 'REJECTED')
        baseline_target=None
        if proposal.change_required and target=='aria_variant':
            baseline_target=await _trial(config,target,strategy,store)
            if baseline_target['verification']==Result.AUTHENTICATED_AND_VERIFIED and not passed:
                decision='REJECTED'
        record={'source_run':attempt['run_id'],'iteration':iteration+1,'proposal':proposal.model_dump(),
            'strategy':{'configuration':candidate.model_dump(),'instructions':candidate.instructions()},
            'decision':decision,'evaluation':rows,'baseline_target':
                {'run_id':baseline_target['run_id'],'verification':baseline_target['verification']} if baseline_target else None,
            'scope':'synthetic_only','approved_for_live':False}
        strategy_record(root,version,record)
        decisions.append({'version':version,'decision':decision})
        if passed:
            chosen=candidate
            verified_records=[{'run_id':r['run_id'],'step_facts':r['step_facts']} for r in rows if r['verification']==Result.AUTHENTICATED_AND_VERIFIED]
            break
        # One bounded offline Teacher revision based on the failed controlled replay.
        if iteration+1<max_iterations and target=='aria_variant' and rule!='prefer_semantic_roles':
            from deep_agents.controller import review_completed
            from deep_agents.offline import SyntheticChatModel
            from deep_agents.permissions import InferenceBudget
            from app.trace import Trace
            failed=next((row for row in rows if row['scenario']=='aria_variant'),None)
            if not failed:
                break
            old_events=json.loads((ROOT/'outputs'/f"{failed['run_id']}.json").read_text())
            review_trace=Trace();review_trace.events=old_events
            review_result={'verification':failed['verification'],'teacher_proposal':None}
            await review_completed(review_result,review_trace,SyntheticChatModel(role='teacher'),InferenceBudget(4),True)
            if review_result.get('teacher_proposal'):
                proposal=TeacherProposal.model_validate(review_result['teacher_proposal'])
                continue
        break
    print('OFFLINE STRATEGY EVALUATION COMPLETED: '+('PASS' if verified_records else 'FAIL'),flush=True)
    if not verified_records:
        return {'decision':decisions[-1]['decision'],'strategies':decisions,'candidate_skill':None}
    # Keep synthetic-only evidence separate from any unverified live context.
    live_verified=attempt.get('source')=='live' and attempt['verification']==Result.AUTHENTICATED_AND_VERIFIED
    if live_verified:
        verified_records.append({'run_id':attempt['run_id'],'step_facts':attempt['step_facts']})
    text=render_skill(chosen,verified_records,live_verified)
    skill_version=uuid4().hex
    evaluation={'passed':True,'scope':'synthetic_only','comparisons':rows,'skill_loading_verified':False}
    provenance={'source_run':attempt['run_id'],'verified_run_ids':[r['run_id'] for r in verified_records],
        'source_traces':[r['run_id']+'.json' for r in verified_records],
        'strategy_version':chosen.version,'strategy_rules':list(chosen.rules),
        'live_verified':live_verified,'unverified_context_runs':[] if attempt['verification']==Result.AUTHENTICATED_AND_VERIFIED else [attempt['run_id']]}
    artifact=store.candidate(skill_version,text,evaluation,provenance)
    checks=[]
    print('OFFLINE SKILL LOADING EVALUATION STARTED: headless loopback fixtures.',flush=True)
    for scenario in ('valid','changed'):
        fresh=await _trial(config,scenario,chosen,store,skill_version)
        checks.append({'run_id':fresh['run_id'],'scenario':scenario,'verification':fresh['verification'],
            'skill_loaded':fresh.get('skill_loaded',False),'native_metadata_loaded':fresh.get('native_skill_metadata_loaded',False)})
    evaluation['skill_loading_checks']=checks
    evaluation['skill_loading_verified']=all(c['verification']==Result.AUTHENTICATED_AND_VERIFIED and c['skill_loaded'] and c['native_metadata_loaded'] for c in checks)
    evaluation['passed'] &= evaluation['skill_loading_verified']
    directory=store.version_path('candidates',skill_version)
    (directory/'evaluation.json').write_text(json.dumps(evaluation,indent=2),encoding='utf-8')
    artifact['digest']=store.digest(directory)
    print('OFFLINE SKILL LOADING EVALUATION COMPLETED',flush=True)
    return {'decision':decisions[-1]['decision'],'strategies':decisions,'candidate_skill':artifact,
        'evaluation_passed':evaluation['passed'],'promotion':'REQUIRES_EXACT_REVIEWED_APPROVAL'}
