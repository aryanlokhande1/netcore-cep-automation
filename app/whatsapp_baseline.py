"""Operator-supervised baseline, optionally preceded by the existing Login Doer.

Default manual/baseline mode loads no credential or model provider. Optional
Login Doer and autonomous Setup/Teacher have separate explicit finite budgets.
"""
import argparse
import asyncio
import json
import sys
from pathlib import Path
from dataclasses import replace
from time import monotonic
from urllib.parse import urlsplit

from app.config import Config,ConfigurationError,OriginPolicy,ROOT
from app.safety import LivePermit
from app.trace import Trace
from browser.protected_tabs import create_protected_tab
from browser.stagehand_client import StagehandClient
from modules.whatsapp.contract import SETUP_URL
from modules.whatsapp.browser_baseline import BrowserBaseline,BaselineStop,route_allowed,sanitized_action_error,reject_security_negatives
from modules.whatsapp.browser_baseline_dom import DISCOVER
from modules.whatsapp.browser_baseline_dom import COMMIT_BLOCK


class BaselinePolicy(OriginPolicy):
    """Only this isolated session narrows document navigation after manual login."""
    setup_only=False
    def permits(self,url):
        return super().permits(url) and (not self.setup_only or route_allowed(url))


class BaselineBrowser(StagehandClient):
    login_closed=False
    baseline_checkpoint=None
    setup_tab_attempted=False
    def claim_login_submission(self):
        if self.login_closed:raise PermissionError('LOGIN_PHASE_CLOSED')
        super().claim_login_submission()


def validate_login_options(args):
    mode=getattr(args,'login_mode','manual');cap=getattr(args,'login_api_budget',0)
    if mode not in {'manual','doer'}:raise ConfigurationError('Use manual or doer login mode')
    if mode=='doer' and not 1<=cap<=6:raise ConfigurationError('Doer login requires explicit --login-api-budget from 1 to 6')
    if mode=='manual' and cap:raise ConfigurationError('Manual login does not accept an API budget')
    return mode


async def automated_login(browser,tools,model,budget,*,offline=False,login_seconds=180,skill_snapshot=None):
    from modules.whatsapp.baseline_login import execute_login
    return await execute_login(browser,tools,model,budget,offline=offline,login_seconds=login_seconds,skill_snapshot=skill_snapshot)


async def prepare_automated_login(args):
    """Authorized live-only factory; local tests never call this credential path."""
    if not args.authorized_live or validate_login_options(args)!='doer':
        raise ConfigurationError('Explicit live Doer mode and bounded API authorization required')
    from app.safety import authorize_live
    from app.model_settings import ModelSettings
    from modules.login.credentials import LocalCredentials,CredentialBroker
    from modules.login.evidence import EvidenceConfig
    from modules.whatsapp.login_handoff import HandoffLoginTools
    from deep_agents.models import openai_model
    from deep_agents.permissions import InferenceBudget
    from learning.skill_storage.registry import SkillStore
    from learning.policies import Strategy
    # Reuse the existing configuration/credential loaders, without displaying or
    # copying secrets. Manual mode never enters this function.
    loaded_config=Config.load()
    config=replace(loaded_config,executable=args.chromium,max_actions=250,
        timeout_seconds=2*args.login_seconds+300+args.hold_seconds,
        evidence_file=getattr(args,'auth_evidence_file',None) or loaded_config.evidence_file)
    permit=authorize_live(config,args.authorized_live,args.login_api_budget)
    settings=ModelSettings.load()
    if args.login_api_budget>settings.cycle_requests:raise ConfigurationError('Login budget exceeds configured request cap')
    credentials=LocalCredentials.load()
    evidence=EvidenceConfig.for_session(config.evidence_file,review_mode=True)
    snapshot=SkillStore().snapshot()
    strategy=Strategy.model_validate(snapshot.metadata['strategy']) if snapshot else Strategy()
    trace=Trace(strategy_version=strategy.version);trace.reasoner_mode='deepagents_openai_gpt-6-luna'
    budget=InferenceBudget(args.login_api_budget,role_limits={'doer':args.login_api_budget,'teacher':0})
    budget.max_input_tokens=settings.cycle_input
    budget.max_output_tokens=min(settings.cycle_output,args.login_api_budget*settings.doer_output)
    model=openai_model('doer',settings,trace=trace,budget=budget)
    try:await model.root_async_client.models.retrieve('gpt-6-luna')
    except Exception:raise ConfigurationError('Login model availability failed before browser launch; manual mode remains available') from None
    browser=BaselineBrowser(config,BaselinePolicy(set(permit.origins)),trace,evidence=evidence,live_permit=permit)
    if getattr(args,'discover_authentication',False):browser.evidence_review=True
    tools=HandoffLoginTools(browser,CredentialBroker(browser,credentials));tools.strategy=strategy
    print(f'LOGIN ONLY BUDGET: requests<={args.login_api_budget}, output/request<={settings.doer_output}, '+
        f'total output<={budget.max_output_tokens}; Setup requests=0; Teacher requests=0.',flush=True)
    return browser,tools,model,budget,snapshot


