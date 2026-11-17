import pytest
from browser.discovery import ReadOnlyDiscovery, identity_facts
from modules.login.evidence import EvidenceConfig
from browser.evidence_diagnostics import EvidenceFailure
from test_evidence_discovery_repair import setup_discovery, network, captured, config, ACCOUNT

def test_nested_facts_and_no_truthy_false_positives():
    facts=identity_facts({'session':{'authenticated':True,'tenant':ACCOUNT}},ACCOUNT)
    assert facts['auth_keys']==['/session/authenticated']
    assert facts['tenant_keys']==['/session/tenant']
    assert not identity_facts({'authenticated':'true','tenant':ACCOUNT.lower()},ACCOUNT)['auth_keys']

def test_missing_config_is_fixed_sanitized_code(tmp_path):
    with pytest.raises(EvidenceFailure,match='^MISSING_EVIDENCE_CONFIGURATION$'):
        EvidenceConfig.load(tmp_path/'absent.json')
    assert EvidenceConfig.for_session(tmp_path/'absent.json',True) is None

async def test_query_resource_is_observed_but_not_persisted_or_auto_selected(tmp_path):
    b,d=setup_discovery(tmp_path);network(d,path='/observed?private=value');await captured(d)
    assert len(d.resources)==1
    result=await d.prepare()
    assert not result['unreviewed_draft_written']
    assert 'private=value' not in (tmp_path/'netcore-evidence-discovery.json').read_text()
    assert not d.eligible('https://unapproved.invalid/a?q=1')
    assert not d.eligible('http://user:pass@127.0.0.1:9000/a')

def test_open_shadow_evidence_and_unknown_host_gap():
    from test_evidence_discovery_repair import dom_expression, BASE
    from modules.login.discovery import DOM_DISCOVERY, POSITIVE_DOM
    document={'url':BASE+'/', 'children':[{'tag':'main','children':[{'tag':'x-panel','attrs':{'id':'host'},'shadow':True,'shadowChildren':[
        {'tag':'h1','attrs':{'id':'heading'},'text':'Workspace'},
        {'tag':'span','attrs':{'id':'tenant'},'text':ACCOUNT}]}]}]}
    d=dom_expression(DOM_DISCOVERY,document)
    assert d['open_shadow_roots_inspected']==1 and d['tenant_candidates']
    assert d['tenant_candidates'][0]['shadow_selectors']==['#host']
    settings=dict(dashboard_selector='#heading',tenant_selector='#tenant',dashboard_shadow=['#host'],tenant_shadow=['#host'])
    positive=dom_expression(POSITIVE_DOM,document,**settings)
    assert positive['dashboard'] and positive['tenant_matches']
    document['children'].append({'tag':'x-unknown'})
    d=dom_expression(DOM_DISCOVERY,document)
    assert d['inaccessible_or_unknown_hosts']==1
    # A custom-element name is uncertainty, not proof of a closed root. The
    # protected certificate must resolve it before independent authentication.
    assert 'CUSTOM_HOST_SCOPE_REQUIRES_PROTECTED_INVENTORY' in dom_expression(POSITIVE_DOM,document,**settings)['issues']

def test_exact_reviewed_mapping_and_context_binding():
    import hashlib
    from modules.login.evidence import reviewed_account
    c=config()
    raw=c.model_dump();raw['identity_mapping']={'reviewed':True,'login_sha256':hashlib.sha256(ACCOUNT.encode()).hexdigest(),'application_identity':'APP-TENANT','evidence_reference':'operator-reviewed-local-record'}
    c=EvidenceConfig.model_validate(raw)
    assert reviewed_account(c,ACCOUNT)=='APP-TENANT'
    with pytest.raises(EvidenceFailure,match='IDENTITY_MAPPING_CONTEXT_MISMATCH'):reviewed_account(c,'other')
    assert EvidenceConfig.model_validate_json(c.model_dump_json()).identity_mapping.application_identity.get_secret_value()=='APP-TENANT'

async def test_nested_resource_verifies_only_matching_current_keys(tmp_path):
    import json
    b,d=setup_discovery(tmp_path)
    d.channel.send.return_value={'body':json.dumps({'session':{'authenticated':True,'tenant':ACCOUNT}})}
    network(d);await captured(d)
    c=config();c.authenticated_json_key='/session/authenticated';c.tenant_json_key='/session/tenant'
    assert d.check_resource(c)[0]
    c.tenant_json_key='/session/other'
    assert not d.check_resource(c)[0]

def test_nested_bounds_fail_closed():
    value={'authenticated':True,'tenant':ACCOUNT}
    for _ in range(12):value={'nested':value}
    assert identity_facts(value,ACCOUNT)['traversal_limit_reached']
    assert identity_facts(value,ACCOUNT)['auth_keys']==[]
