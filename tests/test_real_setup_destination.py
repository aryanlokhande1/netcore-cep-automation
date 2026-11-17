"""Opt-in actual headed Chromium; only approved loopback documents/local model."""
import os
import pytest
from evals.local_setup_destination import accept_destination


@pytest.mark.skipif(os.getenv('RUN_REAL_SETUP_DESTINATION')!='1',reason='Explicit local headed destination acceptance')
async def test_actual_chromium_setup_fragment_reaches_readonly_doer():
    report=await accept_destination(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    assert report['passed'],report
    assert len(report['cases'])==6 and report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
