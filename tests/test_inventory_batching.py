"""Bounded batching, coherent observations and saved real exhaustion evidence."""
from copy import deepcopy
import hashlib
import json
from unittest.mock import AsyncMock
import pytest
from app.config import ROOT
from browser.shadow_inventory import shadow_inventory
from modules.whatsapp.safety import NEGATIVES,read_safety_snapshot,safety_diagnostic
from test_whatsapp_phase1 import setup_tools,protocol_metadata_transport


def tree():
    return {'backendNodeId':1,'nodeType':9,'nodeName':'#document','childNodeCount':14,
        'children':[{'backendNodeId':2+i*3,'nodeType':1,'nodeName':'DIV','childNodeCount':0,
            'shadowRoots':[{'backendNodeId':3+i*3,'nodeType':11,'shadowRootType':'open','childNodeCount':1,
                'children':[{'backendNodeId':4+i*3,'nodeType':3,'nodeValue':'PRIVATE_FIXTURE_VALUE','childNodeCount':0}]}]}
            for i in range(14)]}


class BatchReader:
    def __init__(self,first=None,second=None):
        self.first=first or tree();self.second=second or deepcopy(self.first)
        self.calls=[];self.mutation_epoch=0;self.bytes_received=0;self.protocol_commands=1
    async def send(self,method,params,session):
        assert session=='owned' and params=={'depth':12,'pierce':True}
        assert method=='DOM.getDocument'
        self.calls.append(method);self.protocol_commands+=1
        return {'root':self.first if len(self.calls)==1 else self.second}


async def test_two_complete_batches_bracket_observation_and_keep_private_values_transient():
    reader=BatchReader()
    async def observe():assert reader.calls==['DOM.getDocument']
    result=await shadow_inventory(reader,'owned',{'http://local.example'},observe=observe)
    assert result['inventory_complete'] and result['inventory_passes']==2
    assert result['inventory_commands']==2 and result['inventory_transport_commands']==3
    assert result['inventory_open_shadow_roots']==14 and not result['inventory_changed']
    assert result['inventory_nodes_processed']==2*result['inventory_nodes_inspected']
    assert not any(k in json.dumps(result) for k in ['PRIVATE_FIXTURE_VALUE','backendNodeId','fingerprint','digest'])


@pytest.mark.parametrize('change',['identity','parent','attributes','text','epoch'])
async def test_same_root_count_does_not_authorize_incoherent_observation(change):
    reader=BatchReader()
    if change=='identity':reader.second['children'][0]['shadowRoots'][0]['backendNodeId']=999
    elif change=='parent':reader.second['children'][0],reader.second['children'][1]=reader.second['children'][1],reader.second['children'][0]
    elif change=='attributes':reader.second['children'][0]['attributes']=['aria-label','PRIVATE_CHANGED_VALUE']
    elif change=='text':reader.second['children'][0]['shadowRoots'][0]['children'][0]['nodeValue']='PRIVATE_CHANGED_VALUE'
    async def observe():
        if change=='epoch':reader.mutation_epoch+=1
    result=await shadow_inventory(reader,'owned',{'http://local.example'},observe=observe)
    assert result['inventory_open_shadow_roots']==14 and result['inventory_changed'] is True
    assert result['inventory_complete'] is True
    assert 'PRIVATE_CHANGED_VALUE' not in json.dumps(result)


async def test_partial_traversal_has_explicit_limit_reason_and_is_not_dom_change_evidence():
    reader=BatchReader()
    result=await shadow_inventory(reader,'owned',{'http://local.example'},max_commands=1)
    assert result['inventory_commands']==1 and result['inventory_passes']==1
    assert result['inventory_command_limit_reached'] and result['inventory_limit_reached']
    assert not result['inventory_complete'] and not result['inventory_changed']


async def test_shadow_root_budget_is_diagnosed_separately_from_node_budget():
    root={'backendNodeId':1,'nodeType':9,'childNodeCount':65,'children':[
        {'backendNodeId':2+i*2,'nodeType':1,'childNodeCount':0,'shadowRoots':[
            {'backendNodeId':3+i*2,'nodeType':11,'shadowRootType':'open','childNodeCount':0}]} for i in range(65)]}
    result=await shadow_inventory(BatchReader(root),'owned',{'http://local.example'})
    assert result['inventory_limit_reached'] and result['inventory_shadow_root_limit_reached']
    assert not result['inventory_node_limit_reached'] and not result['inventory_complete']


