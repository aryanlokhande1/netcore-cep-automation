"""Opt-in actual headed browser/controller acceptance; loopback and local models."""
import os
import pytest
from evals.local_setup_execution import accept_setup_execution


@pytest.mark.skipif(os.getenv('RUN_REAL_SETUP_EXECUTION') != '1', reason='Explicit local headed execution acceptance')
async def test_actual_chromium_controller_observation_and_authorized_fields():
    report = await accept_setup_execution(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    assert report['passed'], report
    assert len(report['cases']) == 8
    assert all(c['cleanup_result'] == 'CLOSED_AND_PROFILE_REMOVED' for c in report['cases'])
