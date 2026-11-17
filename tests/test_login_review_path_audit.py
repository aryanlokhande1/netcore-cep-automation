"""Controlled offline audit cases. No Chromium, tenant requests or inference."""
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.state import Result
from browser.discovery import ReadOnlyDiscovery
from modules.login.evidence import EvidenceConfig, stable_authentication
from modules.login.verifier import verify
from test_evidence_discovery_repair import ACCOUNT, BASE, captured, dom_expression, live_review


def coherent(browser):
    owner=SimpleNamespace(target='CONTROLLED_TARGET',session='CONTROLLED_SESSION',
        generation=1,document_generation=1,frame_id='CONTROLLED_FRAME',loader_id='CONTROLLED_LOADER')
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set(),primary_observable=lambda:True,
        ensure_observable=AsyncMock(),primary_session=owner.session,
        session_states={owner.session:owner},session_aliases={owner.session:1},target_aliases={owner.target:1})
    async def tree(*args,**kwargs):
        return {'frameTree':{'frame':{'id':owner.frame_id,'loaderId':owner.loader_id,'url':await browser.page.url()}}}
    browser.guard.send=tree;browser.page.page_id='CONTROLLED_PAGE'
    browser.browser=SimpleNamespace(context=SimpleNamespace(active_page=AsyncMock(return_value=browser.page)))


async def observed_scalar(tmp_path):
    b,review=live_review(tmp_path);coherent(b)
    d=ReadOnlyDiscovery(b);b.discovery=d;d.sample_interval=0
    d.channel=SimpleNamespace(ready=True,failure=None,active_sessions={'OBSERVER'},send=AsyncMock(
        return_value={'body':json.dumps({'session':{'status':'active','tenant':ACCOUNT}})}))
    async def evaluate(expression):
        if 'const dashboard_candidates=' in expression:
            return {'dashboard_candidates':[{'selector':'#content','frame_selectors':[],
                'shadow_selectors':[],'kind':'APPLICATION_SHELL'}],
                'tenant_candidates':[{'selector':'#tenant','frame_selectors':[],
                'shadow_selectors':[],'kind':'EXACT_EXPECTED_ACCOUNT_TEXT'}]}
        if 'const alerts' in expression:
            return {k:False for k in ['credential_form','challenge','rejected','expired','account_restricted']}
        assert 'fetch(' not in expression
        return {'dashboard':True,'tenant_matches':True,'mismatch':False,'issues':[]}
    b.page.evaluate=evaluate
    url='https://'+b.config.expected_host+'/controlled-observed'
    def emit(method,params):d.observe_network({'method':'Network.'+method,'sessionId':'OBSERVER',
        'params':{'requestId':'CONTROLLED_REQUEST',**params}})
    emit('requestWillBeSent',{'loaderId':'CONTROLLED_LOADER','frameId':'CONTROLLED_FRAME',
        'request':{'method':'GET','url':url}})
    emit('responseReceived',{'response':{'status':200,'mimeType':'application/json','url':url}})
    emit('loadingFinished',{'encodedDataLength':100});await captured(d)
    return b,d,review


def test_partial_scan_reports_unknown_not_proven_application_absence():
    from modules.login.discovery import READINESS_DOM
    nodes=[{'tag':'div'} for _ in range(600)]+[{'tag':'main','children':[{'tag':'button','text':'Menu'}]}]
    result=dom_expression(READINESS_DOM,{'url':BASE+'/', 'children':nodes})
    assert result['scan_limited'] is True
    assert result['shell'] is None


async def test_partial_scan_timeout_records_incomplete_coverage():
    from test_login_robustness import browser
    b=browser();b.readiness_sample=AsyncMock(return_value={
        'context':'CONTROLLED','expected_host':True,'shell':None,'scan_limited':True})
    result=await b.wait_for_application_ready()
    assert result['code']=='POST_LOGIN_READINESS_SCOPE_INCOMPLETE'
    assert result['shell_status']=='UNKNOWN'


async def test_observed_nonboolean_candidate_has_complete_explicit_review_path(tmp_path):
    b,d,review=await observed_scalar(tmp_path)
    discovery=await d.prepare()
    assert not discovery['unreviewed_draft_written']
    options=await review.resource_options()
    assert options['options'][0]['observed_value']=='active'
    selected=await review.select_resource_contract(1,options['digest'],confirmed=True)
    assert selected['trusted'] is False
    c=EvidenceConfig.load(review.path,allow_unreviewed=True)
    assert not c.reviewed and c.authenticated_value_sha256==hashlib.sha256(b'"active"').hexdigest()
    assert not verify(await b.observe())==Result.AUTHENTICATED_AND_VERIFIED
    preview=await review.preview()
    assert preview['status']=='READY_FOR_EXPLICIT_APPROVAL'
    assert not preview['trusted'] and preview['gate_status']['evidence_configured']=='FAIL'
    await review.approve(preview['digest'],approved=True)
    assert verify(await stable_authentication(b))==Result.AUTHENTICATED_AND_VERIFIED
    assert b.login_submissions==0
    assert not any(e.get('diagnostic',{}).get('network_requests_replayed') for e in b.trace.events)


@pytest.mark.parametrize('change',['document','resource','no_consent','digest'])
async def test_scalar_selection_rejects_stale_or_unapproved_options(tmp_path,change):
    b,d,review=await observed_scalar(tmp_path);await d.prepare();options=await review.resource_options()
    if change=='document':b.guard.session_states[b.guard.primary_session].document_generation+=1
    if change=='resource':d.latest_requests[d.resources[0]['path']]+=1
    with pytest.raises(PermissionError):
        await review.select_resource_contract(1,'0'*64 if change=='digest' else options['digest'],confirmed=change!='no_consent')
    assert not review.path.exists()