async def confirm_login(browser,login_result=None,*,automated=False,seconds=180):
    """Retain C for either mode; a Doer report is never operator consent."""
    if browser.baseline_checkpoint is not None:raise BaselineStop('LOGIN_CHECKPOINT_ALREADY_USED')
    async def eligible():
        if browser.fixture:
            from app.safety import require_fixture_browser
            require_fixture_browser(browser)
            if not browser.policy.permits(await browser.page.url()):raise BaselineStop('OPERATOR_SESSION_NOT_AT_AUTHORIZED_HOST')
        else:
            source=urlsplit(await browser.page.url())
            if source.scheme!='https' or source.hostname!=browser.config.expected_host or source.port not in (None,443):
                raise BaselineStop('OPERATOR_SESSION_NOT_AT_AUTHORIZED_HOST')
        await reject_security_negatives(browser)
        denied=await browser.page.evaluate('('+DISCOVER.replace('SETTINGS',json.dumps({'values':{}}))+').access_denied')
        if denied is True:raise BaselineStop('ACCESS_DENIED')
        if automated:
            from modules.whatsapp.baseline_login import validate_handoff
            await validate_handoff(browser,login_result)
        return await login_context(browser)
    before=await eligible() if automated else None
    prompt=('Login Doer has stopped; credentials cannot be retried. Review the SAME authenticated account context. '
        if automated else 'Log in manually ONCE in this owned Chromium. No automation reads or enters credentials. Do not retry authentication. ')
    choice=await key(prompt+'Stop on challenge/rejection/expiry/denial. Press C to confirm this account for the baseline; Enter cancels.',
        {'c'},seconds,browser)
    if choice!='c':raise BaselineStop('OPERATOR_LOGIN_CHECKPOINT_DECLINED')
    after=await eligible()
    if before is not None and before!=after:raise BaselineStop('LOGIN_CHECKPOINT_CONTEXT_CHANGED')
    browser.login_closed=True
    browser.baseline_checkpoint=after
    print('LOGIN_HANDOFF_ACCEPTED: HUMAN_CONFIRMED_FOR_POC',flush=True)
    return {'authentication':'OPERATOR_CONFIRMED_ONLY','checkpoint':'HUMAN_CONFIRMED_FOR_POC'}


async def login_context(browser):
    from modules.whatsapp.session import source_signature
    await browser.guard.ensure_observable()
    if browser.guard.failed or browser.guard.blocking_hosts:raise BaselineStop('BROWSER_PROTECTION_LOST')
    return (source_signature(browser),await browser.page.url(),browser.login_submissions,
        id(browser.browser.context),id(browser.process))


async def prepare_setup(browser,destination=SETUP_URL):
    if browser.baseline_checkpoint is None:raise BaselineStop('LOGIN_CHECKPOINT_REQUIRED')
    if browser.setup_tab_attempted:raise BaselineStop('SETUP_TAB_ALREADY_ATTEMPTED')
    if await login_context(browser)!=browser.baseline_checkpoint:raise BaselineStop('LOGIN_CHECKPOINT_CONTEXT_CHANGED')
    await reject_security_negatives(browser)
    if browser.fixture:
        from app.safety import require_fixture_browser
        require_fixture_browser(browser)
        if not browser.policy.permits(destination):raise BaselineStop('WRONG_SETUP_ROUTE')
    elif destination!=SETUP_URL:raise BaselineStop('WRONG_SETUP_ROUTE')
    browser.setup_tab_attempted=True
    original=browser.page;page,binding=await create_protected_tab(browser)
    browser.login_page=original;browser.page=page;browser.setup_binding=binding
    browser.policy.setup_only=True
    await page.add_init_script(COMMIT_BLOCK)
    await browser.navigate(destination)
    await page.wait_for_load_state('domcontentloaded',timeout=5000)
    return BrowserBaseline(browser,destination)


