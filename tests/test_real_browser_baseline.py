"""Opt-in actual headed local Chromium proof; no live Netcore/model/credentials."""
import os
import pytest
from evals.local_browser_baseline import accept_browser_baseline


@pytest.mark.skipif(os.getenv('RUN_REAL_BROWSER_BASELINE')!='1',reason='Explicit actual local headed browser baseline')
async def test_actual_local_browser_baseline():
    report=await accept_browser_baseline(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    assert report['passed'],report
    assert len(report['cases'])==3
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