def snapshot():
    return {**{key:False for key in NEGATIVES},'negative_scope_complete':True,
        '_scope_diagnostics':{'open_shadow_roots_inspected':14,'shadow_roots_uninspected':0}}


async def test_partial_twelve_roots_are_not_compared_as_complete_against_fourteen(monkeypatch):
    import browser.shadow_inventory as module
    browser,_=setup_tools();browser.page.evaluate=AsyncMock(return_value=snapshot())
    async def partial(*args,**kwargs):
        return {'inventory_open_shadow_roots':12,'inventory_commands':512,'inventory_limit_reached':True,
            'inventory_command_limit_reached':True,'inventory_complete':False,'inventory_changed':False}
    monkeypatch.setattr(module,'shadow_inventory',partial)
    result=await read_safety_snapshot(browser);diagnostic=safety_diagnostic(result)
    assert diagnostic['code']=='SAFETY_OBSERVATION_INCOMPLETE'
    counts=diagnostic['scope_reason_counts']
    assert counts['inventory_incomplete'] and not counts['inventory_changed']
    assert not counts['inventory_root_counts_comparable'] and not counts['inventory_root_count_mismatch']
    assert browser.page.evaluate.await_count==1 and not browser.page.actions


async def test_complete_inventory_disagreement_is_a_separate_failed_predicate(monkeypatch):
    import browser.shadow_inventory as module
    browser,_=setup_tools();browser.page.evaluate=AsyncMock(return_value=snapshot())
    async def disagree(*args,**kwargs):
        await kwargs['observe']()
        return {'inventory_open_shadow_roots':12,'inventory_complete':True,'inventory_changed':False}
    monkeypatch.setattr(module,'shadow_inventory',disagree)
    result=await read_safety_snapshot(browser);counts=safety_diagnostic(result)['scope_reason_counts']
    assert counts['inventory_root_count_mismatch'] and counts['inventory_root_counts_comparable']
    assert not counts['inventory_changed'] and not result['negative_scope_complete']


async def test_safety_cannot_succeed_if_bracketed_observation_callback_is_skipped(monkeypatch):
    import browser.shadow_inventory as module
    browser,_=setup_tools();browser.page.evaluate=AsyncMock(return_value=snapshot())
    monkeypatch.setattr(module,'shadow_inventory',AsyncMock(return_value={
        'inventory_open_shadow_roots':14,'inventory_complete':True,'inventory_changed':False}))
    result=await read_safety_snapshot(browser)
    assert not result['negative_scope_complete'] and result['_scope_diagnostics']['inventory_incomplete']


def test_saved_real_run_exhausted_metadata_budget_without_proof_of_dom_mutation():
    run='cfe8d6bc2ce0467d984bae3bdc97e06d'
    events=json.loads((ROOT/'outputs'/f'{run}.json').read_text(encoding='utf-8'))
    result=json.loads((ROOT/'outputs'/f'{run}-result.json').read_text(encoding='utf-8'))
    start=[(i,e) for i,e in enumerate(events) if e.get('diagnostic',{}).get('state')=='COMMAND_STARTED'
        and e['diagnostic'].get('command') in {'DOM.getDocument','DOM.describeNode'}]
    done=[e for e in events if e.get('diagnostic',{}).get('state')=='COMMAND_COMPLETED'
        and e['diagnostic'].get('command') in {'DOM.getDocument','DOM.describeNode'}]
    assert len(start)==len(done)==512
    assert sum(e['diagnostic']['command']=='DOM.describeNode' for _,e in start)==511
    assert start[0][0]==220 and not any(e.get('diagnostic',{}).get('state')=='DOCUMENT_NAVIGATED' for e in events[220:1274])
    scope=events[1274]['diagnostic']['scope_reason_counts']
    assert scope['inventory_commands']==512 and scope['inventory_nodes_inspected']==1751
    assert scope['open_shadow_roots_inspected']==14 and scope['inventory_open_shadow_roots']==12
    assert scope['inventory_limit_reached'] and scope['inventory_changed']
    assert result['stage_requests']['setup']==0 and result['teacher_status']=='ANALYZED'
    assert result['login_submissions']==1 and result['cleanup_result']=='CLOSED_AND_PROFILE_REMOVED'


def test_login_handoff_contract_guard_teacher_and_saved_artifacts_are_unchanged():
    manifest=json.loads((ROOT/'outputs/inventory-batching-preservation.json').read_text(encoding='utf-8'))
    for name,sha in manifest['files'].items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