async def key(prompt,allowed,seconds,browser):
    print(prompt,flush=True)
    deadline=monotonic()+seconds
    while monotonic()<deadline:
        await browser.guard.ensure_observable()
        if browser.guard.failed or browser.guard.blocking_hosts:raise BaselineStop('BROWSER_PROTECTION_LOST')
        char=None
        if sys.platform=='win32':
            import msvcrt
            if msvcrt.kbhit():char=msvcrt.getwch().lower()
        else:
            import select
            if select.select([sys.stdin],[],[],0)[0]:char=sys.stdin.readline().strip().lower() or '\n'
        if char in allowed:return char
        if char in ('\n','\r'):return None
        if char=='\x03':raise KeyboardInterrupt
        await asyncio.sleep(.05)
    raise BaselineStop('OPERATOR_DEADLINE_REACHED')


def unique_reference(discovery,field):
    if field!='campaign_name':
        from modules.whatsapp.browser_baseline import field_reference
        return field_reference(discovery,field)
    matches=[c for c in discovery['controls'] if c['field_hint']==field and c['visible'] and c['enabled']]
    if len(matches)!=1:raise BaselineStop('VISIBLE_FIELD_NOT_UNIQUELY_MAPPED',field=field,matches=len(matches))
    return matches[0]['reference']


