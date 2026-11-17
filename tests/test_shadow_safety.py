"""Execute the real JS with DOM doubles and test bounded CDP/protected composition."""
import hashlib
import json
from unittest.mock import AsyncMock
import pytest
from app.config import ROOT
from browser.shadow_inventory import shadow_inventory
from modules.login.discovery import NEGATIVE_STATES
from modules.whatsapp.safety import SAFETY_SNAPSHOT,NEGATIVES,read_safety_snapshot,safety_diagnostic
from test_evidence_discovery_repair import dom_expression,BASE
from test_whatsapp_phase1 import setup_tools,protocol_metadata_transport


def negative_node(kind):
    if kind=='credential_form':return {'tag':'input','attrs':{'type':'password'}}
    if kind=='challenge':return {'tag':'input','attrs':{'autocomplete':'one-time-code'}}
    return {'attrs':{'role':'alert'},'text':{'rejected':'Invalid credentials','expired':'Session expired','account_restricted':'Account locked'}[kind]}


def host(children):return {'shadow':True,'shadowChildren':children}


@pytest.mark.parametrize('kind',NEGATIVES)
def test_shared_predicates_find_each_negative_in_nested_open_roots(kind):
    document={'url':BASE+'/','children':[host([host([negative_node(kind)])])]}
    snapshot=dom_expression(SAFETY_SNAPSHOT,document)
    assert snapshot[kind] is True and snapshot['negative_scope_complete'] is True
    assert snapshot['_scope_diagnostics']['open_shadow_roots_inspected']==2
    flags=dom_expression(NEGATIVE_STATES,document)
    assert set(flags)==set(NEGATIVES)|{'negative_scope_complete'} and all(type(v) is bool for v in flags.values())
    assert safety_diagnostic(snapshot)['code']=='AUTHENTICATION_OR_SECURITY_STOP'


def test_fourteen_accessible_open_roots_are_inspected_not_ignored():
    document={'url':BASE+'/','children':[host([{'text':'PRIVATE_FIXTURE_MARKER'}]) for _ in range(14)]}
    snapshot=dom_expression(SAFETY_SNAPSHOT,document)
    counts=snapshot['_scope_diagnostics']
    assert snapshot['negative_scope_complete'] and not any(snapshot[k] for k in NEGATIVES)
    assert counts['open_shadow_roots_inspected']==14 and counts['shadow_roots_uninspected']==0
    assert 'PRIVATE_FIXTURE_MARKER' not in json.dumps(snapshot)


@pytest.mark.parametrize('case',['nodes','roots','shadow_depth','tree_depth','documents','frame_depth','text'])
def test_dom_limits_remain_bounded_and_incomplete(case):
    children=[];expected={}
    if case=='nodes':children=[{'tag':'span'} for _ in range(12001)];expected={'node_limit_reached':True,'nodes_inspected':12000}
    elif case=='roots':children=[host([]) for _ in range(65)];expected={'shadow_root_limit_reached':True,'open_shadow_roots_inspected':64}
    elif case=='shadow_depth':
        nested=negative_node('credential_form')
        for _ in range(9):nested=host([nested])
        children=[nested];expected={'shadow_depth_limit_reached':True,'open_shadow_roots_inspected':8}
    elif case=='tree_depth':
        nested={'tag':'span'}
        for _ in range(130):nested={'children':[nested]}
        children=[nested];expected={'tree_depth_limit_reached':True}
    elif case=='documents':
        children=[{'tag':'iframe','attrs':{'src':'/frame'},'document':{'url':BASE+'/frame','children':[]}} for _ in range(8)]
        expected={'document_limit_reached':True,'documents_inspected':8}
    elif case=='frame_depth':
        nested={'url':BASE+'/frame','children':[]}
        for _ in range(4):nested={'url':BASE+'/frame','children':[{'tag':'iframe','attrs':{'src':'/frame'},'document':nested}]}
        children=nested['children'];expected={'permitted_frames_unavailable':1}
    else:children=[{'attrs':{'role':'alert'},'text':'x'*65537}];expected={'text_limit_reached':True}
    snapshot=dom_expression(SAFETY_SNAPSHOT,{'url':BASE+'/','children':children})
    assert snapshot['negative_scope_complete'] is False
    for key,value in expected.items():assert snapshot['_scope_diagnostics'][key]==value


def test_shadow_negative_inside_permitted_frame_and_unavailable_frame_gap():
    document={'url':BASE+'/','children':[{'tag':'iframe','attrs':{'src':'/frame'},'document':{
        'url':BASE+'/frame','children':[host([negative_node('expired')])]}},
        {'tag':'iframe','attrs':{'src':'/unavailable'}}]}
    result=dom_expression(SAFETY_SNAPSHOT,document)
    assert result['expired'] and not result['negative_scope_complete']
    assert safety_diagnostic(result)['code']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert result['_scope_diagnostics']['permitted_frames_unavailable']==1


def protocol_node(key,**values):return {'backendNodeId':key,'nodeType':1,'childNodeCount':0,**values}


class MetadataGuard:
    def __init__(self,root):self.root=root;self.calls=[]
    async def send(self,method,params,session):
        self.calls.append((method,params))
        assert session=='protected' and params['depth']==12 and params['pierce'] is True
        if method=='DOM.getDocument':return {'root':self.root}
        raise AssertionError('Unexpected unbounded expansion')


