"""Opt-in real headed local widget compatibility; no tenant/model/credentials."""
import os
import pytest
from evals.local_remaining_fields import accept_remaining_fields


@pytest.mark.skipif(os.getenv('RUN_REAL_REMAINING_FIELDS')!='1',reason='Explicit local headed remaining-field acceptance')
async def test_actual_remaining_field_widget_compatibility():
    report=await accept_remaining_fields(os.environ['CHROMIUM_EXECUTABLE_PATH'])
    assert report['passed'],report
    assert len(report['cases'])==10
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