async def run_baseline(args):
    raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp; historical helpers remain importable')
    if not args.authorized_live:raise ConfigurationError('Explicit --authorized-live required; browser was not launched')
    login_mode=validate_login_options(args)
    autonomous=getattr(args,'autonomous_setup',False)
    if getattr(args,'automatic_handoff',False) and (not autonomous or login_mode!='doer'):raise ConfigurationError('Automatic handoff requires authorized Login Doer and autonomous Setup')
    if autonomous and args.teacher_api_budget<args.iterations:raise ConfigurationError('Reserve one Teacher review per attempt, including final review')
    if autonomous and (getattr(args,'diagnose_only',False) or not 1<=args.iterations<=5 or not 1<=args.setup_api_budget<=20 or not 1<=args.teacher_api_budget<=5):
        raise ConfigurationError('Autonomous mode requires iterations 1..5, Setup budget 1..20, Teacher budget 1..5 and no diagnose-only')
    if not getattr(args,'automatic_handoff',False) and not sys.stdin.isatty():raise ConfigurationError('Use an interactive operator CMD terminal')
    if not Path(args.chromium).is_file():raise ConfigurationError('Provide the installed Chromium executable with --chromium')
    if not 30<=args.login_seconds<=300 or not 1<=args.hold_seconds<=300:raise ConfigurationError('Invalid bounded login/inspection deadline')
    tools=model=budget=snapshot=None
    if login_mode=='doer':
        browser,tools,model,budget,snapshot=await prepare_automated_login(args)
    else:
        config=Config(args.chromium,headless=False,live_login_enabled=True,max_actions=250,
            timeout_seconds=args.login_seconds+300+args.hold_seconds)
        policy=BaselinePolicy({config.login_url.rstrip('/'),'https://'+config.expected_host})
        permit=LivePermit(config,policy.origins)
        browser=BaselineBrowser(config,policy,Trace(),live_permit=permit)
    browser.autonomous_setup=autonomous
    trace=browser.trace
    result={'run_id':trace.run_id,'workflow':'ISOLATED_BROWSER_BASELINE','source':'live',
        'real_netcore_campaign_name':'NOT_RUN','four_field_real_netcore':'NOT_RUN',
        'deep_agents_integration':'LOGIN_HANDOFF_ONLY' if login_mode=='doer' else 'MANUAL_BASELINE',
        'setup_tools_integration':'DEFERRED_PENDING_REAL_BASELINE','authentication':'NOT_CERTIFIED',
        'automatic_login_submissions':0,'manual_login_submissions':'NOT_MEASURED','model_requests':0,'teacher':'NOT_INVOKED','explicit_commits':0,
        'server_persistence':'UNDETERMINED','login_mode':login_mode,'login_doer':None}
    executor=None;stage='BROWSER_START'
    try:
        if login_mode=='doer':
            stage='LOGIN_DOER'
            result['login_doer']=await automated_login(browser,tools,model,budget,
                login_seconds=args.login_seconds,skill_snapshot=snapshot)
        else:
            await browser.start();await browser.navigate(browser.config.login_url)
        if getattr(args,'discover_authentication',False):
            stage='AUTHENTICATION_DISCOVERY'
            await reject_security_negatives(browser)
            settle_end=monotonic()+6
            while monotonic()<settle_end:
                await browser.guard.ensure_observable();await asyncio.sleep(.25)
            await reject_security_negatives(browser)
            monitor=getattr(browser,'discovery',None)
            result['authentication_discovery']=await monitor.prepare() if monitor else {'status':'DISCOVERY_UNAVAILABLE'}
            observation=await browser.observe()
            from modules.login.verifier import verify
            result['independent_authentication']={'verification':str(verify(observation)),
                'missing':(browser.verification_details or {}).get('missing',[])}
            print('AUTHENTICATION_DISCOVERY '+json.dumps(result['authentication_discovery']),flush=True)
        stage='LOGIN_CHECKPOINT'
        if autonomous:print('C ALSO authorizes unsaved Campaign name=agent_test, WABA=1730519403808933, Business number=Bajaj Markets (+91 8422973254), existing tag=test, and up to three scoped Doer attempts with bounded Teacher review. No Save/Next/Publish/Schedule/Send.',flush=True)
        if getattr(args,'automatic_handoff',False):
            from modules.login.verifier import verify
            from app.state import Result
            observed=await browser.observe()
            await reject_security_negatives(browser)
            if verify(observed)!=Result.AUTHENTICATED_AND_VERIFIED:raise BaselineStop('HUMAN_ACTION_REQUIRED',reason='INDEPENDENT_AUTHENTICATION_EVIDENCE_MISSING')
            browser.baseline_checkpoint=await login_context(browser)
            result.update(checkpoint='INDEPENDENTLY_VERIFIED',authentication='AUTHENTICATED_AND_VERIFIED')
            end=monotonic()+6
            while monotonic()<end:
                await browser.guard.ensure_observable();await asyncio.sleep(.25)
            settled_observation=await browser.observe()
            if verify(settled_observation)!=Result.AUTHENTICATED_AND_VERIFIED:raise BaselineStop('HUMAN_ACTION_REQUIRED',reason='AUTHENTICATION_EVIDENCE_NOT_STABLE')
            if await login_context(browser)!=browser.baseline_checkpoint:raise BaselineStop('LOGIN_CHECKPOINT_CONTEXT_CHANGED')
        else:
            result.update(await confirm_login(browser,result['login_doer'],automated=login_mode=='doer',seconds=args.login_seconds))
        stage='PROTECTED_SETUP_TAB'
        executor=await prepare_setup(browser)
        if autonomous:
            from modules.whatsapp.learning_loop import run_loop
            from deep_agents.models import openai_model
            from deep_agents.permissions import InferenceBudget
            from app.model_settings import ModelSettings
            settings=ModelSettings.load()
            if args.setup_api_budget+args.teacher_api_budget>settings.cycle_requests or args.teacher_api_budget>settings.teacher_requests:raise ConfigurationError('Setup/Teacher budgets exceed configured caps')
            setup_budget=InferenceBudget(args.setup_api_budget+args.teacher_api_budget,
                role_limits={'doer':args.setup_api_budget,'teacher':args.teacher_api_budget})
            setup_budget.max_input_tokens=min(settings.cycle_input,(args.setup_api_budget+args.teacher_api_budget)*settings.teacher_input)
            setup_budget.max_output_tokens=min(settings.cycle_output,args.setup_api_budget*settings.doer_output+args.teacher_api_budget*settings.teacher_output)
            print(f'AUTONOMOUS SETUP SHARED BUDGET: Doer<={args.setup_api_budget}, Teacher<={args.teacher_api_budget}, input<={setup_budget.max_input_tokens}, output<={setup_budget.max_output_tokens}; iterations<={args.iterations}.',flush=True)
            doer=openai_model('doer',settings,trace,setup_budget)
            teacher=openai_model('teacher',settings,trace,setup_budget)
            stage='AUTONOMOUS_SETUP'
            result['autonomous_setup']=await run_loop(executor,doer,teacher,setup_budget,
                ROOT/'outputs'/('setup-loop-'+trace.run_id),authorized=True,iterations=args.iterations)
            result['setup_tools_integration']='BROWSER_EXECUTOR_DEEP_AGENTS'
            result['teacher']='ANALYZED' if any('teacher' in r for r in result['autonomous_setup']['iterations']) else ('NOT_INVOKED' if not setup_budget.role_requests['teacher'] else 'ANALYSIS_UNAVAILABLE')
            result['four_field_real_netcore']='PASS' if result['autonomous_setup']['status']=='FOUR_FIELDS_VERIFIED_UNSAVED' else 'FAIL'
            result['real_netcore_campaign_name']='PASS' if 'campaign_name' in executor.verified else 'NOT_VERIFIED'
            return result
        stage='READ_ONLY_DISCOVERY'
        print('C ACCEPTED — automatic READ-ONLY field discovery; no campaign edits authorized.',flush=True)
        for attempt in range(4):
            discovery=await executor.discover()
            if discovery.get('setup_active') or attempt==3:break
            await asyncio.sleep(.25)
        result['discovery']=discovery
        print(json.dumps(discovery,indent=2),flush=True)
        async def demonstrate(field):
            choice=await key('SUPERVISED DEMONSTRATION field='+field+'. Open ONLY this dropdown once in Chromium. '+
                'Do not select, type, navigate or commit. R records redacted structure/options; Enter stops.',{'r'},90,browser)
            return choice=='r'
        executor.widget_prompt=demonstrate
        result['widget_discovery']=await executor.inspect_widgets()
        if getattr(args,'diagnose_only',False):
            if getattr(args,'demonstrate_widgets',False):
                choice=await key('READ-ONLY widget demonstration: W=WABA, B=Business number, T=tags. '+
                    'Choose ONE dropdown to open manually without selecting; Enter skips.',{'w','b','t'},60,browser)
                if choice:await executor.interactive.demonstrate({'w':'waba','b':'business_number','t':'tags'}[choice])
            result['setup_diagnostics']='READ_ONLY_COMPLETE_NO_FIELD_EDITS'
            print('BASELINE_DIAGNOSTIC_COMPLETE: no Campaign or remaining field edits authorized or attempted.',flush=True)
            return result
        campaign=unique_reference(discovery,'campaign_name')
        if not discovery['setup_active']:raise BaselineStop('DIRECT_SETUP_EVIDENCE_INCOMPLETE')
        stage='CAMPAIGN_AUTHORIZATION'
        approved=await key('Review the actual Setup heading/step and observed Campaign name input in Chromium. '+
            'Global/closed shadow contents are NOT certified. A authorizes ONLY Campaign name = agent_test in this current input; '+
            'Enter remains read-only and stops. No save, advance or send is authorized.',{'a'},60,browser)
        if approved!='a':raise BaselineStop('OPERATOR_CAMPAIGN_AUTHORIZATION_DECLINED')
        result['real_netcore_campaign_name']='FAIL'
        stage='CAMPAIGN_EDIT'
        await executor.fill_campaign(campaign,authorized=True)
        result['real_netcore_campaign_name']='PASS'
        stage='REMAINING_FIELDS_AUTHORIZATION'
        discovery=await executor.discover();result['discovery']=discovery
        print(json.dumps(discovery,indent=2),flush=True)
        follow=await key('Campaign name verified. Press F only to authorize the remaining three fixed fields in this SAME session '+
            '(approved WABA, business number and EXISTING test tag), including necessary non-committing dropdown opens. '+
            'Enter stops unsaved after Campaign name.',{'f'},60,browser)
        if follow=='f':
            result['four_field_real_netcore']='FAIL'
            stage='REMAINING_FIELD_EDITS'
            await executor.complete_remaining(authorized=True)
            result['four_field_real_netcore']='PASS'
        print('BASELINE_STOP_UNSAVED: '+result['real_netcore_campaign_name']+' Campaign; '+result['four_field_real_netcore']+' four-field baseline.',flush=True)
    except (KeyboardInterrupt,asyncio.CancelledError):
        result['stop']={'code':'OPERATOR_CANCELLED','operation':stage}
        print('BASELINE_STOP OPERATOR_CANCELLED',flush=True)
    except Exception as error:
        result['stop']=sanitized_action_error(error,executor.stage if executor and stage in {'CAMPAIGN_EDIT','REMAINING_FIELD_EDITS'} else stage)
        print('BASELINE_STOP '+json.dumps(result['stop']),flush=True)
    finally:
        # Report first, then bounded inspection. No action resumes after this point.
        if executor:
            executor.stopped=True
            result['verified_fields']=sorted(executor.verified)
            if getattr(executor,'interactive',None):result['interactive_widget_evidence']=executor.interactive.evidence
            if executor.last_discovery:
                result['discovery']={**executor.last_discovery,'field_diagnostics':executor.latest_field_diagnostics}
        browser.login_closed=True
        from learning.browser_runtime import usage
        result['provider_usage']=usage(trace)
        combined=result['provider_usage']
        loop=result.get('autonomous_setup',{})
        if loop:
            initial=loop['usage_initial']['doer'];final=loop['usage_final']['doer']
            setup={k:(final[k]-initial[k] if final[k] is not None and initial[k] is not None else None) for k in final}
        else:
            initial=combined['doer'];setup={k:(0 if k=='requests' else None) for k in initial}
        result['session_usage_roles']={'login_doer':initial,'setup_doer':setup,'teacher':combined['teacher']}
        result['usage_note']='Missing provider usage is null; cached and reasoning tokens are subsets, never added to totals.'
        result.update(automatic_login_submissions=browser.login_submissions,
            model_requests=(budget.requests if budget else 0)+(setup_budget.requests if autonomous and 'setup_budget' in locals() else 0),actual_api_requests=trace.openai_api_calls)
        if browser.guard and not browser.guard.failed and not browser.guard.blocking_hosts:
            try:
                await key('Read-only inspection hold. No action will resume. Enter closes; automatic owned cleanup at deadline.',set(),args.hold_seconds,browser)
            except (BaselineStop,KeyboardInterrupt,asyncio.CancelledError):pass
        try:await browser.close()
        except Exception:result['cleanup_warning']='OWNED_CLEANUP_NOT_CONFIRMED'
        result['cleanup_result']=trace.cleanup_result
        output=ROOT/'outputs'/('browser-baseline-'+trace.run_id+'-result.json')
        output.write_text(json.dumps(result,indent=2),encoding='utf-8')
        print(json.dumps(result,indent=2),flush=True)
    return result


