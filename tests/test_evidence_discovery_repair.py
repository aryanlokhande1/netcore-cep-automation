"""Deterministic evidence preflight only: no browser, HTTP or model inference."""
import asyncio
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
from time import monotonic
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from pydantic import SecretStr
from app.config import Config, OriginPolicy, ROOT
from app.state import Observation, Result
from app.trace import Trace
from browser.discovery import ReadOnlyDiscovery
from browser.readonly_cdp import ReadOnlyCDP, ReadOnlyCommandError
from browser.stagehand_client import StagehandClient
from modules.login.evidence import EvidenceConfig, observe_live

ACCOUNT='TEST-ACCOUNT-DO-NOT-PERSIST'
BASE='http://127.0.0.1:9000'


def coherent_context(browser):
    """Controlled SDK/CDP pairing required by the current protected contract."""
    owner=SimpleNamespace(target='CONTROLLED_TARGET',session='guard-session',generation=1,
        document_generation=1,loader_id='loader',frame_id='frame')
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set(),ensure_observable=AsyncMock(),
        primary_observable=lambda:True,primary_session=owner.session,
        session_states={owner.session:owner},session_aliases={owner.session:1},target_aliases={owner.target:1})
    async def tree(*args,**kwargs):
        return {'frameTree':{'frame':{'id':owner.frame_id,'loaderId':owner.loader_id,'url':await browser.page.url()}}}
    browser.guard.send=tree;browser.page.page_id='CONTROLLED_PAGE'
    browser.browser=SimpleNamespace(context=SimpleNamespace(active_page=AsyncMock(return_value=browser.page)))


def setup_discovery(tmp_path):
    browser=StagehandClient(Config('unused',expected_host='127.0.0.1',evidence_file=str(tmp_path/'evidence.json')),
        OriginPolicy({BASE}),Trace(),fixture=True)
    owner=SimpleNamespace(loader_id='loader',frame_id='frame')
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set(),ensure_observable=AsyncMock(),
        primary_session='guard-session',session_states={'guard-session':owner})
    browser.expected_account=SecretStr(ACCOUNT)
    browser.page=SimpleNamespace(url=AsyncMock(return_value=BASE+'/'),evaluate=AsyncMock(return_value={
        'dashboard_candidates':[{'selector':'h1','frame_selectors':[],'kind':'CONTENT_HEADING','confidence':'OPERATOR_REVIEW_REQUIRED'}],
        'tenant_candidates':[{'selector':'#tenant','frame_selectors':[],'kind':'EXACT_EXPECTED_ACCOUNT_TEXT'}]}))
    coherent_context(browser)
    discovery=ReadOnlyDiscovery(browser);discovery.sample_interval=0
    browser.discovery=discovery
    discovery.channel=SimpleNamespace(ready=True,failure=None,active_sessions={'observer-session'},send=AsyncMock(
        return_value={'body':json.dumps({'authenticated':True,'tenant':ACCOUNT,'secret':'NEVER-PERSIST-RESPONSE'})}))
    return browser,discovery


def network(discovery,ident='request',path='/observed',loader='loader',frame='frame',finish=True,status=200,method='GET'):
    def emit(name,params):discovery.observe_network({'method':'Network.'+name,'sessionId':'observer-session','params':{'requestId':ident,**params}})
    emit('requestWillBeSent',{'loaderId':loader,'frameId':frame,'request':{'method':method,'url':BASE+path}})
    emit('responseReceived',{'response':{'status':status,'mimeType':'application/json','url':BASE+path}})
    if finish:emit('loadingFinished',{'encodedDataLength':100})


async def captured(discovery):
    await asyncio.gather(*tuple(discovery.tasks))


def config():
    return EvidenceConfig(dashboard_selector='h1',tenant_selector='#tenant',readonly_path='/observed',
        tenant_json_key='tenant',authenticated_json_key='authenticated')


async def test_capture_on_loading_finished_preserves_owning_session_and_never_saves_values(tmp_path):
    browser,d=setup_discovery(tmp_path)
    network(d,finish=False)
    d.channel.send.assert_not_awaited()
    d.observe_network({'method':'Network.loadingFinished','sessionId':'observer-session','params':{'requestId':'request'}})
    await captured(d)
    d.channel.send.assert_awaited_once_with('Network.getResponseBody',{'requestId':'request'},'observer-session')
    result=await d.prepare()
    assert result['unreviewed_draft_written'] and result['network_requests_replayed']==0
    assert d.check_resource(config())==(True,'PASSIVE_AUTHENTICATED_JSON_CONFIRMED')
    text=json.dumps(result)+json.dumps(browser.trace.events)+(tmp_path/'netcore-evidence-discovery.json').read_text()+(tmp_path/'evidence.json').read_text()
    assert all(marker not in text for marker in [ACCOUNT,'NEVER-PERSIST-RESPONSE','observer-session','"request"'])
    assert not json.loads((tmp_path/'evidence.json').read_text())['reviewed']


