"""Headed, loopback-only Pass 2 evidence. No real model or live account calls."""
import json
import os
from uuid import uuid4
import pytest
from app.config import Config,OriginPolicy,ROOT
from app.trace import Trace
from app.state import Result
from browser.stagehand_client import StagehandClient
from browser.live_dom import LIVE_DOM,VALIDATE_MAPPING
from browser.diagnostics import ScreenshotDiagnostics
from deep_agents.browser_tools import BrowserTools
from deep_agents.controller import run_synthetic
from deep_agents.offline import SyntheticChatModel
from evals.fixture import fixture_server
from learning.policies import Strategy
from learning.skill_storage.registry import SkillStore
from learning.skill_storage.generation import render_skill

pytestmark=pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS')!='1',reason='Requires actual headed local Chromium')

@pytest.fixture
def config():
    return Config(os.environ['CHROMIUM_EXECUTABLE_PATH'])

@pytest.fixture
async def harness(config):
    with fixture_server() as server:
        browser=StagehandClient(config,OriginPolicy({server.base}),Trace(),fixture=True)
        tools=BrowserTools(browser,server.base+'/start?scenario=valid')
        try:
            await browser.start()
            assert (await tools.navigate(tools.entry_url))['ok']
            yield tools
        finally:
            await browser.close()

async def test_live_dom_mapping_is_observed_revalidated_and_ambiguous_forms_rejected(harness):
    page=harness.browser.page
    await page.evaluate("document.querySelector('button').textContent='LOGIN'")
    await page.locator('#password').fill('SYNTHETIC-PRIVATE-MARKER')
    elements=await page.evaluate(LIVE_DOM.replace('PREFIX',json.dumps('unit-roles')))
    assert {e['key'] for e in elements}=={'account','username','password','submit'}
    assert 'SYNTHETIC-PRIVATE-MARKER' not in json.dumps(elements)
    mapping={e['key']:e for e in elements}
    expression=VALIDATE_MAPPING.replace('MAPPING',json.dumps(mapping))
    assert await page.evaluate(expression)
    await page.evaluate("document.querySelector('form').insertAdjacentHTML('beforeend','<input name=username>')")
    assert not await page.evaluate(expression)
    await page.evaluate("document.querySelector('form').setAttribute('action','https://evil.example/')")
    assert not await page.evaluate(expression)

async def test_privacy_gate_skips_private_form_and_captures_only_known_static_fixture(harness):
    browser=harness.browser
    await browser.page.locator('#password').fill('SYNTHETIC-PRIVATE-MARKER')
    assert (await ScreenshotDiagnostics(browser).capture(True))['reason']=='SCREENSHOT_SKIPPED_PRIVACY'
    state=await harness.inspect()
    reference=next(e['reference'] for e in state['elements'] if e['kind']=='submit')
    assert (await harness.interact(reference,'submit_fixture'))['ok']
    diagnostics=ScreenshotDiagnostics(browser)
    captured=await diagnostics.capture(True)
    assert captured['captured'] and (ROOT/'outputs'/captured['artifact']).is_file()
    assert not (await diagnostics.capture(True))['captured']
    await browser.page.evaluate("document.querySelector('#tenant').textContent='SYNTHETIC-PRIVATE-MARKER'")
    assert not (await ScreenshotDiagnostics(browser).capture(True))['captured']

@pytest.mark.parametrize('rule,fact',[
    ('reinspect_before_submit','REINSPECTED'),('wait_for_stable_controls','STABLE_CONTROLS'),
    ('verify_after_action','READ_ONLY_CHECK')])
