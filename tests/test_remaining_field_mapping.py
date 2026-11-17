"""Focused eligibility, diagnostic-only and working-flow preservation checks."""
import ast
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import ROOT
from modules.whatsapp.browser_baseline import field_reference,BaselineStop


def test_preselected_owned_readonly_field_verifies_but_uncertain_or_ambiguous_field_does_not():
    waba={'reference':'current','field_hint':'waba','visible':True,'enabled':False,
        'owned':True,'matches_required_value':True}
    discovery={'controls':[waba],'field_diagnostics':{'waba':{'kinds':['READ_ONLY_SELECTED_VALUE']}}}
    assert field_reference(discovery,'waba')=='current'
    for change in ({'matches_required_value':False},{'owned':False}):
        with pytest.raises(BaselineStop,match='READ_ONLY_SELECTION_NOT_VERIFIED'):
            field_reference({**discovery,'controls':[{**waba,**change}]},'waba')
    with pytest.raises(BaselineStop,match='FIELD_CONTROL_AMBIGUOUS'):
        field_reference({**discovery,'controls':[waba,{**waba,'reference':'other'}]},'waba')
    with pytest.raises(BaselineStop,match='FIELD_CONTROL_UNRESOLVED'):
        field_reference({**discovery,'controls':[]},'waba')


async def test_diagnostic_mode_never_requests_edit_authority_or_calls_field_actions(monkeypatch,tmp_path):
    import app.whatsapp_baseline as app
    executable=tmp_path/'fixture.exe';executable.write_bytes(b'')
    (tmp_path/'outputs').mkdir();monkeypatch.setattr(app,'ROOT',tmp_path)
    monkeypatch.setattr(app.sys.stdin,'isatty',lambda:True)
    browser=SimpleNamespace(start=AsyncMock(),navigate=AsyncMock(),login_submissions=0,
        guard=SimpleNamespace(failed=False,blocking_hosts=set()))
    def factory(config,policy,trace,**kwargs):
        browser.config=config;browser.trace=trace
        return browser
    monkeypatch.setattr(app,'BaselineBrowser',factory)
    async def close():browser.trace.cleanup_result='CLOSED_AND_PROFILE_REMOVED'
    browser.close=AsyncMock(side_effect=close)
    monkeypatch.setattr(app,'confirm_login',AsyncMock(return_value={'checkpoint':'HUMAN_CONFIRMED_FOR_POC'}))
    diagnostic={'controls':[],'field_diagnostics':{'waba':{'failure_codes':['UNSUPPORTED_WIDGET']}}}
    executor=SimpleNamespace(discover=AsyncMock(return_value=diagnostic),last_discovery=diagnostic,
        inspect_widgets=AsyncMock(return_value={}),
        latest_field_diagnostics=diagnostic['field_diagnostics'],verified=set(),
        fill_campaign=AsyncMock(),complete_remaining=AsyncMock())
    monkeypatch.setattr(app,'prepare_setup',AsyncMock(return_value=executor))
    async def keys(prompt,allowed,seconds,browser):
        assert allowed in ({'r'},set())  # A/F must never be offered
        return 'r' if allowed else None
    monkeypatch.setattr(app,'key',keys)
    result=await app.run_baseline(SimpleNamespace(authorized_live=True,login_mode='manual',login_api_budget=0,
        chromium=str(executable),login_seconds=30,hold_seconds=1,diagnose_only=True))
    assert result['setup_diagnostics']=='READ_ONLY_COMPLETE_NO_FIELD_EDITS'
    executor.fill_campaign.assert_not_awaited();executor.complete_remaining.assert_not_awaited()
    assert browser.start.await_count==browser.close.await_count==1
    assert result['real_netcore_campaign_name']==result['four_field_real_netcore']=='NOT_RUN'


def test_working_campaign_login_core_and_saved_live_evidence_unchanged():
    pins=json.loads((ROOT/'outputs/remaining-fields-preservation.json').read_text())
    for name,sha in pins['files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha,name
    for file,names in [('modules/whatsapp/browser_baseline.py',{'fill_campaign'}),
        ('app/whatsapp_baseline.py',{'automated_login','prepare_automated_login','confirm_login','login_context','prepare_setup'})]:
        source=(ROOT/file).read_text();tree=ast.parse(source)
        for node in ast.walk(tree):
            if isinstance(node,ast.AsyncFunctionDef) and node.name in names:
                key=('app.' if file.startswith('app/') else '')+node.name
                assert hashlib.sha256(ast.get_source_segment(source,node).encode()).hexdigest()==pins['functions'][key],key