@pytest.mark.parametrize('change,reason',[
    ('loader','DIFFERENT_DOCUMENT'),('frame','DIFFERENT_DOCUMENT'),('expired','CAPTURE_EXPIRED'),
    ('detached','SESSION_UNAVAILABLE'),('account','ACCOUNT_CONTEXT_CHANGED'),('replacement','RESOURCE_REPLACED_OR_PENDING')])
async def test_passive_facts_cannot_verify_a_different_or_stale_context(tmp_path,change,reason):
    browser,d=setup_discovery(tmp_path);network(d);await captured(d)
    if change in {'loader','frame'}:setattr(browser.guard.session_states['guard-session'],change+'_id','changed')
    elif change=='expired':d.resources[-1]['captured']=monotonic()-121
    elif change=='detached':d.channel.active_sessions.clear()
    elif change=='account':browser.expected_account=SecretStr('OTHER-ACCOUNT')
    elif change=='replacement':network(d,ident='rejected-new',status=401)
    assert d.check_resource(config())==(False,reason)


@pytest.mark.parametrize('kind,expected',[
    ('evicted','BODY_EVICTED'),('missing','NO_RESOURCE_FOR_REQUEST'),('failed','LOADING_FAILED'),
    ('detached','SESSION_UNAVAILABLE'),('unfinished','WAITING_LOADING_FINISHED'),('post',None),
    ('query','CAPTURED'),('large','BODY_TOO_LARGE'),('noauth','CAPTURED'),('badjson','BODY_INVALID_JSON_OR_ENCODING')])
async def test_specific_unavailable_and_unsupported_resource_diagnostics(tmp_path,kind,expected):
    browser,d=setup_discovery(tmp_path)
    if kind in {'evicted','missing'}:
        message='Request SECRET_PROTOCOL_MARKER evicted from cache' if kind=='evicted' else 'No resource with given identifier found SECRET_PROTOCOL_MARKER'
        d.channel.send.side_effect=ReadOnlyCommandError({'code':-32000,'message':message})
    if kind=='badjson':d.channel.send.return_value={'body':'private invalid response'}
    if kind=='noauth':d.channel.send.return_value={'body':json.dumps({'tenant':ACCOUNT,'authenticated':False})}
    network(d,finish=kind not in {'failed','unfinished','detached','large'},method='POST' if kind=='post' else 'GET',path='/observed?private=yes' if kind=='query' else '/observed')
    if kind=='detached':
        d.channel.active_sessions.clear()
        d.observe_network({'method':'Network.loadingFinished','sessionId':'observer-session','params':{'requestId':'request'}})
    if kind=='failed':d.observe_network({'method':'Network.loadingFailed','sessionId':'observer-session','params':{'requestId':'request','errorText':'SECRET_PROTOCOL_MARKER'}})
    if kind=='large':d.observe_network({'method':'Network.loadingFinished','sessionId':'observer-session','params':{'requestId':'request','encodedDataLength':262145}})
    await captured(d)
    result=await d.prepare()
    assert not result['unreviewed_draft_written'] and result['status']=='PENDING_OPERATOR_OBSERVATION'
    if expected:assert result['resource_status_counts']=={expected:1}
    else:assert not d.resources
    assert 'SECRET_PROTOCOL_MARKER' not in json.dumps(browser.trace.events)+json.dumps(result)


async def test_same_path_uses_latest_response_and_sessions_never_interchange(tmp_path):
    browser,d=setup_discovery(tmp_path)
    network(d,ident='old',loader='old-loader');await captured(d)
    network(d,ident='new');await captured(d)
    result=await d.prepare()
    assert len(d.resources)==2 and result['supported_json_resources']==1
    d.observe_network({'method':'Network.loadingFinished','sessionId':'wrong-session','params':{'requestId':'new'}})
    assert d.channel.send.await_count==2
    assert [c.args[1]['requestId'] for c in d.channel.send.await_args_list]==['old','new']


