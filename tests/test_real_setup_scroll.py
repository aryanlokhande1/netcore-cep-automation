"""Opt-in focused actual Chromium final scroll/guard/timeout edge acceptance."""
import os
import pytest
from evals.local_setup_agent_acceptance import local_setup_scroll_acceptance


@pytest.mark.skipif(os.getenv('RUN_REAL_SETUP_SCROLL_TEST')!='1',reason='Explicit actual local Chromium final Setup-edge acceptance')
async def test_real_chromium_setup_scroll_hidden_option_preexisting_guard_and_timeout():
    report=await local_setup_scroll_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False)
    assert report['passed'],report
    assert len(report['cases'])==4 and all(c['passed'] for c in report['cases'])
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED' and report['runtime_sources_unchanged'] is True
