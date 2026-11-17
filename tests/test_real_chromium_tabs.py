"""Opt-in actual Chromium acceptance, never mocked. Local about pages only."""
import os
import pytest
from evals.local_tabs import local_tab_acceptance

@pytest.mark.skipif(os.getenv('RUN_REAL_TAB_TEST')!='1',reason='Explicit actual local Chromium blank-tab acceptance')
async def test_real_chromium_same_context_protected_tab_and_owned_cleanup():
    report=await local_tab_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=False)
    assert report['passed'],{k:v for k,v in report.items() if k!='guard_lifecycle'}
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert all(report['checks'].values())
    assert [e['state'] for e in report['tab_stages']]==['TAB_CREATED','TARGET_ATTACHED','FETCH_PROTECTED','PAGE_BOUND']
