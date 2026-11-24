"""Scoped login CLI. Live execution is opt-in; all evaluation remains synthetic."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from app.config import Config,ConfigurationError,ROOT
from app.model import OpenAIReasoner,ReplayReasoner
from app.codex_reasoner import CodexReasoner
from evals.fixture import fixture_server,SCENARIOS
from learning.runner import learn,local_attempt
from deep_agents.schemas import DEFAULT_TASK
from deep_agents.controller import run_synthetic,run_live
from learning.skill_storage.registry import SkillStore


def load_evidence():
    from modules.login.evidence import EvidenceConfig
    return EvidenceConfig.load(os.getenv('NETCORE_EVIDENCE_FILE'))


def readiness(config):
    from modules.login.evidence import EvidenceConfig
    evidence_valid=False
    evidence_error=False
    try:
        evidence_valid=EvidenceConfig.load(config.evidence_file) is not None
    except Exception:
        evidence_error=True
    credentials_present=all(bool(os.getenv(name)) for name in ('NETCORE_ACCOUNT_NAME','NETCORE_USERNAME','NETCORE_PASSWORD'))
    return {'live_enabled':config.live_login_enabled,'headed':not config.headless,
        'one_submission':config.max_login_submissions==1,'teacher_live_replay_disabled':not config.teacher_live_replay_enabled,
        'private_credentials_present':credentials_present,'api_key_present':bool(os.getenv('OPENAI_API_KEY')),
        'model_selection_valid':os.getenv('OPENAI_MODEL','gpt-6-luna')=='gpt-6-luna',
        'reviewed_evidence_configured':evidence_valid,'evidence_config_error':evidence_error,
        'model_availability':'NOT_CHECKED_WITHOUT_AUTHORIZED_RUN','live_execution':'NOT_EXECUTED',
        'verification_readiness':'REVIEWED_CONFIG_AVAILABLE' if evidence_valid else 'FIRST_RUN_WILL_REMAIN_UNVERIFIED_UNTIL_EVIDENCE_IS_REVIEWED'}


async def run(args):
    if args.command == 'sms-preflight':
        from modules.sms.contract import load_sms_payload
        from modules.sms.preflight import Preflight, verify_future_schedule
        from modules.sms.skill import load_skill
        from modules.whatsapp.workflow import run_human_assisted

        if bool(args.payload) != bool(args.approve_payload_sha256) or (args.sms_teacher or args.through_preview) and not args.payload:
            raise ConfigurationError('Supply the SMS payload and its approved SHA-256')
        if args.sms_tag_investigation and (not args.through_preview or not args.sms_iteration):
            raise ConfigurationError('SMS Tag investigation requires --through-preview and a named iteration')
        if args.sms_reconcile_before_write and not args.sms_tag_investigation:
            raise ConfigurationError('Use --sms-reconcile-before-write with --sms-tag-investigation')
        payload, digest = load_sms_payload(args.payload, approved_digest=args.approve_payload_sha256) if args.payload else (None, None)
        attestation = args.operator_confirmed_unused_name
        if attestation is not None and (not args.through_preview or not payload or attestation != payload.campaign_name):
            raise ConfigurationError('Operator name evidence must match the exact SMS payload name and requires --through-preview')
        scheduled = None
        if payload:
            scheduled = verify_future_schedule()
        skill = load_skill(version=args.sms_skill_version, expected_digest=args.sms_skill_sha256,
                           task_digest=digest) if args.through_preview else None
        diagnostic = Preflight(args.operator_review_seconds, payload=payload, teacher=args.sms_teacher or args.through_preview,
                               preview=args.through_preview, digest=digest, operator_confirmed_unused_name=attestation,
                               skill=skill, iteration=args.sms_iteration, tag_investigation=args.sms_tag_investigation,
                               reconcile_before_write=args.sms_reconcile_before_write)
        handoff = argparse.Namespace(**vars(args), dashboard_handoff_only=True, operator_confirmed_login=True,
                                     task_api_budget=0)
        result = await run_human_assisted(handoff, after_dashboard_handoff=diagnostic,
                                          before_dashboard_handoff=diagnostic.start)
        fields = ('run_id', 'status', 'last_verified_step', 'login_verification', 'authentication_verified',
                  'login_doer_outcome', 'dashboard_handoff_evidence', 'failure', 'elapsed_seconds', 'cleanup',
                  'login_doer_usage', 'task_doer_usage', 'teacher_usage', 'artifact_directory')
        details = {field: result.get(field) for field in fields}
        details.update(payload_path=args.payload, payload_sha256=digest, future_schedule=scheduled.isoformat() if scheduled else None)
        artifact = Path(result['artifact_directory']) / 'sms-result.json'
        artifact.write_text(json.dumps(details, indent=2) + '\n', encoding='utf-8')
        evidence = result.get('dashboard_handoff_evidence') or {}
        review = evidence.get('review_status')
        if review == 'SMS_REVIEW_WINDOW_EXPIRED':
            print('SMS inspection window expired.', flush=True)
        elif review != 'RELEASED':
            failure = (result.get('failure') or {}).get('code') or result['status']
            print(evidence.get('message') if review is None and evidence.get('message') else
                  f'SMS stopped at {review or failure}.', flush=True)
        expected = {'SMS_PREVIEW_VERIFIED' if args.through_preview else 'SMS_PREFLIGHT_VERIFIED'}
        if args.sms_tag_investigation:
            expected.add('SMS_TAG_INVESTIGATION_COMPLETE')
        if (result['status'] not in expected or review != 'RELEASED'
                or result['cleanup'] != 'CLOSED_AND_PROFILE_REMOVED'):
            raise SystemExit(1)
        return
    if args.command=='whatsapp-contract':
        from modules.whatsapp.contract import v2_contract_summary
        print(json.dumps(v2_contract_summary(),indent=2));return
    if args.command=='whatsapp':
        from modules.whatsapp.workflow import run_v2
        print(json.dumps(await run_v2(args),indent=2));return
    if args.command=='whatsapp-login-capture':
        from modules.whatsapp.login_capture import run_login_capture
        await run_login_capture(args.authorized_live,dashboard_only=args.dashboard_only);return
    if args.command=='whatsapp-human-assisted':
        from modules.whatsapp.workflow import run_human_assisted
        if getattr(args, 'unattended_config', None):
            result = await run_human_assisted(args)
            print(json.dumps({key: result.get(key) for key in ('run_id', 'status', 'last_verified_step', 'selected_skill_sha256', 'task_doer_invoked',
                  'login_verification', 'login_gate_status', 'reconciliation', 'schedule_draft_preview',
                  'failure', 'cleanup', 'login_doer_usage', 'task_doer_usage', 'teacher_usage')}, indent=2))
            read_only = result.get('unattended_action') == 'READ_DRAFT'
            saved_record = result.get('schedule_draft_preview') or {}
            verified = (result.get('status') == 'PERSISTED_SCHEDULE_MATCH' if read_only else
                        result.get('unattended_action') == 'SAVE_AND_PREVIEW'
                        and result.get('status') == 'DRAFT_RECORD_VERIFIED'
                        and saved_record.get('status') == 'PERSISTED_DRAFT_MATCH'
                        and saved_record.get('netcore_status') == 'Draft'
                        and saved_record.get('configuration_source') == 'SAVED_RECORD'
                        and all(saved_record.get(key) for key in ('campaign_id', 'campaign_name', 'netcore_status',
                                                                 'date', 'time', 'timezone', 'sender', 'template')))
            if (not verified
                    or result.get('authentication_verified') is not True
                    or result.get('cleanup') != 'CLOSED_AND_PROFILE_REMOVED'):
                raise SystemExit(1)
            return
        runs = args.operator_confirmed_runs
        if runs != 1 and not args.operator_confirmed_login:
            raise ConfigurationError('Multiple headed runs require explicit operator-confirmed mode')
        if not args.operator_confirmed_login:
            print(json.dumps(await run_human_assisted(args), indent=2))
            return
        for index in range(1, runs + 1):
            print(f'OPERATOR_CONFIRMED_RUN {index}/{runs}', flush=True)
            result = await run_human_assisted(args)
            evaluation = result.get('evaluation') or {}
            print('OPERATOR_RUN_SUMMARY ' + json.dumps({
                'attempt': index,
                **{key: result.get(key) for key in ('run_id', 'status', 'login_verification',
                   'authentication_verified', 'last_verified_step', 'written_fields', 'verified_fields',
                   'teacher_status', 'failure', 'cleanup', 'artifact_directory', 'login_doer_outcome',
                   'audience_transition', 'reconciliation', 'selected_skill_sha256', 'ga_readback', 'candidate_review')},
                'schedule_draft_preview': result.get('schedule_draft_preview'),
                'write_results': evaluation.get('write_results', []),
            }), flush=True)
            failure = result.get('failure') or {}
            if getattr(args, 'mcp_ga_setup', False):
                clean = result.get('cleanup') == 'CLOSED_AND_PROFILE_REMOVED'
                passed = clean and result.get('status') == 'MCP_GA_VERIFIED'
                print('MCP_GA_ATTEMPT_RESULT ' + json.dumps({
                    'attempt': index, 'run_id': result.get('run_id'), 'exit_code': int(not passed),
                }), flush=True)
                if passed:
                    continue
                retry = result.get('mcp_ga_retry')
                if index < runs and clean and result.get('status') == 'STUCK' and retry and not failure.get('unsafe'):
                    args.mcp_ga_retry = retry
                    continue
                raise SystemExit(1)
            mcp_success = ('MCP_CAMPAIGN_NAME_WRITE_VERIFIED' if args.mcp_campaign_name_write
                           else 'MCP_READ_ONLY_DISCOVERY_VERIFIED')
            if getattr(args, 'browser_mode', 'direct') == 'mcp' and (
                    result.get('status') != mcp_success
                    or result.get('cleanup') != 'CLOSED_AND_PROFILE_REMOVED'):
                raise SystemExit(1)
            if getattr(args, 'campaign_b_draft', False) and result.get('status') not in {'DRAFT_PREVIEW_VERIFIED', 'DRAFT_RECORD_VERIFIED'}:
                raise SystemExit(1)
            if (result.get('cleanup') != 'CLOSED_AND_PROFILE_REMOVED' or failure.get('unsafe')
                    or failure.get('code') == 'OPERATOR_CANCELLED'):
                print('OPERATOR_BATCH_STOPPED', flush=True)
                break
            if result.get('status') == 'STUCK' and result.get('teacher_status') != 'ANALYZED':
                print('OPERATOR_BATCH_STOPPED TEACHER_REVIEW_UNAVAILABLE', flush=True)
                break
        return
    if args.command=='whatsapp-setup':
        raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp')
        from modules.whatsapp.contract import Phase1Limits,Page1Limits
        if not args.authorized_live or not args.authorized_setup:
            raise ConfigurationError('Explicit login and Setup authorization required before configuration loading')
        limits_type=Page1Limits if args.doer_only else Phase1Limits
        limits=limits_type(args.login_api_budget,args.setup_api_budget,args.teacher_request_budget,
            args.total_api_budget,args.checkpoint_seconds,args.setup_seconds,args.hold_seconds).validate()
        from modules.whatsapp.workflow import run_phase1
        if args.doer_only:
            result=await run_phase1(Config.load(),args.authorized_live,args.authorized_setup,limits,doer_only=True)
        else:result=await run_phase1(Config.load(),args.authorized_live,args.authorized_setup,limits)
        print(json.dumps(result,indent=2));return
    if args.command=='teacher-replay':
        from deep_agents.teacher_replay import teacher_replay
        result=await teacher_replay(args.run_id,args.authorized_api,args.api_request_budget,args.max_output_tokens)
        print(json.dumps(result,indent=2))
        if result['teacher_status']!='ANALYZED':raise SystemExit(1)
        return

    if args.command=='draft':
        import re
        if not re.fullmatch('[a-f0-9]{32}',args.run_id):raise ConfigurationError('Invalid sanitized run ID')
        from learning.skill_storage.procedure import partial_candidate
        result=json.loads((ROOT/'outputs'/f'{args.run_id}-result.json').read_text())
        events=json.loads((ROOT/'outputs'/f'{args.run_id}.json').read_text())
        print(json.dumps(partial_candidate(result,events),indent=2));return
    if args.command=='learn':
        if args.mode!='offline':raise ConfigurationError('Repeated evaluation is offline-only')
        from learning.three_run import run_three
        path=os.getenv('CHROMIUM_EXECUTABLE_PATH','')
        if not Path(path).is_file():raise ConfigurationError('Set CHROMIUM_EXECUTABLE_PATH; controlled learning does not load private .env')
        result=await run_three(Config(path),args.task,args.scenario)
        print(json.dumps({k:result[k] for k in ('cycle_id','candidate_skill','graduation','actual_api_requests')},indent=2));return
    if args.command=='learn-three':
        from learning.three_run import run_three
        if args.mode=='live':
            if not args.authorized_live or not args.authorized_live_comparison or not args.total_api_budget:
                raise ConfigurationError('Explicit three-run live authorization and budgets required before loading configuration')
            cfg=Config.load()
        else:
            path=os.getenv('CHROMIUM_EXECUTABLE_PATH','')
            if not Path(path).is_file():raise ConfigurationError('Set CHROMIUM_EXECUTABLE_PATH for controlled fixtures; private .env is not loaded')
            cfg=Config(path)
        if args.evidence_review_file:
            from dataclasses import replace
            cfg=replace(cfg,evidence_file=args.evidence_review_file)
        result=await run_three(cfg,args.task,args.scenario,args.mode,args.learning_runs,
            args.authorized_live,args.authorized_live_comparison,args.doer_api_budget,args.teacher_api_budget,args.total_api_budget,
            tuple(args.approve_origin),args.hold_seconds,args.review_evidence)
        print(json.dumps({k:result[k] for k in ('cycle_id','mode','stop_reason','candidate_skill','graduation','actual_api_requests')},indent=2));return
    if args.command=='skill':
        store=SkillStore()
        if args.skill_command=='status':
            snapshot=store.snapshot()
            print(json.dumps({'approved_digest':snapshot.digest if snapshot else None,'state':'APPROVED' if snapshot else 'NONE'}))
        else:
            method=store.promote if args.skill_command=='promote' else store.rollback
            digest=method(args.version,args.approve_digest,args.approved)
            print(json.dumps({'operation':args.skill_command,'approved_digest':digest}))
        return
    if args.command=='live' and (not getattr(args,'authorized_live',False) or not 1<=getattr(args,'api_request_budget',0)<=8):
        raise ConfigurationError('Explicit live authorization and an API request budget from 1 to 8 are required')
    cfg=Config.load()
    if args.command=='readiness':
        print(json.dumps(readiness(cfg),indent=2));return
    if args.command=='live':
        from dataclasses import replace

        calibration = getattr(args, 'calibrate_whatsapp_login', False)
        if calibration:
            if (not args.login_only or args.review_evidence or args.evidence_review_file
                    or args.hold_seconds or args.approve_origin or args.diagnostics):
                raise ConfigurationError('WhatsApp Login calibration requires the isolated Login-only mode.')
            result = await run_live(replace(cfg, max_actions=120, headless=False, evidence_file=None),
                args.task, args.authorized_live, args.api_request_budget,
                teacher_request_budget=0, review=False, learning=False,
                evidence_review=False, login_calibration=True)
            calibration_report = result.get('login_calibration') or {
                'run_id': result.get('run_id'), 'status': 'BLOCKED', 'authentication_verified': False,
                'failed_checks': [result.get('error') or result.get('reason_code') or 'CALIBRATION_NOT_CAPTURED'],
            }
            print(json.dumps(calibration_report, indent=2))
            return
        if args.evidence_review_file:
            cfg=replace(cfg,evidence_file=args.evidence_review_file)
        if args.login_only and args.review_evidence:
            # Readiness, final verification and held discovery share this cap.
            cfg = replace(cfg, max_actions=120)
        result=await run_live(cfg,args.task,args.authorized_live,args.api_request_budget,
            tuple(args.approve_origin),args.hold_seconds,args.diagnostics,
            teacher_request_budget=0 if args.login_only else args.teacher_request_budget,
            evidence_review=args.review_evidence,review=not args.login_only,learning=not args.login_only)
        print(json.dumps(result,indent=2));return
    if args.command in {'run','fixture','smoke','learn'}:
        mode='offline' if args.command=='smoke' else args.mode
        if args.command=='learn' and mode!='offline':
            raise ConfigurationError('Repeated evaluation is offline-only; use one explicitly budgeted run for actual inference')
        result=await run_synthetic(cfg,getattr(args,'task',DEFAULT_TASK),
            'already' if args.command=='smoke' else args.scenario,mode,
            api_request_budget=getattr(args,'api_request_budget',0),review=True,learning=True,
            diagnostics=getattr(args,'diagnostics',False))
        print(json.dumps(result,indent=2))
        if result['error']:
            raise RuntimeError('Execution stopped; inspect sanitized evidence')
        return
    provider='openai' if args.command=='models' else args.reasoner
    reasoner={'offline':ReplayReasoner,'openai':OpenAIReasoner,'codex':CodexReasoner}[provider]()
    if not reasoner.is_mock:
        await reasoner.validate_model()
    if args.command=='models':
        print('Configured model is visible; inference compatibility requires a successful request.')
    elif args.command=='legacy-fixture':
        with fixture_server() as server:
            outcome,trace=await local_attempt(cfg,reasoner,server,args.scenario,strategy=True)
        print(json.dumps({'verification':outcome,'run_id':trace.run_id,'legacy':True}))
    elif args.command=='legacy-learn':
        print(json.dumps(await learn(cfg,reasoner),indent=2))


def build_parser():
    parser=argparse.ArgumentParser(description='Genuine Deep Agents login, trusted credentials and controlled learning')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('whatsapp-contract',help='Inspect the V2 contract; no private values or external actions')
    whatsapp=sub.add_parser('whatsapp',help='Canonical V2 draft workflow; requires separate explicit live authorization')
    whatsapp.add_argument('--payload',default=str(ROOT/'credentials'/'whatsapp-v2-payload.json'))
    whatsapp.add_argument('--approve-payload-sha256',required=True)
    whatsapp.add_argument('--ui-evidence',help='Optional incremental reviewed UI/record evidence; no guessed selectors')
    whatsapp.add_argument('--approve-ui-sha256',help='Exact approval required when --ui-evidence is supplied')
    whatsapp.add_argument('--login-evidence-file',help='Existing Login evidence contract; private observed/reviewed input')
    whatsapp.add_argument('--review-login-evidence',action='store_true',help='Reuse bounded existing interactive Login evidence review, without resubmission')
    whatsapp.add_argument('--login-review-seconds',type=int,default=120)
    for permission in ('live','inference','edits','persistence','vision','learning','fresh-iterations','candidate-evaluation'):
        whatsapp.add_argument('--authorized-'+permission,action='store_true')
    whatsapp.add_argument('--iterations',type=int,default=1)
    whatsapp.add_argument('--candidate')
    whatsapp.add_argument('--browser-mode', choices=('direct', 'mcp'), default='direct',
                          help='Use the private in-process MCP connection for WhatsApp task steps')
    whatsapp.add_argument('--login-api-budget',type=int,default=6)
    whatsapp.add_argument('--total-api-budget',type=int,default=24)
    whatsapp.add_argument('--max-turns',type=int,default=20)
    whatsapp.add_argument('--session-seconds',type=int,default=900)
    login_capture=sub.add_parser('whatsapp-login-capture',help='Capture passive Login signals after operator sign-in')
    login_capture.add_argument('--authorized-live',action='store_true')
    login_capture.add_argument('--dashboard-only',action='store_true')
    sms_preflight = sub.add_parser('sms-preflight', help='Check SMS prerequisites after protected operator-confirmed Login')
    sms_preflight.add_argument('--authorized-live', action='store_true')
    sms_preflight.add_argument('--authorized-inference', action='store_true')
    sms_preflight.add_argument('--payload')
    sms_preflight.add_argument('--approve-payload-sha256')
    sms_preflight.add_argument('--sms-teacher', action='store_true', help='Reserve one real, analysis-only Teacher request')
    sms_preflight.add_argument('--through-preview', action='store_true', help='Authorize one fixed SMS workflow after all live preflight checks')
    sms_preflight.add_argument('--operator-confirmed-unused-name', metavar='EXACT_NAME',
                              help='Attest at payload approval that you already searched this exact name in the same tenant and confirmed it is unused')
    sms_preflight.add_argument('--sms-iteration', choices=('baseline', 'recovery', 'retention'))
    sms_preflight.add_argument('--sms-tag-investigation', action='store_true',
                              help='After read-only name reconciliation, investigate the Setup Tag binding with all write tools withheld')
    sms_preflight.add_argument('--sms-reconcile-before-write', action='store_true',
                              help='Offer payload approval only after Campaigns verifies the exact name is unused')
    sms_preflight.add_argument('--sms-skill-version', help='Load this exact unapproved, validated SMS candidate')
    sms_preflight.add_argument('--sms-skill-sha256', help='Pin the baseline or candidate bytes')
    sms_preflight.add_argument('--login-api-budget', type=int, default=6)
    sms_preflight.add_argument('--total-api-budget', type=int, default=6)
    sms_preflight.add_argument('--login-evidence-file')
    sms_preflight.add_argument('--operator-review-seconds', type=int, default=300)
    sms_preflight.add_argument('--session-seconds', type=int, default=900)
    human_assisted=sub.add_parser('whatsapp-human-assisted',
        help='One headed Login attempt and consent-gated WhatsApp Doer diagnostic (read-only by default)')
    human_assisted.add_argument('--browser-mode', choices=('direct', 'mcp'), default='direct',
                                help='Opt in to operator-confirmed MCP Setup validation')
    human_assisted.add_argument('--mcp-campaign-name-write', action='store_true',
        help='Validate one pinned Campaign Name edit via MCP with separate operator approval')
    human_assisted.add_argument('--mcp-ga-setup', action='store_true',
        help='Validate only the pinned GA Tracking boolean via MCP at Setup')
    for permission in ('live','inference','candidate-evaluation'):
        human_assisted.add_argument('--authorized-'+permission,action='store_true')
    human_assisted.add_argument('--manual-form-test',action='store_true',
        help='Authorize fixed-payload Setup field entry without persistence; authentication remains independently verified')
    login_mode = human_assisted.add_mutually_exclusive_group()
    login_mode.add_argument('--operator-login', action='store_true',
        help='Sign in personally in the owned browser, then independently verify the same session before Setup')
    login_mode.add_argument('--operator-confirmed-login', action='store_true',
        help='Explicit operator tenant confirmation in the protected tab, followed by separate Setup write approval')
    human_assisted.add_argument('--operator-confirmed-runs', type=int, choices=(1, 2, 3), default=1)
    human_assisted.add_argument('--page1-through-audience', action='store_true')
    human_assisted.add_argument('--through-schedule', action='store_true')
    human_assisted.add_argument('--reconcile-run')
    human_assisted.add_argument('--dashboard-handoff-only', action='store_true',
        help='Run the Login Doer, confirm the tenant, and hold the owned dashboard without opening a campaign')
    human_assisted.add_argument('--save-schedule-draft', action='store_true')
    human_assisted.add_argument('--one-approval-draft', action='store_true')
    human_assisted.add_argument('--preview-draft-validation', action='store_true')
    draft_payload = human_assisted.add_mutually_exclusive_group()
    draft_payload.add_argument('--campaign-a-draft', action='store_true', help='Use the reviewed campaign A payload for one operator-confirmed draft')
    draft_payload.add_argument('--campaign-b-draft', action='store_true', help='Use the reviewed campaign B payload for one operator-confirmed draft')
    human_assisted.add_argument('--campaigns-diagnostic', action='store_true')
    human_assisted.add_argument('--campaigns-reload-once', action='store_true')
    human_assisted.add_argument('--through-audience', action='store_true')
    human_assisted.add_argument('--capture-audience-failure', action='store_true')
    human_assisted.add_argument('--authorized-learning',action='store_true',
        help='Reserve one Teacher review and permit an unapproved candidate after recoverable failure; no live retry')
    human_assisted.add_argument('--login-api-budget',type=int,default=6)
    human_assisted.add_argument('--task-api-budget',type=int,default=12)
    human_assisted.add_argument('--total-api-budget',type=int,default=20)
    human_assisted.add_argument('--browser-actions',type=int,default=300)
    human_assisted.add_argument('--max-turns',type=int,default=20)
    human_assisted.add_argument('--doer-tool-budget',type=int,default=160)
    human_assisted.add_argument('--browser-operation-seconds',type=int,default=30)
    strategy = human_assisted.add_mutually_exclusive_group()
    strategy.add_argument('--candidate', help='Evaluate an existing WhatsApp candidate with its exact --candidate-sha256')
    strategy.add_argument('--baseline-sha256', help='Select the exact WhatsApp module baseline bytes for this run')
    human_assisted.add_argument('--candidate-sha256', help='Expected SHA-256 of the explicitly selected candidate')
    human_assisted.add_argument('--operator-review-seconds',type=int,default=300)
    human_assisted.add_argument('--session-seconds',type=int,default=900)
    human_assisted.add_argument('--login-evidence-file',
        help='Optional existing private Login evidence contract; campaign values remain in the approved private payload')
    human_assisted.add_argument('--unattended-config', help='Trusted read-only operator approval JSON')
    human_assisted.add_argument('--unattended-payload', help='Read-only payload file matched to the unattended approval digest')
    human_assisted.add_argument('--ga-setup-payload', help='Fixed payload for the operator-confirmed GA Setup-only test')
    human_assisted.add_argument('--ga-setup-sha256', help='Exact reviewed GA payload SHA-256')
    human_assisted.add_argument('--ga-teacher-inspection', action='store_true',
        help='Allow bounded read-only DOM/AX and scoped vision review of the held GA Setup tab')
    replay=sub.add_parser('teacher-replay',help='Teacher-only review of saved real evidence; no browser/fixtures/credentials')
    replay.add_argument('--run-id',required=True)
    replay.add_argument('--mode',choices=['openai'],default='openai')
    replay.add_argument('--authorized-api',action='store_true')
    replay.add_argument('--api-request-budget',type=int,default=0)
    replay.add_argument('--max-output-tokens',type=int,default=2000)
    for name in ('run','fixture','learn'):
        command=sub.add_parser(name)
        command.add_argument('--task',default=DEFAULT_TASK)
        mode=command.add_mutually_exclusive_group()
        mode.add_argument('--mode',choices=['offline','openai'],default='offline')
        mode.add_argument('--offline',dest='mode',action='store_const',const='offline')
        mode.add_argument('--reasoner',dest='mode',choices=['offline','openai'])
        command.add_argument('--scenario',choices=sorted(SCENARIOS),default='valid')
        command.add_argument('--api-request-budget',type=int,default=0)
        command.add_argument('--review',action='store_true',help='Compatibility flag; Teacher review is automatic')
        command.add_argument('--diagnostics',action='store_true')
    sub.add_parser('smoke');sub.add_parser('models');sub.add_parser('readiness')
    for name in ('legacy-fixture','legacy-learn'):
        command=sub.add_parser(name)
        mode=command.add_mutually_exclusive_group()
        mode.add_argument('--reasoner',choices=['offline','openai','codex'],default='offline')
        mode.add_argument('--offline',dest='reasoner',action='store_const',const='offline')
        if name=='legacy-fixture':
            command.add_argument('--scenario',choices=sorted(SCENARIOS),default='valid')
    live=sub.add_parser('live')
    live.add_argument('--task',default=DEFAULT_TASK)
    live.add_argument('--login-only',action='store_true',help='One login execution without Teacher review, learning, candidate generation or promotion')
    live.add_argument('--authorized-live',action='store_true')
    live.add_argument('--api-request-budget',type=int,default=0)
    live.add_argument('--approve-origin',action='append',default=[])
    live.add_argument('--hold-seconds',type=int,default=0)
    live.add_argument('--diagnostics',action='store_true')
    live.add_argument('--teacher-request-budget',type=int,default=2)
    live.add_argument('--review-evidence',action='store_true')
    live.add_argument('--evidence-review-file')
    live.add_argument('--calibrate-whatsapp-login', action='store_true',
        help='Collect protected read-only Login signal candidates without approving evidence or starting Setup')
    cycle=sub.add_parser('learn-three')
    cycle.add_argument('--mode',choices=['controlled','live'],default='controlled')
    cycle.add_argument('--learning-runs',type=int,default=3)
    cycle.add_argument('--task',default=DEFAULT_TASK)
    cycle.add_argument('--scenario',choices=sorted(SCENARIOS),default='valid')
    cycle.add_argument('--authorized-live',action='store_true')
    cycle.add_argument('--authorized-live-comparison',action='store_true')
    cycle.add_argument('--doer-api-budget',type=int,default=6)
    cycle.add_argument('--teacher-api-budget',type=int,default=2)
    cycle.add_argument('--total-api-budget',type=int,default=0)
    cycle.add_argument('--approve-origin',action='append',default=[])
    cycle.add_argument('--hold-seconds',type=int,default=0)
    cycle.add_argument('--review-evidence',action='store_true')
    cycle.add_argument('--evidence-review-file')
    draft=sub.add_parser('draft')
    draft.add_argument('--run-id',required=True)
    skill=sub.add_parser('skill')
    skill_sub=skill.add_subparsers(dest='skill_command',required=True)
    skill_sub.add_parser('status')
    for name in ('promote','rollback'):
        operation=skill_sub.add_parser(name)
        operation.add_argument('--version',required=True)
        operation.add_argument('--approve-digest',required=True)
        operation.add_argument('--approved',action='store_true')
    return parser


def main():
    parser=build_parser()
    args=parser.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('Cancelled; owned browser cleanup was requested.')
        if getattr(args, 'unattended_config', None):
            raise SystemExit(130)
    except Exception as error:
        from modules.whatsapp.contract import V2Stop
        from browser.evidence_diagnostics import EvidenceFailure
        if isinstance(error,EvidenceFailure):
            print(error.code);raise SystemExit(1)
        print(error.code if isinstance(error,V2Stop) else str(error) if isinstance(error,ConfigurationError) else 'Execution stopped; inspect sanitized outputs and configuration.')
        raise SystemExit(1)

if __name__=='__main__':
    main()
