"""Opt-in actual local Chromium shadow coverage acceptance; never mocked."""
import os
import pytest
from evals.local_shadow_safety import local_shadow_acceptance


@pytest.mark.skipif(os.getenv('RUN_REAL_SHADOW_TEST')!='1',reason='Explicit actual local Chromium shadow fixture acceptance')
async def test_real_chromium_open_and_closed_shadow_safety_and_owned_cleanup():
    from modules.whatsapp.safety import read_safety_snapshot
    report=await local_shadow_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],
        snapshot_reader=read_safety_snapshot,headless=False)
    assert report['passed'],report
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert len(report['cases'])==9 and all(c['passed'] for c in report['cases'])
    assert all(report['checks'].values())