@pytest.mark.parametrize('kind,counter',[('open','inventory_open_shadow_roots'),('closed','inaccessible_shadow_roots'),('user-agent','user_agent_shadow_roots'),('unknown','unknown_shadow_roots'),(None,'unknown_shadow_roots')])
async def test_inventory_distinguishes_open_author_closed_native_and_unknown_roots(kind,counter):
    root=protocol_node(1,nodeType=9,children=[protocol_node(2,shadowRoots=[protocol_node(3,shadowRootType=kind)])])
    result=await shadow_inventory(MetadataGuard(root),'protected',{BASE})
    assert result[counter]==1 and result['inventory_commands']==2
    assert not result['inventory_unavailable']


@pytest.mark.parametrize('limit',['nodes','commands','deadline','documents'])
async def test_inventory_limits_never_return_complete_proof(limit):
    root=protocol_node(1,nodeType=9)
    guard=MetadataGuard(root);options={}
    if limit=='nodes':root['children']=[protocol_node(2),protocol_node(3)];options={'max_nodes':2}
    elif limit=='commands':root['childNodeCount']=1;options={'max_commands':1}
    elif limit=='documents':root['children']=[protocol_node(i,nodeType=9) for i in range(2,10)]
    else:
        import asyncio
        guard.send=AsyncMock(side_effect=lambda *a,**k:None)
        async def stalled(*args):await asyncio.Future()
        guard.send=stalled;options={'seconds':.01}
    result=await shadow_inventory(guard,'protected',{BASE},**options)
    assert result['inventory_limit_reached']
    assert result['inventory_nodes_inspected']<=options.get('max_nodes',12000)
    assert result['inventory_commands']<=options.get('max_commands',512)
    assert result['inventory_documents_inspected']<=8


def plain_snapshot(**updates):
    return {**{k:False for k in NEGATIVES},'negative_scope_complete':True,
        '_scope_diagnostics':{'open_shadow_roots_inspected':0,'shadow_roots_uninspected':0},**updates}


@pytest.mark.parametrize('failure',['closed','unknown','unavailable','mismatch','generation','guard','malformed'])
async def test_reader_fails_closed_and_preserves_first_negative(monkeypatch,failure):
    import browser.shadow_inventory as module
    browser,_=setup_tools()
    second=None if failure=='malformed' else plain_snapshot()
    browser.page.evaluate=AsyncMock(side_effect=[plain_snapshot(challenge=True),second])
    async def inventory(*args,**kwargs):
        counts={'inventory_open_shadow_roots':0,'inaccessible_shadow_roots':0,'inventory_complete':True}
        if failure=='closed':counts['inaccessible_shadow_roots']=1
        if failure=='unknown':counts['unknown_shadow_roots']=1
        if failure=='unavailable':counts['inventory_unavailable']=True
        if failure=='mismatch':counts['inventory_open_shadow_roots']=1
        if failure=='generation':browser.guard.session_states[browser.guard.primary_session].document_generation+=1
        if failure=='guard':browser.guard.failed=True
        await kwargs['observe']()
        return counts
    monkeypatch.setattr(module,'shadow_inventory',inventory)
    result=await read_safety_snapshot(browser)
    assert result['challenge'] is True and result['negative_scope_complete'] is False
    assert safety_diagnostic(result)['code']=='AUTHENTICATION_OR_SECURITY_STOP'
    assert browser.page.evaluate.await_count==(1 if failure in {'generation','guard'} else 2)
    assert not browser.page.actions and browser.login_submissions==1


async def test_inventory_transport_error_is_a_coverage_gap_without_private_error_text():
    guard=MetadataGuard(protocol_node(1,nodeType=9))
    guard.send=AsyncMock(side_effect=RuntimeError('PRIVATE_FIXTURE_MARKER'))
    result=await shadow_inventory(guard,'protected',{BASE})
    assert result['inventory_unavailable'] and 'PRIVATE_FIXTURE_MARKER' not in json.dumps(result)


async def test_reader_does_not_read_unprotected_document():
    browser,_=setup_tools();browser.guard.session_states[browser.guard.primary_session].protected=False
    browser.guard.ensure_observable=AsyncMock();browser.page.evaluate=AsyncMock()
    result=await read_safety_snapshot(browser)
    assert safety_diagnostic(result)['code']=='SAFETY_OBSERVATION_INVALID'
    browser.page.evaluate.assert_not_awaited()


def test_saved_run_proves_accessible_shadow_coverage_gap_and_teacher_uncertainty():
    run='bd3fd9cdf01d4c8e80b5a64c08ee3dd1'
    events=json.loads((ROOT/'outputs'/f'{run}.json').read_text(encoding='utf-8'))
    result=json.loads((ROOT/'outputs'/f'{run}-result.json').read_text(encoding='utf-8'))
    diagnostic=events[221]['diagnostic']
    assert diagnostic['code']=='SAFETY_OBSERVATION_INCOMPLETE' and not any(diagnostic['negative_flags'].values())
    assert diagnostic['scope_reason_counts']['shadow_roots_uninspected']==14
    assert result['stage_requests']['setup']==0 and not result['navigation_guard_status']['failed']
    assert result['login_submissions']==1 and result['failure_inspection']['status']=='DEADLINE_REACHED'
    assert result['teacher_diagnostic']['category']=='STRUCTURED_OUTPUT_VALIDATION'


def test_security_teacher_contract_saved_sources_and_positive_indicators_are_preserved():
    import modules.login.discovery as shared
    manifest=json.loads((ROOT/'outputs/shadow-safety-preservation.json').read_text(encoding='utf-8'))
    for path,sha in manifest['files'].items():assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==sha
    for name,sha in manifest['shared_positive_constants'].items():assert hashlib.sha256(getattr(shared,name).encode()).hexdigest()==sha