async def test_delayed_dom_uses_last_snapshot_and_records_frame_scope(tmp_path):
    browser,d=setup_discovery(tmp_path);network(d);await captured(d)
    final={'dashboard_candidates':[{'selector':'#heading','frame_selectors':['#app-frame'],'kind':'CONTENT_HEADING'}],
        'tenant_candidates':[{'selector':'#tenant','frame_selectors':['#app-frame'],'kind':'EXACT_EXPECTED_ACCOUNT_TEXT'}], 'documents_inspected':2}
    browser.page.evaluate.side_effect=[{'dashboard_candidates':[],'tenant_candidates':[]},final,final]
    result=await d.prepare()
    assert browser.page.evaluate.await_count==3 and result['observation_samples']==3
    candidate=EvidenceConfig.load(tmp_path/'evidence.json',allow_unreviewed=True)
    assert candidate.dashboard_frame==candidate.tenant_frame==['#app-frame']
    private=json.loads((tmp_path/'netcore-evidence-discovery.json').read_text())
    assert private['observed_at'] and private['source_run_id']==browser.trace.run_id


async def test_inaccessible_permitted_frame_keeps_draft_pending(tmp_path):
    browser,d=setup_discovery(tmp_path);network(d);await captured(d)
    browser.page.evaluate.return_value['permitted_frames_unavailable']=1
    result=await d.prepare()
    assert not result['unreviewed_draft_written'] and 'PERMITTED_FRAMES_UNAVAILABLE' in result['gaps']


async def test_protocol_reader_forwards_loading_lifecycle_and_sanitizes_rejection():
    observed=[];monitor=ReadOnlyCDP(observed.append)
    future=asyncio.get_running_loop().create_future();monitor.pending[1]=future
    class Messages:
        close_code=1000
        def __aiter__(self):return self
        items=iter([{'method':'Network.loadingFinished','sessionId':'private','params':{'requestId':'private'}},
            {'id':1,'error':{'code':-32000,'message':'No resource with given identifier found SECRET'}}])
        async def __anext__(self):
            try:return json.dumps(next(self.items))
            except StopIteration:raise StopAsyncIteration
    monitor.ws=Messages();await monitor._read()
    assert observed[0]['method']=='Network.loadingFinished'
    error=future.exception()
    assert error.category=='NO_RESOURCE_FOR_REQUEST' and 'SECRET' not in str(error)


def live_review(tmp_path):
    from app.safety import authorize_live
    from browser.evidence_review import EvidenceReview
    cfg=Config('unused',live_login_enabled=True,evidence_file=str(tmp_path/'review.json'))
    permit=authorize_live(cfg,True,8);permit.started=True
    browser=StagehandClient(cfg,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.expected_account=SecretStr(ACCOUNT)
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set())
    async def evaluate(expression):
        if 'const alerts' in expression:return {'credential_form':False,'challenge':False,'rejected':False,'expired':False,'account_restricted':False}
        assert 'fetch(' not in expression
        return {'dashboard':True,'tenant_matches':True,'mismatch':False,'issues':[]}
    browser.page=SimpleNamespace(url=AsyncMock(return_value='https://'+cfg.expected_host+'/'),evaluate=evaluate)
    coherent_context(browser)
    browser.discovery=SimpleNamespace(check_resource=lambda e:(True,'PASSIVE_AUTHENTICATED_JSON_CONFIRMED'))
    browser.observe=lambda:observe_live(browser,'https://'+cfg.expected_host+'/')
    return browser,EvidenceReview(browser)


async def test_preview_missing_config_reports_host_and_negative_gates_without_network(tmp_path):
    browser,review=live_review(tmp_path)
    result=await review.preview()
    assert result['status']=='PENDING_OPERATOR_OBSERVATION' and result['gate_status']['expected_host']=='PASS'
    assert result['gate_status']['dashboard']=='NOT_CHECKED' and result['gate_status']['evidence_configured']=='FAIL'
    assert set(result['negative_states'].values())=={'PASS'} and review.preview_digest is None


async def test_passive_preview_and_approval_require_exact_unchanged_digest(tmp_path):
    browser,review=live_review(tmp_path);review.path.write_text(config().model_dump_json())
    preview=await review.preview()
    assert preview['status']=='READY_FOR_EXPLICIT_APPROVAL' and preview['gate_status']['evidence_configured']=='FAIL'
    assert preview['verification_basis']=='PROSPECTIVE_APPROVAL_CHECK' and not preview['trusted']
    observed=await review.approve(preview['digest'],True)
    assert observed.readonly_access and browser.login_submissions==0
    assert json.loads(review.path.read_text())['reviewed'] and browser.trace.verification_snapshot


