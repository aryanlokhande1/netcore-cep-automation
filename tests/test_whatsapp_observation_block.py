"""Saved live-run regression and local observation contracts; no live inference."""
import json
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import ROOT
from deep_agents.permissions import InferenceBudget
from modules.whatsapp.agent import create_setup_doer
from modules.whatsapp.contract import ORDER
from modules.whatsapp.safety import read_safety_snapshot, safety_diagnostic
from test_setup_observation import fixture, observe, combo_fixture
from test_whatsapp_phase1 import protocol_metadata_transport
from test_whatsapp_phase_b import bound_setup_tools, Page1Model

RUN='7d4523a9b7b84d44af1b6dedbe35aa3c'


def test_original_run_proves_read_then_observe_then_block_without_field_actions():
    result=json.loads((ROOT/'outputs'/f'{RUN}-result.json').read_text())
    events=json.loads((ROOT/'outputs'/f'{RUN}.json').read_text())
    requested=[e['tool_name'] for e in events if e.get('tool_phase')=='requested' and
        e.get('tool_name') in {'read_module_skill','observe_page','SetupReport'}]
    assert requested==['read_module_skill','observe_page','SetupReport']
    assert result['setup_agent_report']=={'status':'BLOCKED','reason':'unexpected_page'}
    assert result['setup_execution']['browser_operations']==[]
    assert result['setup_execution']['field_facts']=={}
    evidence=json.loads((ROOT/'outputs'/f'whatsapp-page1-{RUN}'/'failure-evidence.json').read_text())
    assert evidence['dom']['form_recognized'] and evidence['dom']['setup_active']
    assert not evidence['dom']['page_identity']
    assert not result['setup_observation']['form_recognized']
    assert all(not evidence['dom']['fields'][f]['ownership_verified'] for f in ORDER[1:])
    assert result['persistence']['non_get_requests_during_setup']=={'POST':49}


def test_current_scoped_form_signature_establishes_identity_without_heading_text():
    children=fixture();children[0]['text']='Create campaign'
    data=observe(children)
    assert not data['whatsapp_text_observed']
    assert data['page_identity'] and data['identity_basis']=='SCOPED_PAGE1_FORM_SIGNATURE'
    assert data['identity_diagnostics']['same_form_field_count']==4


def test_signature_cannot_certify_missing_ambiguous_or_wrong_step_form():
    for change in ('missing','ambiguous','wrong_step'):
        children=fixture();children[0]['text']='Create campaign'
        if change=='missing':children[2]['children']=children[2]['children'][:2]
        elif change=='ambiguous':children[2]['children'].append({'tag':'input','attrs':{'aria-label':'Campaign name'}})
        else:children[1]['text']='Audience'
        data=observe(children)
        assert not data['page_identity']


def test_structural_identity_does_not_grant_unsupported_field_ownership_or_wrong_route():
    from modules.whatsapp.destination import classify_setup_page, validate_setup_destination, DestinationRejected
    from modules.whatsapp.contract import SETUP_URL
    children=fixture();children[0]['text']='Create campaign'
    children[2]['children'][3]={'tag':'input','attrs':{'id':'waba','type':'text'}}
    data=observe(children)
    assert data['page_identity'] and not data['fields']['waba']['ownership_verified']
    assert data['fields']['waba']['kind']=='UNSUPPORTED'
    assert classify_setup_page(SETUP_URL,identity=data)['page_state']=='SETUP_CONFIRMED'
    with pytest.raises(DestinationRejected):validate_setup_destination(SETUP_URL+'/other',identity=data)


async def test_read_only_coverage_defers_inventory_without_certifying_safety():
    browser,tools=await bound_setup_tools()
    data=await read_safety_snapshot(browser,include_inventory=False)
    status=safety_diagnostic(data)
    assert status['negative_scope_complete'] is False
    assert status['coverage_status']=='DEFERRED_INVENTORY_AT_ACTION_BOUNDARY'
    assert status['scope_reason_counts']['inventory_requested'] is False


