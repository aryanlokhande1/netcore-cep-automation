"""Offline saved-evidence and genuine harness regression; no browser/provider."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.state import Observation
from deep_agents.live_tools import LiveBrowserTools
from test_whatsapp_handoff_repair import LoginToolsDouble, EarlyReportModel
from test_whatsapp_phase1 import session
from deep_agents.doer import create_doer
from deep_agents.schemas import DEFAULT_TASK
from deep_agents.permissions import InferenceBudget

class StandaloneTools(LiveBrowserTools):
    _inspect = LoginToolsDouble._inspect
    _resolve = LoginToolsDouble._resolve

async def test_standalone_doer_submits_normal_form_once_without_postlogin_evidence():
    browser=session();browser.login_submissions=0;browser.trace.step_facts=[]
    browser.page.location=browser.config.login_url
    async def observe():
        return Observation(login_entry=not browser.login_submissions,credential_form=not browser.login_submissions,expected_host=bool(browser.login_submissions))
    browser.observe=observe
    async def submit(mapping):
        browser.claim_login_submission();browser.trace.fact('SUBMITTED_ONCE')
    tools=StandaloneTools(browser,SimpleNamespace(submit=submit));model=EarlyReportModel()
    agent,_=create_doer(model,tools,browser.trace,InferenceBudget(8),offline=True)
    await agent.ainvoke({'messages':[{'role':'user','content':DEFAULT_TASK}]},{'configurable':{'thread_id':browser.trace.run_id},'recursion_limit':40})
    assert browser.login_submissions==1 and not tools.submission_pending()
    assert 'submit_login' in model.choices

@pytest.mark.parametrize('stop',['challenge','expired','rejected','account_restricted','wrong_origin','guard_failed','guard_blocked','submitted','missing_role'])
def test_standalone_completion_gate_preserves_stops(stop):
    browser=session();browser.login_submissions=int(stop=='submitted')
    tools=LiveBrowserTools(browser,None);tools.navigation_count=1
    tools.references={k:{'key':k} for k in ['account','username','password','submit']}
    observation=Observation(login_entry=True,credential_form=True)
    if stop in ['challenge','expired','rejected','account_restricted']:setattr(observation,stop,True)
    if stop=='wrong_origin':observation.allowed=False
    if stop=='guard_failed':browser.guard.failed=True
    if stop=='guard_blocked':browser.guard=SimpleNamespace(failed=False,primary_observable=lambda:True,blocking_hosts={'unapproved.invalid'})
    if stop=='missing_role':tools.references.pop('password')
    browser.trace.current_observation=observation.model_dump()
    assert not tools.submission_pending()

async def test_cli_login_only_disables_review_learning_and_teacher_budget(monkeypatch):
    from app import cli
    from app.config import Config
    runner=AsyncMock(return_value={})
    monkeypatch.setattr(cli,'run_live',runner)
    monkeypatch.setattr(cli.Config,'load',lambda:Config('unused'))
    args=cli.build_parser().parse_args(['live','--authorized-live','--api-request-budget','6','--login-only'])
    await cli.run(args)
    assert runner.call_args.kwargs['review'] is False
    assert runner.call_args.kwargs['learning'] is False
    assert runner.call_args.kwargs['teacher_request_budget']==0

async def test_login_only_controller_never_constructs_teacher_or_generates_candidates(monkeypatch,tmp_path):
    from deep_agents import controller as c
    from app.config import Config
    from modules.login.credentials import LocalCredentials, CredentialBroker
    from learning import autonomous
    from learning.skill_storage.registry import SkillStore
    from app.model_settings import ModelSettings
    model=SimpleNamespace(root_async_client=SimpleNamespace(models=SimpleNamespace(retrieve=AsyncMock())),bind_meter=lambda *a:None)
    roles=[]
    def build(role,*a):roles.append(role);return model
    monkeypatch.setattr(c,'openai_model',build)
    monkeypatch.setattr(c,'ROOT',tmp_path);(tmp_path/'outputs').mkdir()
    monkeypatch.setattr(c,'authorize_live',lambda *a:None,raising=False)
    import app.safety
    monkeypatch.setattr(app.safety,'authorize_live',lambda *a:SimpleNamespace(origins=[]))
    monkeypatch.setattr(LocalCredentials,'load',lambda:None)
    monkeypatch.setattr(CredentialBroker,'__init__',lambda *a:None)
    monkeypatch.setattr(SkillStore,'snapshot',lambda *a:None)
    monkeypatch.setattr(ModelSettings,'load',lambda:SimpleNamespace(teacher_requests=3,cycle_input=10000,cycle_output=1000))
    monkeypatch.setattr(c,'StagehandClient',lambda *a,**k:SimpleNamespace(config=a[0],guard=None))
    monkeypatch.setattr(c,'_execute_doer',AsyncMock(return_value={'verification':'HUMAN_ACTION_REQUIRED','reason_code':'LOGIN_NAVIGATION_INCOMPLETE','error':None}))
    monkeypatch.setattr(c,'_finish_result',lambda *a:None)
    review=AsyncMock();learn=AsyncMock()
    monkeypatch.setattr(c,'review_completed',review)
    monkeypatch.setattr(autonomous,'process_learning',learn)
    r=await c.run_live(Config('unused'),authorized=True,api_request_budget=6,teacher_request_budget=0,review=False,learning=False)
    assert roles==['doer'] and r['teacher_status']=='DISABLED_LOGIN_ONLY'
    review.assert_not_called();learn.assert_not_called()

def test_saved_failure_is_normal_prelogin_not_a_denied_submission():
    import json
    from app.config import ROOT
    from modules.login.verifier import verify
    from app.state import Result
    events=json.loads((ROOT/'outputs/b77f29eac26f48098f2570c676bfebd4.json').read_text())
    for i in [64,68,72,75]:
        observation=Observation.model_validate(events[i]['observation'])
        assert observation.allowed and observation.login_entry and observation.credential_form
        assert not observation.expected_host and not observation.challenge
        assert verify(observation)==Result.HUMAN_ACTION_REQUIRED
    assert not any(e.get('tool_name')=='submit_login' for e in events)
    assert events[70]['tool_name']=='DoerReport'

@pytest.mark.parametrize('learning,teacher',[(True,0),(False,1)])
async def test_disabled_review_rejects_accidental_learning_or_teacher_reservation(monkeypatch,learning,teacher):
    from deep_agents import controller
    from app.config import Config, ConfigurationError
    import app.safety
    monkeypatch.setattr(app.safety,'authorize_live',lambda *a:None)
    with pytest.raises(ConfigurationError,match='Disabled Teacher review'):
        await controller.run_live(Config('unused'),authorized=True,api_request_budget=6,review=False,learning=learning,teacher_request_budget=teacher)


@pytest.mark.parametrize('login_only,evidence_review,expected_cap', [
    (True, True, 120), (True, False, 30), (False, True, 30),
])
async def test_cli_scopes_startup_action_allowance_to_login_only_evidence_review(
        monkeypatch, login_only, evidence_review, expected_cap):
    from app import cli
    from app.config import Config

    runner = AsyncMock(return_value={})
    monkeypatch.setattr(cli, 'run_live', runner)
    monkeypatch.setattr(cli.Config, 'load', lambda: Config('unused'))
    argv = ['live', '--authorized-live', '--api-request-budget', '6', '--hold-seconds', '300']
    if login_only:
        argv.append('--login-only')
    if evidence_review:
        argv += ['--review-evidence', '--evidence-review-file', 'private-destination.json']
    await cli.run(cli.build_parser().parse_args(argv))

    config = runner.call_args.args[0]
    assert config.max_actions == expected_cap
    assert config.max_login_submissions == 1
    assert runner.call_args.args[5] == 300
    assert runner.call_args.kwargs['evidence_review'] is evidence_review


@pytest.mark.parametrize('stage', ['FINAL_READ_ONLY_OBSERVATION', 'DISCOVERY'])
def test_review_persists_typed_action_budget_stop(stage, monkeypatch):
    from app.trace import Trace
    from browser.evidence_diagnostics import evidence_diagnostic, review_event
    from browser.stagehand_client import BrowserBudgetExceeded

    trace = Trace()
    monkeypatch.setattr(trace, 'write', lambda _root: None)
    browser = SimpleNamespace(trace=trace, guard=None)
    error = BrowserBudgetExceeded('ACTION_LIMIT', actions_used=120, actions_limit=120)
    diagnostic = evidence_diagnostic(error, stage, browser)
    saved = review_event(browser, stage, diagnostic)

    assert saved['code'] == 'BROWSER_ACTION_BUDGET_EXHAUSTED'
    assert (saved['budget_kind'], saved['actions_used'], saved['actions_limit']) == ('ACTION_LIMIT', 120, 120)
    assert trace.events[-1]['diagnostic'] == saved
    assert evidence_diagnostic(TimeoutError(), stage)['code'] == 'BROWSER_OBSERVATION_UNAVAILABLE'
