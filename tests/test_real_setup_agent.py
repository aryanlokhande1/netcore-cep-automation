"""Opt-in actual headed Chromium seven-tool Setup acceptance; no inference."""
import os
import pytest
from evals.local_setup_agent_acceptance import local_setup_agent_acceptance


@pytest.mark.skipif(os.getenv('RUN_REAL_SETUP_AGENT_TEST')!='1',reason='Explicit actual local Chromium Setup-tool acceptance')
async def test_real_chromium_setup_read_only_authority_actions_and_prohibited_controls():
    report=await local_setup_agent_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False)
    assert report['passed'],report
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert len(report['cases'])==11 and all(c['passed'] for c in report['cases'])
    assert report['local_model_requests']==3 and report['runtime_sources_unchanged'] is True
    assert all(report['checks'].values())