async def test_passive_preview_rejects_unobserved_resource_and_negative_state(tmp_path):
    browser,review=live_review(tmp_path);review.path.write_text(config().model_dump_json())
    browser.discovery.check_resource=lambda e:(False,'READONLY_RESOURCE_NOT_OBSERVED')
    preview=await review.preview()
    assert preview['gate_status']['readonly_access']=='FAIL' and 'READONLY_RESOURCE_NOT_OBSERVED' in preview['issues']
    with pytest.raises(PermissionError):await review.approve(preview['digest'],True)
    assert not json.loads(review.path.read_text())['reviewed']


def test_preserved_login_teacher_ownership_and_graduation_hashes():
    manifest=json.loads((ROOT/'outputs/evidence-discovery-preservation.json').read_text())
    for name,digest in manifest.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==digest


def dom_expression(script,document,**settings):
    node=shutil.which('node')
    if node is None:pytest.skip('Node required for the deterministic DOM expression check')
    settings={'origins':[BASE],'account':ACCOUNT,**settings}
    result=subprocess.run([node,str(Path(__file__).with_name('evidence_dom_double.js'))],
        input=json.dumps({'document':document,'expression':script.replace('SETTINGS',json.dumps(settings))}),
        text=True,capture_output=True,timeout=5,check=True)
    return json.loads(result.stdout)


def content_document():
    return {'url':BASE+'/', 'children':[
        {'tag':'nav','attrs':{'id':'nav'},'children':[{'tag':'h2','attrs':{'id':'nav-heading'},'text':'Campaigns'},
            {'tag':'a','attrs':{'id':'nav-active','aria-current':'page'},'text':'Campaigns'}]},
        {'tag':'main','attrs':{'id':'content'},'children':[
            {'tag':'div','attrs':{'id':'actual-heading','role':'heading'},'text':'Journeys'},
            {'tag':'span','attrs':{'id':'tenant'},'text':ACCOUNT,'children':[{'tag':'svg'}]}]}]}


def test_dom_discovers_content_role_heading_and_icon_tenant_without_campaigns_guess():
    from modules.login.discovery import DOM_DISCOVERY
    result=dom_expression(DOM_DISCOVERY,content_document())
    assert [c['selector'] for c in result['dashboard_candidates']]==['#actual-heading']
    assert result['dashboard_candidates'][0]['kind']=='CONTENT_HEADING'
    assert len(result['navigation_candidates'])==2
    assert [c['selector'] for c in result['tenant_candidates']]==['#tenant']
    assert ACCOUNT not in json.dumps(result) and 'Journeys' not in json.dumps(result)


def test_dom_permitted_frame_scope_is_reusable_by_positive_verifier():
    from modules.login.discovery import DOM_DISCOVERY, POSITIVE_DOM
    document={'url':BASE+'/', 'children':[{'tag':'iframe','attrs':{'id':'app','src':'/frame'},'document':content_document()}]}
    result=dom_expression(DOM_DISCOVERY,document)
    assert result['documents_inspected']==2 and result['dashboard_candidates'][0]['frame_selectors']==['#app']
    positive=dom_expression(POSITIVE_DOM,document,dashboard_selector='#actual-heading',tenant_selector='#tenant',dashboard_frame=['#app'],tenant_frame=['#app'])
    assert positive['dashboard'] and positive['tenant_matches'] and not positive['issues']
    wrong=dom_expression(POSITIVE_DOM,document,dashboard_selector='#actual-heading',tenant_selector='#tenant',dashboard_frame=[],tenant_frame=[])
    assert not wrong['dashboard'] and not wrong['tenant_matches']


def test_dom_inaccessible_permitted_and_external_frames_are_separate():
    from modules.login.discovery import DOM_DISCOVERY, POSITIVE_DOM
    document=content_document()
    document['children'] += [
        {'tag':'iframe','attrs':{'id':'permitted','src':'https://permitted.example/'}},
        {'tag':'iframe','attrs':{'id':'external','src':'https://external.example/'}}]
    settings={'origins':[BASE,'https://permitted.example']}
    result=dom_expression(DOM_DISCOVERY,document,**settings)
    assert result['permitted_frames_unavailable']==result['external_frames_skipped']==1
    positive=dom_expression(POSITIVE_DOM,document,dashboard_selector='#actual-heading',tenant_selector='#tenant',**settings)
    assert 'PERMITTED_DOCUMENT_UNOBSERVABLE' in positive['issues']


def test_dom_retains_negative_authentication_indicators_inside_permitted_frame():
    from modules.login.discovery import NEGATIVE_STATES
    document={'url':BASE+'/', 'children':[{'tag':'iframe','attrs':{'id':'app','src':'/frame'},'document':{
        'url':BASE+'/frame', 'children':[{'tag':'input','attrs':{'type':'password'}},
        {'tag':'input','attrs':{'autocomplete':'one-time-code'}},
        {'attrs':{'role':'alert'},'text':'Session expired. Invalid credentials. Account locked.'}]}}]}
    flags=dom_expression(NEGATIVE_STATES,document)
    assert all(flags.values())