async def test_general_strategy_rules_drive_actual_browser_checks(config,tmp_path,rule,fact):
    result=await run_synthetic(config,strategy=Strategy(rules=(rule,)),skill_store=SkillStore(tmp_path))
    assert result['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert fact in {f['kind'] for f in result['step_facts']}
    assert result['login_submissions']==1 and result['actual_api_requests']==0

async def test_teacher_failure_does_not_erase_verified_login(config,tmp_path):
    class BrokenTeacher(SyntheticChatModel):
        def _generate(self,*args,**kwargs):
            raise RuntimeError('Synthetic Teacher unavailable')
    result=await run_synthetic(config,review=True,teacher_model=BrokenTeacher(role='teacher'),skill_store=SkillStore(tmp_path))
    assert result['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert result['verification_snapshot'] and result['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert result['teacher_status']=='ANALYSIS_UNAVAILABLE'
    assert result['teacher_error']=='TEACHER_ANALYSIS_UNAVAILABLE'

async def test_cancelled_doer_cleans_up_without_teacher_inference(config,tmp_path):
    import asyncio
    entered=asyncio.Event()
    class CancelledDoer(SyntheticChatModel):
        async def _agenerate(self,*args,**kwargs):
            entered.set()
            await asyncio.Future()
    teacher=SyntheticChatModel(role='teacher')
    running=asyncio.create_task(run_synthetic(config,doer_model=CancelledDoer(),teacher_model=teacher,review=True,skill_store=SkillStore(tmp_path)))
    try:
        await asyncio.wait_for(entered.wait(),30)
        running.cancel()
        result=await asyncio.wait_for(running,30)
    finally:
        if not running.done():
            running.cancel()
            await asyncio.gather(running,return_exceptions=True)
    assert result['error']=='OPERATOR_CANCELLED' and teacher.calls==0
    assert result['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert not (ROOT/'profiles'/result['run_id']).exists()

async def test_autonomous_failed_attempt_improves_generates_and_loads_promoted_skill_fresh(config,tmp_path):
    from learning.autonomous import process_learning
    store=SkillStore(tmp_path/'skills')
    legacy=(ROOT/'modules/login/skill/SKILL.md').read_bytes()
    baseline=await run_synthetic(config,scenario='aria_variant',review=True,skill_store=store)
    assert baseline['verification']!=Result.AUTHENTICATED_AND_VERIFIED
    assert baseline['teacher_status']=='ANALYZED'
    assert baseline['teacher_proposal']['strategy_rule']=='prefer_semantic_roles'
    events=json.loads((ROOT/'outputs'/f"{baseline['run_id']}.json").read_text())
    learned=await process_learning(config,baseline,events,store=store,root=tmp_path/'learning')
    assert learned['decision']=='ACCEPTED_FOR_FIXTURES' and learned['evaluation_passed']
    artifact=learned['candidate_skill']
    directory=store.version_path('candidates',artifact['version'])
    evaluation=json.loads((directory/'evaluation.json').read_text())
    assert len(evaluation['comparisons'])==10 and all(r['passed'] for r in evaluation['comparisons'])
    assert evaluation['skill_loading_verified']
    assert all(r['skill_loaded'] and r['native_metadata_loaded'] for r in evaluation['skill_loading_checks'])
    provenance=json.loads((directory/'provenance.json').read_text())
    assert not provenance['live_verified'] and provenance['verified_run_ids']
    assert baseline['run_id'] in provenance['unverified_context_runs']
    assert store.snapshot() is None  # Learning never overwrites active guidance.
    store.promote(artifact['version'],artifact['digest'],approved=True)
    model=SyntheticChatModel()
    promoted=await run_synthetic(config,scenario='aria_variant',doer_model=model,skill_store=store)
    assert promoted['verification']==Result.AUTHENTICATED_AND_VERIFIED
    assert promoted['skill_loaded'] and promoted['native_skill_metadata_loaded']
    assert model.loaded_skill_digest==artifact['digest'] and model.loaded_skill_content==(directory/'SKILL.md').read_text()
    # A second tested version must appear on a fresh thread, then rollback must reload the first.
    rules=('prefer_semantic_roles','verify_after_action')
    strategy=Strategy(version=uuid4().hex,rules=rules)
    records=[{'run_id':r['run_id'],'step_facts':r['step_facts']} for r in evaluation['comparisons'] if r['verification']==Result.AUTHENTICATED_AND_VERIFIED]
    second=store.candidate(uuid4().hex,render_skill(strategy,records),evaluation,provenance)
    tested=await run_synthetic(config,scenario='aria_variant',skill_store=store,candidate_version=second['version'],_evaluation=True)
    assert tested['verification']==Result.AUTHENTICATED_AND_VERIFIED and tested['skill_loaded']
    store.promote(second['version'],second['digest'],approved=True)
    fresh=await run_synthetic(config,skill_store=store)
    assert fresh['skill_digest']==second['digest'] and fresh['native_skill_metadata_loaded']
    assert 'READ_ONLY_CHECK' in {f['kind'] for f in fresh['step_facts']}
    store.rollback(artifact['digest'],artifact['digest'],approved=True)
    rolled=await run_synthetic(config,scenario='aria_variant',skill_store=store)
    assert rolled['skill_digest']==artifact['digest'] and rolled['skill_loaded']
    assert len({promoted['run_id'],fresh['run_id'],rolled['run_id']})==3
    assert (ROOT/'modules/login/skill/SKILL.md').read_bytes()==legacy
    assert not (ROOT/'profiles'/rolled['run_id']).exists()
