"""Opt-in actual Chromium realistic DOM complexity acceptance; no mocks/models."""
import os
import pytest
from evals.local_inventory_acceptance import local_inventory_acceptance


@pytest.mark.skipif(os.getenv('RUN_REAL_INVENTORY_TEST')!='1',reason='Explicit actual local Chromium realistic inventory acceptance')
async def test_real_chromium_complex_inventory_limits_coherence_and_owned_cleanup():
    report=await local_inventory_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False)
    assert report['passed'],report
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert len(report['cases'])==10 and all(c['passed'] for c in report['cases'])
    assert all(report['checks'].values())
