"""Ordered, fact-grounded procedures; no raw model prose, selectors or private values."""
import json
from uuid import uuid4
from app.config import ROOT
from app.state import Result
from learning.policies import Strategy
from learning.skill_storage.registry import SkillStore

def render_procedure(records,strategy):
    def refs(kind):
        return '; '.join(f"{r['run_id']} event {f['event_index']}" for r in records for f in r.get('step_facts',[]) if f['kind']==kind) or 'Unresolved: no retained supporting fact.'
    def observed(kind,body):
        return body+' Evidence: '+refs(kind)
    facts={f['kind'] for r in records for f in r.get('step_facts',[])}
    partial='DESTINATION_VERIFIED' not in facts
    scope='Live authentication remains unverified.' if any(r.get('source')=='live' for r in records) and partial else 'Scope and certification are recorded in provenance.json and graduation.json.'
    role_sources='; '.join(r['run_id']+': '+json.dumps(r.get('role_evidence',{}),sort_keys=True) for r in records if r.get('role_evidence'))
    attribution=('Observed canonical role attribution: '+role_sources+'. Evidence: '+refs('LOGIN_RECOGNIZED')) if role_sources else 'Unresolved: attribute-level attribution was not retained; do not guess which private attribute supplied a match.'
    steps=[
        '1. Application policy: use navigate_approved with the controller-provided entry. The reviewed real entry is https://login.netcoresmartech.com/. Fixture evaluations use an exact loopback entry; they do not certify this real site.',
        observed('LOGIN_RECOGNIZED','2. Inspect the visible login page and identify account, username, password and submit roles using current opaque references.') if 'LOGIN_RECOGNIZED' in facts else '2. Unresolved: login-page role recognition is not supported by retained facts.',
        '3. '+attribution+' Application policy: identify account/user roles from associated labels, ARIA labels, placeholder/name/id hints; identify password by type and submit by visible login text. Attribute-level attribution is listed per run in execution-history.md when retained; missing attribution must not be guessed.',
        observed('FIELD_ROLES_VALIDATED','4. Pass current role references to the trusted submission boundary. Validated roles were recorded.') if 'FIELD_ROLES_VALIDATED' in facts else '4. Unresolved: no recorded validated field mapping.',
        '5. Application policy: the live broker checks the approved origin, unique visible/enabled controls, one form, semantic roles, password type and same-origin form action; it claims the one-submission latch before filling privately. The model never handles values.',
        observed('SUBMITTED_ONCE','6. Submit once through the trusted boundary; inspect the destination using read-only tools afterward.') if 'SUBMITTED_ONCE' in facts else '6. Unresolved: no completed submission fact; never infer a click from a URL.',
        '7. Observed outcomes (completed submissions are separately evidenced): '+('; '.join(f"{r['run_id']} -> {r['verification']} ({r.get('reason_code','NOT_RETAINED')})" for r in records))+'. See each event timeline in execution-history.md.',
        observed('DESTINATION_VERIFIED','8. Call verify_dashboard. Independently verified destination evidence exists for the cited runs; certification scope remains limited to those runs.') if not partial else '8. Unresolved: authenticated destination verification has not passed. Expected hostname or missing login fields cannot establish authentication.',
        '9. Application policy: require expected hostname, reviewed dashboard context, private tenant match and authenticated read-only access. Review actual evidence through NETCORE_EVIDENCE_FILE; invent no selector or endpoint.',
        '10. Evaluated strategy: '+(strategy.instructions() or 'no_change; retain the current procedure and evaluate repeatability.')+' Supporting strategy/evaluation references are in provenance.json and evaluation.json.',
        '11. Recovery evidence: '+('at least one cited run used semantic rediscovery; see its recover event.' if any(r.get('rediscovery_used') for r in records) else 'no semantic rediscovery was recorded in these source runs. The one-rediscovery maximum is application policy, not a newly observed recovery success.'),
        '12. Application policy: stop for rejection, CAPTCHA/MFA, account restrictions, uncertain submission, unknown roles, unapproved navigation or exhausted budgets. Never resubmit, create campaigns, change settings, read cookies or capture private screenshots.'
    ]
    text='---\nname: netcore-login\ndescription: Execute the evidenced login procedure with trusted credentials and independent destination verification.\n---\n\n'
    text+='## Purpose and scope\n'+scope+' This is an unapproved candidate until explicit digest promotion.\n\n'
    text+='## Login recognition\n'+ '\n\n'.join(steps[:3])+'\n\n## Authentication boundary\n'+'\n\n'.join(steps[3:7])
    text+='\n\n## Destination verification\n'+'\n\n'.join(steps[7:9])+'\n\n## Recovery\n'+'\n\n'.join(steps[9:11])+'\n\n## Stop conditions\n'+steps[11]+'\n'
    history='# Sanitized execution history\n\nRaw values, private labels, selectors and sessions are excluded.\n'
    for r in records:
        history+=f"\n## Run {r['run_id']}\n\nSource: {r.get('source')}; strategy: {r.get('strategy_version')}; outcome: {r['verification']}.\n"
        history+='Role evidence (canonical attribution only): '+json.dumps(r.get('role_evidence',{}))+'\n\n'
        events=r.get('_events',[])
        for i,e in enumerate(events):
            if e.get('tool_phase')=='requested' or e.get('observation') is not None or e.get('stop_code'):
                history+=f"- Event {i}: {e.get('tool_name') or e['action_category']}; result {e['step_result']}"
                if e.get('stop_code'):history+='; stop '+e['stop_code']
                if e.get('observation') is not None:history+='; '+json.dumps(e['observation'],sort_keys=True)
                history+='\n'
    return text,history

def partial_candidate(attempt,events,store=None):
    store=store or SkillStore()
    if not attempt.get('step_facts'):return None
    record={**attempt,'_events':events}
    strategy=Strategy(version=attempt.get('strategy_version','baseline'))
    text,history=render_procedure([record],strategy)
    from app.state import Observation
    from modules.login.verifier import verify
    current=next((e['observation'] for e in reversed(events) if e.get('observation') is not None),None)
    single_verified=bool(attempt.get('source')=='live' and attempt['verification']==Result.AUTHENTICATED_AND_VERIFIED
        and current and verify(Observation.model_validate(current))==Result.AUTHENTICATED_AND_VERIFIED
        and attempt.get('teacher_status')=='ANALYZED' and attempt.get('cleanup_result')=='CLOSED_AND_PROFILE_REMOVED'
        and not attempt.get('error') and not attempt.get('navigation_guard_status',{}).get('failed'))
    provenance={'source_run':attempt['run_id'],'source_traces':[attempt['run_id']+'.json'],
        'verified_run_ids':[attempt['run_id']] if single_verified else [],'verified_step_runs':[attempt['run_id']],
        'unverified_context_runs':[] if single_verified else [attempt['run_id']],
        'evidence_scope':'live_verified_single_run' if single_verified else 'live_partial' if attempt.get('source')=='live' else 'synthetic_partial',
        'live_verified':single_verified,'three_run_graduation':False,
        'strategy':strategy.model_dump(),'unresolved':[] if single_verified else attempt.get('missing_evidence',[]) or ['destination_verification']}
    artifact=store.draft(uuid4().hex,text,{'state':'DRAFT','passed':False,'skill_loading_verified':False},provenance,history)
    artifact.update(state='DRAFT',live_certified=False)
    return artifact