def test_dom_private_account_id_is_not_persisted_in_selector():
    from modules.login.discovery import DOM_DISCOVERY
    document=content_document();document['children'][1]['children'][1]['attrs']['id']=ACCOUNT
    result=dom_expression(DOM_DISCOVERY,document)
    assert result['tenant_candidates'] and ACCOUNT not in json.dumps(result)


@pytest.mark.parametrize('negative',['credential_form','challenge','rejected','expired','account_restricted','login_entry'])
async def test_negative_state_blocks_approval_even_with_all_positive_gates(tmp_path,negative):
    browser,review=live_review(tmp_path);review.path.write_text(config().model_dump_json())
    observation=Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True,**{negative:True})
    browser.observe=AsyncMock(return_value=observation)
    preview=await review.preview()
    assert preview['negative_states'][negative]=='FAIL'
    with pytest.raises(PermissionError):await review.approve(preview['digest'],True)
    assert not json.loads(review.path.read_text())['reviewed'] and browser.login_submissions==0


async def test_capture_queue_is_bounded_and_cleanup_cancels_pending_reads(tmp_path):
    browser,d=setup_discovery(tmp_path)
    async def stalled(*args):await asyncio.Future()
    d.channel.send=stalled;d.channel.close=AsyncMock()
    for index in range(9):network(d,ident=str(index),path='/observed-'+str(index))
    assert len(d.tasks)==8 and d.resources[-1]['state']=='CAPTURE_LIMIT_REACHED'
    await asyncio.sleep(0)
    await d.close()
    assert not d.tasks and not d.resources and not browser.guard.failed
    d.channel.close.assert_awaited_once()


async def test_dom_samples_consume_three_actions_and_guard_failure_blocks_discovery(tmp_path):
    browser,d=setup_discovery(tmp_path)
    await d.prepare()
    assert browser.trace.actions==3
    browser.guard.failed=True
    with pytest.raises(ValueError):await d.prepare()


async def test_ambiguous_navigation_and_display_name_never_make_draft(tmp_path):
    browser,d=setup_discovery(tmp_path);network(d);await captured(d)
    browser.page.evaluate.return_value={'dashboard_candidates':[], 'navigation_candidates':[
        {'selector':'nav','frame_selectors':[],'kind':'NAVIGATION_ONLY'}], 'tenant_candidates':[]}
    result=await d.prepare()
    assert result['candidate_type_counts']['navigation']=={'NAVIGATION_ONLY':1}
    assert not result['unreviewed_draft_written'] and result['tenant_candidates']==0


async def test_missing_config_does_not_claim_absent_negatives_in_inaccessible_document(tmp_path):
    browser,review=live_review(tmp_path)
    browser.page.evaluate=AsyncMock(return_value={'credential_form':False,'challenge':False,'rejected':False,
        'expired':False,'account_restricted':False,'negative_scope_complete':False})
    result=await review.preview()
    assert set(result['negative_states'].values())=={'NOT_CHECKED'}
    assert result['status']=='PENDING_OPERATOR_OBSERVATION'


async def test_out_of_order_responses_keep_newest_request_ownership(tmp_path):
    browser,d=setup_discovery(tmp_path)
    for ident in ['older','newer']:
        d.observe_network({'method':'Network.requestWillBeSent','sessionId':'observer-session','params':{
            'requestId':ident,'loaderId':'loader','frameId':'frame','request':{'url':BASE+'/observed','method':'GET'}}})
    for ident in ['newer','older']:
        d.observe_network({'method':'Network.responseReceived','sessionId':'observer-session','params':{
            'requestId':ident,'response':{'status':200,'mimeType':'application/json'}}})
        d.observe_network({'method':'Network.loadingFinished','sessionId':'observer-session','params':{'requestId':ident}})
    await captured(d)
    assert d.check_resource(config())[0]
    result=await d.prepare()
    assert result['supported_json_resources']==1


def test_review_diagnostics_preserve_canonical_gate_types_without_changing_preview():
    from browser.evidence_diagnostics import review_event
    preview={'status':'READY_FOR_EXPLICIT_APPROVAL','gates':{'expected_host':True,'dashboard':False},
        'gate_status':{'expected_host':'PASS','dashboard':'FAIL'}}
    result=review_event(SimpleNamespace(trace=None),'PREVIEW',preview)
    assert result['gates']=={'expected_host':'PASS','dashboard':'FAIL'}
    assert preview['gates']['expected_host'] is True
