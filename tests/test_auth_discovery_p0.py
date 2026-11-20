"""Controlled offline diagnostics. No tenant, Chromium or provider execution."""
import json
from copy import deepcopy

from browser.discovery import identity_facts, resource_pair_supported
from browser.shadow_inventory import shadow_inventory
from modules.login.discovery import DOM_DISCOVERY
from test_evidence_discovery_repair import dom_expression, BASE, ACCOUNT
from test_inventory_batching import BatchReader


def test_composed_shell_controls_and_sanitized_rejection_reasons():
    document={'url':BASE+'/', 'children':[
        {'tag':'main','attrs':{'id':'app'},'children':[
            {'tag':'control-panel','attrs':{'id':'controls'},'shadow':True,
             'shadowChildren':[{'tag':'button','text':'PRIVATE_UI_TEXT'}]},
            {'tag':'span','text':ACCOUNT}]},
        {'tag':'nav','attrs':{'id':'navigation'},'children':[
            {'tag':'a','attrs':{'id':'link','href':'/actual'},'text':'PRIVATE_UI_TEXT'}]},
        {'tag':'h2','attrs':{'id':'hidden'},'hidden':True,'text':'PRIVATE_UI_TEXT'}]}
    result=dom_expression(DOM_DISCOVERY,document)
    assert any(c['selector']=='#app' for c in result['dashboard_candidates'])
    assert any(c['selector']=='#link' for c in result['navigation_candidates'])
    diagnostic=result['dom_diagnostics']
    assert diagnostic['cross_shadow_control_owners']>0
    assert diagnostic['rejections']['dashboard']['NOT_VISIBLE']>0
    assert 'PRIVATE_UI_TEXT' not in json.dumps(diagnostic)
    assert ACCOUNT not in json.dumps(diagnostic)


def test_custom_shell_requires_observed_content_navigation_and_controls():
    result=dom_expression(DOM_DISCOVERY,{'url':BASE+'/', 'children':[
        {'tag':'actual-app','attrs':{'id':'custom'},'children':[
            {'tag':'nav','children':[{'tag':'button','text':'observed'}]},
            {'tag':'article','children':[{'tag':'span','text':ACCOUNT}]}]}]})
    assert any(c['selector']=='#custom' and c['kind']=='APPLICATION_SHELL'
               for c in result['dashboard_candidates'])


def test_bounded_arrays_retain_exact_identity_without_promoting_status():
    facts=identity_facts({'data':[{'authenticated':True,'tenant':ACCOUNT}]},ACCOUNT)
    assert facts['auth_keys']==['/data/0/authenticated']
    assert facts['tenant_keys']==['/data/0/tenant']
    assert facts['structure']['arrays']==1
    assert not identity_facts({'data':[{'status':'active','tenant':ACCOUNT}]},ACCOUNT)['auth_keys']
    wrong=identity_facts([{'authenticated':True,'tenant':'OTHER'}],ACCOUNT)
    assert not wrong['tenant_keys']
    mixed=identity_facts({'data':[{'authenticated':True,'tenant':'OTHER'},{'tenant':ACCOUNT}]},ACCOUNT)
    assert not resource_pair_supported(mixed,mixed['auth_keys'][0],mixed['tenant_keys'][0])
    oversized=identity_facts({'data':[{}]*33},ACCOUNT)
    assert oversized['traversal_limit_reached'] and not oversized['auth_keys']


async def test_closed_root_ownership_persistence_and_safe_scope_classification():
    def document(role):
        return {'backendNodeId':1,'nodeType':9,'nodeName':'#document','childNodeCount':1,
                'children':[{'backendNodeId':2,'nodeType':1,'nodeName':'DIV',
                             'attributes':['role',role,'id','PRIVATE_CUSTOMER_VALUE'],
                             'childNodeCount':0,'shadowRoots':[
                                 {'backendNodeId':3,'nodeType':11,'shadowRootType':'closed',
                                  'childNodeCount':1,'children':[{'backendNodeId':4,'nodeType':3,
                                                                 'nodeValue':'SECRET_CONTENT'}]}]}]}
    for role,classification in [('main','RELEVANT'),('alert','UNRELATED_TO_POSITIVE_SCOPE'),
                                 ('presentation','UNKNOWN')]:
        reader=BatchReader(document(role))
        result=await shadow_inventory(reader,'owned',{BASE})
        root,=result['closed_root_ownership']
        assert root['scope_classification']==classification
        assert root['observed_passes']==2 and root['persistent']
        assert root['security_coverage']=='UNKNOWN_NO_EXEMPTION'
        assert root['document_ref']=='document-1'
        assert 'SECRET_CONTENT' not in json.dumps(result)
        assert 'PRIVATE_CUSTOMER_VALUE' not in json.dumps(result)
    first=document('main');second=deepcopy(first);second['children'][0]['backendNodeId']=22
    result=await shadow_inventory(BatchReader(first,second),'owned',{BASE})
    assert all(not item['persistent'] for item in result['closed_root_ownership'])
    result=await shadow_inventory(BatchReader(document('main')),'owned',{BASE},primary_frame='PRIVATE_FRAME_ID')
    root,=result['closed_root_ownership']
    assert root['frame_ref']=='frame-1' and 'PRIVATE_FRAME_ID' not in json.dumps(result)
