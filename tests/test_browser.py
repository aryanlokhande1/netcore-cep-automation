import os
import asyncio
from pathlib import Path
import pytest
from app.config import Config, OriginPolicy
from app.model import ReplayReasoner
from app.trace import Trace
from browser.stagehand_client import StagehandClient
from evals.fixture import fixture_server
from learning.runner import local_attempt, learn
from app.state import Result

pytestmark = pytest.mark.skipif(os.getenv('RUN_BROWSER_TESTS') != '1',reason='Set RUN_BROWSER_TESTS=1 for actual local Chromium integration')

@pytest.fixture
def config():
    return Config(os.environ['CHROMIUM_EXECUTABLE_PATH'])

@pytest.mark.parametrize('scenario,expected', [
    ('valid',Result.AUTHENTICATED_AND_VERIFIED),('invalid',Result.AUTH_FAILED),
    ('already',Result.AUTHENTICATED_AND_VERIFIED),('expired',Result.SESSION_EXPIRED),
    ('changed',Result.AUTHENTICATED_AND_VERIFIED),('challenge',Result.AUTHENTICATED_AND_VERIFIED),
    ('wrong_tenant',Result.UNEXPECTED_PAGE),('unknown',Result.UNEXPECTED_PAGE)])
async def test_actual_browser_states(config,scenario,expected):
    with fixture_server() as server:
        result,trace=await local_attempt(config,ReplayReasoner(),server,scenario,strategy=True)
        assert result==expected
        assert trace.actions > 0
        assert trace.actions <= config.max_actions
        if scenario=='invalid':
            assert sum(e['action_category']=='handoff' for e in trace.events)==2  # pause + resume; one form submission

async def test_teacher_fresh_browser_repeated_candidate(config,tmp_path):
    path=tmp_path/'strategy.md'
    records=await learn(config,ReplayReasoner(),strategy_path=path)
    assert records[-1]['accepted']
    assert records[0]['baseline']==Result.UNEXPECTED_PAGE
    assert len({c['run_id'] for c in records[0]['comparisons']})==9
    assert 'accepted for local testing' in path.read_text()

@pytest.mark.parametrize("mode", ["direct", "redirect", "popup"])
async def test_navigation_guard_blocks_redirect_and_new_page(config, mode):
    with fixture_server() as approved, fixture_server() as unapproved:
        browser=StagehandClient(config,OriginPolicy({approved.base}),Trace(),fixture=True)
        try:
            await browser.start()
            await browser.navigate(approved.base+'/start?scenario=valid')
            # Browser-initiated navigation is blocked before any request reaches the other server.
            approved.redirect_target = unapproved.base+'/start?scenario=already'
            try:
                if mode == 'redirect':
                    await browser.page.goto(approved.base+'/redirect-test')
                elif mode == 'popup':
                    await browser.browser.context.new_page(unapproved.base+'/start?scenario=already')
                else:
                    await browser.page.evaluate('location.href='+repr(unapproved.base+'/start?scenario=already'))
            except Exception:
                pass
            await asyncio.sleep(.2)
            assert browser.guard.blocked_hosts=={'127.0.0.1'}
            assert not unapproved.sessions
            assert not (await browser.observe()).allowed
        finally:
            await browser.close()