async def test_observation_names_raw_form_and_binding_separately_and_offers_read_only_next_steps():
    browser,tools=await bound_setup_tools()
    evaluate=browser.page.evaluate
    async def unresolved(source):
        data=await evaluate(source)
        if isinstance(data,dict) and 'fields' in data:data['page_identity']=False
        return data
    browser.page.evaluate=unresolved
    data=await tools.inspect()
    assert data['dom_form_recognized'] is True and data['action_form_identity_bound'] is False
    assert data['snapshot']['observer']=='observe_setup'
    assert data['permitted_read_only_operations']
    assert 'PAGE_IDENTITY_UNESTABLISHED' in data['pending_predicates']
    diagnostic=tools.blocked_diagnostic()
    assert diagnostic['reason']=='SETUP_PAGE_IDENTITY_UNESTABLISHED'


async def test_unresolved_field_ownership_is_not_attributed_to_model_reasoning():
    browser,tools=await bound_setup_tools()
    evaluate=browser.page.evaluate
    async def unowned(source):
        data=await evaluate(source)
        if isinstance(data,dict) and 'fields' in data:
            data['fields']['waba'].update(kind='UNSUPPORTED',ownership_verified=False)
            data['fields']['tags']['ownership_verified']=False
        return data
    browser.page.evaluate=unowned
    await tools.inspect()
    diagnostic=tools.blocked_diagnostic()
    assert diagnostic['failure_owner']=='unknown'
    assert 'FIELD_CONTROL_UNSUPPORTED' in diagnostic['remaining_field_predicates']['waba']
    assert 'FIELD_OWNERSHIP_UNESTABLISHED' in diagnostic['remaining_field_predicates']['tags']


async def test_native_gate_requires_one_read_only_probe_before_early_model_block():
    from langchain_core.messages import ToolMessage
    class EarlyBlock(Page1Model):
        def _generate(self,messages,**kwargs):
            self.calls+=1
            last=next((m for m in reversed(messages) if isinstance(m,ToolMessage)),None)
            if last is None:return self.reply('read_module_skill',{})
            if last.name=='read_module_skill':return self.reply('observe_page',{})
            if last.name=='observe_page' and 'SetupReport' not in self.bound_tool_names:
                target=json.loads(last.content)['fields'][0]
                return self.reply('inspect_control',{'reference':target['reference']})
            return self.reply('SetupReport',{'status':'BLOCKED','reason':'unexpected_page'})
    browser,tools=await bound_setup_tools()
    agent,_=create_setup_doer(EarlyBlock(),tools,browser.trace,InferenceBudget(6),offline=True,native_skill=True)
    await agent.ainvoke({'messages':[{'role':'user','content':'Observe Page 1.'}]},
        {'configurable':{'thread_id':browser.trace.run_id},'recursion_limit':40})
    assert any(e['tool']=='inspect_control' for e in tools.events)
    assert tools.browser_operations==[]


def test_known_safe_native_and_owned_popup_cases_do_not_certify_unowned_tags():
    native=observe(fixture())
    assert all(native['fields'][f]['ownership_verified'] for f in ORDER)
    owned=observe(combo_fixture())
    assert owned['fields']['tags']['ownership_verified']
    children=combo_fixture();children[2]['children'][7]['attrs'].pop('aria-controls')
    assert not observe(children)['fields']['tags']['ownership_verified']


def test_post_metadata_reports_temporal_context_without_claiming_causation():
    from modules.whatsapp.persistence import PersistenceObserver
    from test_whatsapp_phase1 import session
    observer=PersistenceObserver(session());observer.active=True
    observer.operation_source=SimpleNamespace(browser_operations=[])
    observer.event({'method':'Network.requestWillBeSent','sessionId':'PRIVATE','params':{
        'requestId':'PRIVATE','request':{'method':'POST','url':'https://PRIVATE','postData':'PRIVATE'}}})
    summary=observer.summary()
    assert summary['non_get_temporal_context']=={'NO_AGENT_FIELD_OPERATION_ATTEMPTED':1}
    assert summary['agent_causation']=='NOT_ESTABLISHED'
    assert summary['server_persistence']=='UNDETERMINED'
    assert 'PRIVATE' not in json.dumps(summary)


