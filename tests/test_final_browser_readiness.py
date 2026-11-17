"""Safety preflight only: deterministic protocol/state checks, no browser or model calls."""
import asyncio
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.config import Config,OriginPolicy,ROOT
from app.state import Observation,Result
from app.trace import Trace
from browser.cdp_endpoint import browser_websocket
from browser.navigation_guard import NavigationGuard
from browser.stagehand_client import StagehandClient


def primary_guard():
    guard=NavigationGuard(OriginPolicy({'http://127.0.0.1:9000'}))
    owner=guard.register_attachment({'method':'Target.attachedToTarget','params':{
        'sessionId':'primary','targetInfo':{'targetId':'primary-target','type':'page'}}})
    owner.protected=owner.intercepting=True;owner.frame_id='root';owner.loader_id='first-loader';owner.document_generation=1
    guard.sessions.add(owner.target);guard.session_frames['primary']=('page','root')
    guard.reader_state='RUNNING';guard.connection_level='BROWSER'
    return guard


def child(guard):
    owner=guard.register_attachment({'method':'Target.attachedToTarget','sessionId':'primary','params':{
        'sessionId':'child','targetInfo':{'targetId':'child-target','type':'iframe'}}})
    owner.frame_id='child-frame';owner.intercepting=owner.protected=True
    return owner


@pytest.mark.parametrize('endpoint,valid',[('ws://127.0.0.1:9000/devtools/browser/PRIVATE',True),
    ('ws://127.0.0.1:9000/devtools/page/PRIVATE',False),('ws://remote.example:9000/devtools/browser/PRIVATE',False)])
def test_only_owned_browser_wide_websocket_is_accepted(endpoint,valid):
    if valid:assert browser_websocket('http://127.0.0.1:9000',{'webSocketDebuggerUrl':endpoint})==endpoint
    else:
        with pytest.raises(PermissionError):browser_websocket('http://127.0.0.1:9000',{'webSocketDebuggerUrl':endpoint})


async def test_detached_old_document_can_retire_without_target_destruction():
    guard=primary_guard();owner=child(guard)
    async def tree(method,params=None,session=None):
        assert method=='Page.getFrameTree' and session=='primary'
        return {'frameTree':{'frame':{'id':'root','loaderId':'second-loader'}}}
    guard.send=tree
    guard.target_event({'method':'Target.detachedFromTarget','params':{'sessionId':'child'}})
    guard.frame_event({'method':'Page.frameNavigated','sessionId':'primary','params':{
        'frame':{'id':'root','loaderId':'second-loader'}}})
    await owner.settling
    assert owner.completed and owner.frame_retired and not owner.destroyed
    assert guard.primary_observable() and not guard.failed and not guard.paused_requests
    assert any(e.get('completion')=='RETIRED_CHILD_DOCUMENT' for e in guard.lifecycle)


async def test_still_present_frame_is_not_retired_or_hidden_by_reader_label():
    guard=primary_guard();owner=child(guard);guard.detach_grace_seconds=.02
    async def tree(*a,**k):return {'frameTree':{'frame':{'id':'root','loaderId':'second-loader'},'childFrames':[{'frame':{'id':'child-frame'}}]}}
    guard.send=tree
    guard.target_event({'method':'Target.detachedFromTarget','params':{'sessionId':'child'}})
    guard.frame_event({'method':'Page.frameNavigated','sessionId':'primary','params':{'frame':{'id':'root','loaderId':'second-loader'}}})
    await owner.settling
    assert guard.failed and not owner.completed
    assert guard.failure_diagnostic['category']=='CHILD_RETIREMENT_UNPROVEN'
    assert guard.failure_diagnostic['guard']['reader_state']=='RUNNING'


async def test_reader_eof_records_boundary_and_wakes_paused_command():
    guard=primary_guard();guard.context_snapshot=lambda:{'process_state':'ALIVE','stagehand_close_started':False,'browser_close_started':False}
    class Ended:
        close_code=1000
        def __aiter__(self):return self
        async def __anext__(self):raise StopAsyncIteration
    guard.ws=Ended();future=asyncio.get_running_loop().create_future();guard.pending[1]=future
    await guard._read()
    assert guard.failed and guard.failure_diagnostic['category']=='UNEXPECTED_READER_END'
    detail=guard.failure_diagnostic['guard']
    assert detail['connection_level']=='BROWSER' and detail['process_state']=='ALIVE'
    assert detail['primary_attached'] and detail['websocket_close_code']==1000
    assert isinstance(future.exception(),RuntimeError)
    first=guard.failure_diagnostic.copy();guard.fail_closed('ANOTHER_FAILURE')
    assert guard.failure_diagnostic==first


@pytest.mark.parametrize('expected',[False,True])
async def test_reader_cancellation_is_distinct_from_target_detach(expected):
    guard=primary_guard()
    entered=asyncio.Event()
    class Waiting:
        close_code=None
        def __aiter__(self):return self
        async def __anext__(self):entered.set();await asyncio.Future()
    guard.ws=Waiting();guard.reader=asyncio.create_task(guard._read());await entered.wait()
    guard.closing=expected;guard.reader.cancel()
    await asyncio.gather(guard.reader,return_exceptions=True)
    assert guard.failed is not expected
    if not expected:assert guard.failure_diagnostic['category']=='READER_CANCELLED'
    assert guard.lifecycle[-1]['status']==('EXPECTED' if expected else 'UNEXPECTED') or guard.failed