def main():
    raise ConfigurationError('RETIRED_WHATSAPP_PATH: use app.cli whatsapp')
    parser=argparse.ArgumentParser(description='Supervised WhatsApp baseline with optional bounded Login/Setup Doer and Teacher; no commit')
    parser.add_argument('--authorized-live',action='store_true')
    parser.add_argument('--chromium',required=True)
    parser.add_argument('--login-seconds',type=int,default=180)
    parser.add_argument('--hold-seconds',type=int,default=120)
    parser.add_argument('--login-mode',choices=['manual','doer'],default='manual')
    parser.add_argument('--login-api-budget',type=int,default=0,help='Explicit login-only inference cap 1-6 for doer mode; zero for manual')
    parser.add_argument('--discover-authentication',action='store_true',help='Use existing passive post-login DOM/resource discovery; never approves evidence automatically')
    parser.add_argument('--auth-evidence-file',help='Private evidence draft path for the existing discovery/review workflow')
    parser.add_argument('--automatic-handoff',action='store_true',help='Require independent authentication evidence instead of human C; otherwise stop HUMAN_ACTION_REQUIRED')
    parser.add_argument('--autonomous-setup',action='store_true',help='C authorizes fixed unsaved fields; genuine Setup Doer/Teacher loop, no A/F prompts')
    parser.add_argument('--iterations',type=int,choices=[1,2,3,4,5],default=3)
    parser.add_argument('--setup-api-budget',type=int,default=12,help='Shared Setup Doer requests across all attempts, maximum 20')
    parser.add_argument('--teacher-api-budget',type=int,default=3,help='Shared Teacher requests including final review, maximum 5')
    parser.add_argument('--diagnose-only',action='store_true',help='After C automatically inspect fields; never request A/F or edit Setup fields')
    parser.add_argument('--demonstrate-widgets',action='store_true',help='With --diagnose-only, allow one supervised dropdown opening without selection')
    args=parser.parse_args()
    if args.demonstrate_widgets and not args.diagnose_only:parser.error('--demonstrate-widgets requires --diagnose-only')
    try:
        result=asyncio.run(run_baseline(args))
        if result.get('stop') or result['cleanup_result']!='CLOSED_AND_PROFILE_REMOVED':raise SystemExit(1)
    except ConfigurationError as error:
        print(str(error));raise SystemExit(1)
    except Exception as error:
        print(json.dumps(sanitized_action_error(error,'BASELINE_INITIALIZATION')));raise SystemExit(1)


if __name__=='__main__':main()
