"""Opt-in headed local handoff; genuine Deep Agents with a local model double."""
import os
import pytest
from evals.local_baseline_login_handoff import accept_baseline_login_handoff


@pytest.mark.skipif(os.getenv('RUN_REAL_BASELINE_LOGIN_HANDOFF')!='1',reason='Explicit local headed Login Doer handoff')
async def test_actual_single_browser_login_doer_handoff():
    report=await accept_baseline_login_handoff(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    assert report['passed'],report
    assert report['handoff']['authenticated_context_reused']
    assert report['handoff']['campaign_readback_verified']
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