def test_review_deadline_is_separate_and_writes_remain_disabled():
    browser=StagehandClient(Config('unused',timeout_seconds=1),OriginPolicy(set()),Trace())
    browser.started-=1000;browser.begin_readonly_review(10);browser.budget()
    with pytest.raises(PermissionError):browser.claim_login_submission()
    assert browser.login_submissions==0


async def test_positive_read_is_rejected_if_guard_lost_during_observation():
    browser=StagehandClient(Config('unused'),OriginPolicy(set()),Trace())
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set())
    async def observe_once():
        browser.guard.failed=True
        return Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True)
    browser._observe_once=observe_once
    with pytest.raises(PermissionError):await browser.observe()


async def test_failed_last_approval_check_does_not_persist_reviewed_flag(tmp_path):
    from app.safety import authorize_live
    from browser.evidence_review import EvidenceReview
    path=tmp_path/'review.json'
    path.write_text(json.dumps({'reviewed':False,'dashboard_selector':'main','tenant_selector':'span',
        'readonly_path':'/operator-observed-resource','tenant_json_key':'tenant','authenticated_json_key':'authenticated'}))
    cfg=Config('unused',live_login_enabled=True,evidence_file=str(path))
    permit=authorize_live(cfg,True,8);permit.started=True
    browser=StagehandClient(cfg,OriginPolicy(set(permit.origins)),Trace(),live_permit=permit)
    browser.page=SimpleNamespace(url=AsyncMock(return_value='https://tprappin2r.netcoresmartech.com/'))
    browser.guard=SimpleNamespace(failed=False)
    good=Observation(expected_host=True,dashboard=True,tenant_matches=True,readonly_access=True,evidence_configured=True)
    browser.observe=AsyncMock(side_effect=[good,good,Observation(expected_host=True)])
    review=EvidenceReview(browser);preview=await review.preview()
    with pytest.raises(PermissionError):await review.approve(preview['digest'],True)
    assert not json.loads(path.read_text())['reviewed'] and browser.evidence is None


def test_teacher_and_accepted_replay_artifacts_are_unchanged():
    manifest=json.loads((ROOT/'outputs'/'final-readiness-preservation.json').read_text())
    for name,sha in manifest.items():assert hashlib.sha256((ROOT/name).read_bytes()).hexdigest()==sha
    results=[json.loads(p.read_text()) for p in (ROOT/'outputs'/'teacher-replays').glob('*/result.json')]
    accepted=[r for r in results if r.get('teacher_status')=='ANALYZED']
    assert len(accepted)>=3 and all(r['teacher_validation_summary']['canonical_citations_valid'] for r in accepted)


@pytest.mark.parametrize('mode',['primary_loss','unresolved_document'])
async def test_primary_loss_or_held_document_stays_fail_closed(mode):
    guard=primary_guard();owner=child(guard);guard.detach_grace_seconds=.01
    if mode=='primary_loss':
        guard.mark_detached('primary')
        assert guard.failed and guard.failure_diagnostic['code']=='PRIMARY_TARGET_UNAVAILABLE'
    else:
        guard.paused_requests[('primary','held')]={'alias':1,'scope':'top_level'}
        guard.mark_detached('child')
        guard.target_event({'method':'Target.targetDestroyed','params':{'targetId':'child-target'}})
        await owner.settling
        assert guard.failed and not owner.completed
        assert guard.failure_diagnostic['category']=='CHILD_PROTECTION_UNPROVEN'


async def test_dom_discovery_remains_useful_without_an_optional_monitor(tmp_path):
    from browser.discovery import ReadOnlyDiscovery
    from pydantic import SecretStr
    cfg=Config('unused',expected_host='127.0.0.1',evidence_file=str(tmp_path/'evidence.json'))
    browser=StagehandClient(cfg,OriginPolicy({'http://127.0.0.1:9000'}),Trace(),fixture=True)
    browser.guard=SimpleNamespace(failed=False,blocking_hosts=set(),ensure_observable=AsyncMock())
    browser.expected_account=SecretStr('TEST_ONLY_NEVER_PERSISTED')
    browser.page=SimpleNamespace(url=AsyncMock(return_value='http://127.0.0.1:9000/'),
        evaluate=AsyncMock(return_value={
            'dashboard_candidates':[{'selector':'main','frame_selectors':[],'kind':'CONTENT_HEADING'}],
            'tenant_candidates':[{'selector':'#tenant','frame_selectors':[],'kind':'EXACT_EXPECTED_ACCOUNT_TEXT'}]}))
    discovery=ReadOnlyDiscovery(browser)
    result=await discovery.prepare()
    assert result['status']=='PENDING_OPERATOR_OBSERVATION' and result['dashboard_candidates']==1
    assert result['transport_status']=='UNAVAILABLE' and result['network_requests_replayed']==0
    assert not (tmp_path/'evidence.json').exists()
    assert 'TEST_ONLY_NEVER_PERSISTED' not in (tmp_path/'netcore-evidence-discovery.json').read_text()


def test_primary_renderer_crash_is_separate_from_transport_loss():
    guard=primary_guard()
    guard.target_event({'method':'Target.targetCrashed','params':{'targetId':'primary-target'}})
    assert guard.failed and guard.failure_diagnostic['category']=='TARGET_CRASHED'
    assert guard.failure_diagnostic['guard']['primary_crashed']
    assert guard.failure_diagnostic['guard']['reader_state']=='RUNNING'


async def test_finished_reader_cannot_leave_primary_observable():
    guard=primary_guard();guard.reader=asyncio.create_task(asyncio.sleep(0));await guard.reader
    assert not guard.primary_observable()
    with pytest.raises(PermissionError):await guard.ensure_observable()
    assert guard.failure_diagnostic['guard']['reader_task_done']