async def test_review_options_cannot_select_an_explicit_negative_authentication_resource(tmp_path):
    b,d,review=await observed_scalar(tmp_path)
    # Contradictory current response: a generic status must not override the
    # resource's explicit negative authentication field.
    from browser.discovery import identity_facts
    d.resources[0]['facts']=identity_facts({'authenticated':False,
        'session':{'status':'active','tenant':ACCOUNT}},ACCOUNT)
    await d.prepare()
    options=await review.resource_options()
    assert not options['options']


@pytest.mark.parametrize('status',['inactive','unauthenticated','expired','denied','error','failed','logged_out'])
async def test_negative_status_is_not_an_authentication_contract_option(tmp_path,status):
    b,d,review=await observed_scalar(tmp_path)
    from browser.discovery import identity_facts
    d.resources[0]['facts']=identity_facts({'session':{'status':status,'tenant':ACCOUNT}},ACCOUNT)
    await d.prepare()
    assert not (await review.resource_options())['options']


@pytest.mark.parametrize('closed',[False,True])
async def test_real_scope_verifier_breaks_candidate_cycle_but_keeps_closed_root_blocked(tmp_path,monkeypatch,closed):
    from contextlib import asynccontextmanager
    from copy import deepcopy
    import browser.metadata_channel as channel
    from test_inventory_batching import BatchReader,tree
    b,d,review=await observed_scalar(tmp_path)
    first=tree();second=deepcopy(first)
    second['children'][0]['attributes']=['data-animation','changed']
    if closed:
        root={'backendNodeId':1000,'nodeType':1,'nodeName':'X-UNRESOLVED','childNodeCount':0,
            'shadowRoots':[{'backendNodeId':1001,'nodeType':11,'shadowRootType':'closed','childNodeCount':0}]}
        for value in (first,second):value['children'].append(deepcopy(root));value['childNodeCount']+=1
    class Reader(BatchReader):
        async def send(self,method,params,session):
            assert session==b.guard.primary_session
            return await super().send(method,params,'owned')
    @asynccontextmanager
    async def metadata(*args):yield Reader(first=first,second=second)
    monkeypatch.setattr(channel,'metadata_channel',metadata)
    prior=b.page.evaluate
    async def evaluate(expression):
        if 'const alerts' in expression:
            flags={k:False for k in ['credential_form','challenge','rejected','expired','account_restricted']}
            flags['negative_scope_complete']=True
            return flags if expression.rstrip().endswith('.flags') else {'flags':flags}
        result=await prior(expression)
        scope={'inaccessible_or_unknown_hosts':1,'open_shadow_roots_inspected':14}
        if 'const dashboard_candidates=' in expression:return {**result,**scope}
        return {**result,'issues':['CUSTOM_HOST_SCOPE_REQUIRES_PROTECTED_INVENTORY'],'_scope_diagnostics':scope}
    b.page.evaluate=evaluate
    initial=await d.prepare()
    assert initial['scope_certificate']['inventory_changed']
    # Initial discovery lacks scoped positive predicates. Its final certificate
    # cannot be a prerequisite for constructing the input to prospective checking.
    assert not initial['scope_certificate']['certified']
    options=await review.resource_options()
    await review.select_resource_contract(1,options['digest'],confirmed=True)
    preview=await review.preview()
    if closed:
        assert preview['scope_certificate']['inaccessible_shadow_roots']==1
        assert not preview['scope_certificate']['certified']
        assert preview['gate_status']['readonly_access']=='FAIL'
        with pytest.raises(PermissionError):await review.approve(preview['digest'],True)
        assert not EvidenceConfig.load(review.path,allow_unreviewed=True).reviewed
    else:
        assert preview['scope_certificate']['certified']
        assert preview['scope_certificate']['relevant_evidence_stable']
        await review.approve(preview['digest'],True)
        assert verify(await stable_authentication(b))==Result.AUTHENTICATED_AND_VERIFIED


async def test_existing_interactive_hold_executes_r_selection_p_preview_a_approval(tmp_path,monkeypatch,capsys):
    """Scripted local operator inputs exercise the real review loop, not a browser."""
    import sys
    if sys.platform!='win32':pytest.skip('Windows msvcrt review-input branch')
    import msvcrt
    import app.config
    import browser.evidence_review as module
    b,d,review=await observed_scalar(tmp_path)
    keys=list('r1pa')
    monkeypatch.setattr(module.sys,'stdin',SimpleNamespace(isatty=lambda:True))
    monkeypatch.setattr(msvcrt,'kbhit',lambda:bool(keys))
    monkeypatch.setattr(msvcrt,'getwch',lambda:keys.pop(0))
    # Preserve controlled traces under pytest's temporary directory only.
    monkeypatch.setattr(app.config,'ROOT',tmp_path)
    observed=await module.evidence_review_hold(b,15)
    assert verify(observed)==Result.AUTHENTICATED_AND_VERIFIED
    assert not keys and b.login_submissions==0
    statuses=[e.get('diagnostic',{}).get('status') for e in b.trace.events]
    assert 'SELECTED_UNREVIEWED' in statuses and 'READY_FOR_EXPLICIT_APPROVAL' in statuses
    assert statuses[-1]=='APPROVED'