async def test_controller_preserves_specific_observation_predicate(monkeypatch):
    from test_whatsapp_phase1 import session
    from app.state import Observation
    from modules.whatsapp import workflow
    from modules.whatsapp.contract import Page1Limits, SetupReport
    from modules.whatsapp.session import Phase1Budget, PocGrant
    import modules.whatsapp.tools as tools_module
    browser=session();browser.observe=AsyncMock(return_value=Observation(expected_host=True))
    original=tools_module.observe_setup
    async def missing_identity(browser):
        data=await original(browser);data['page_identity']=False
        return data
    monkeypatch.setattr(tools_module,'observe_setup',missing_identity)
    class Agent:
        def __init__(self,tools):self.tools=tools
        async def ainvoke(self,*args):
            await self.tools.observe_page()
            return {'structured_response':SetupReport(status='BLOCKED',reason='unexpected_page')}
    monkeypatch.setattr(workflow,'create_setup_doer',lambda model,tools,*args,**kwargs:(Agent(tools),None))
    observer=SimpleNamespace(active=False,start=AsyncMock(),close=AsyncMock(),summary=lambda:{'server_persistence':'UNDETERMINED'})
    monkeypatch.setattr(workflow,'PersistenceObserver',lambda browser:observer)
    async def checkpoint(*args):return PocGrant(browser)
    result={'verification':'UNEXPECTED_PAGE','reason_code':'MISSING_EVIDENCE','error':None}
    await workflow.continue_setup(browser,SimpleNamespace(terminal_error=False),result,None,
        Phase1Budget(Page1Limits()),Page1Limits(),checkpoint=checkpoint)
    assert result['setup_stop_reason']=='SETUP_PAGE_IDENTITY_UNESTABLISHED'
    assert result['setup_failed_predicate']=='PAGE_IDENTITY_UNESTABLISHED'
    assert result['setup_execution']['controller_stop']['failed_predicate']=='PAGE_IDENTITY_UNESTABLISHED'
    assert result['setup_execution']['browser_operations']==[]


@pytest.mark.skipif(os.getenv('RUN_REAL_OBSERVATION_REPAIR')!='1',reason='Explicit actual local Chromium repair validation')
async def test_actual_chromium_headingless_page1_uses_structural_identity(monkeypatch,tmp_path):
    import evals.local_setup_agent_acceptance as fixture_module
    import test_whatsapp_phase_b as phase_b
    fixture_html=fixture_module.fixture_html
    monkeypatch.setattr(fixture_module,'fixture_html',lambda scenario:fixture_html(scenario).replace(
        '<h1>WhatsApp campaign</h1>','<h1>Create campaign</h1>'))
    # Reuse existing acceptance without overwriting prior phase evidence.
    monkeypatch.setattr(phase_b,'ROOT',tmp_path)
    (tmp_path/'outputs/phase-b-20261113').mkdir(parents=True)
    await phase_b.test_actual_chromium_native_skill_doer_and_failure_evidence(tmp_path)
    report=json.loads((tmp_path/'outputs/phase-b-20261113/local-native.json').read_text())
    assert report['four_fields_verified']
    assert report['evidence']['dom']['identity_basis']=='SCOPED_PAGE1_FORM_SIGNATURE'
    (ROOT/'outputs/phase-b-observation-repair-20261113/local-headingless.json').write_text(json.dumps(report,indent=2))


@pytest.mark.skipif(os.getenv('RUN_REAL_OBSERVATION_REPAIR')!='1',reason='Explicit actual local Chromium repair validation')
async def test_actual_chromium_existing_scope_safety_and_widget_cases(tmp_path):
    from evals.local_setup_agent_acceptance import local_setup_agent_acceptance
    report=await local_setup_agent_acceptance(os.environ['CHROMIUM_EXECUTABLE_PATH'],headless=True,
        output=tmp_path/'scope-and-widget-acceptance.json')
    assert report['passed'],report
    assert report['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'
    assert all(case['passed'] for case in report['cases'])
    (ROOT/'outputs/phase-b-observation-repair-20261113/local-scope-and-widgets.json').write_text(json.dumps(report,indent=2))
