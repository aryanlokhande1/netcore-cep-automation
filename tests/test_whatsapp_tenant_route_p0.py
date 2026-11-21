"""Protected route regressions; no browser, Login or provider execution."""
from types import SimpleNamespace
from unittest.mock import AsyncMock,Mock
from urllib.parse import urlsplit

import pytest


def protected_tools(monkeypatch, url=None, *, tenant=None):
    from app.config import OriginPolicy
    from app.trace import Trace
    from modules.whatsapp.contract import SETUP_URL
    from modules.whatsapp.tools import V2BrowserTools
    import modules.whatsapp.tools as module
    import modules.whatsapp.browser_baseline as baseline

    expected=urlsplit(SETUP_URL)
    document=['task-target','task-session',1,2,'task-frame','task-loader']
    current=SimpleNamespace(url=url or SETUP_URL)
    page=SimpleNamespace(page_id='task-page',url=AsyncMock(side_effect=lambda:current.url))
    async def send(method,**kwargs):
        assert method=='Page.getFrameTree'
        actual=urlsplit(current.url)
        return {'frameTree':{'frame':{'id':document[4],'loaderId':document[5],
            'url':current.url.split('#',1)[0],
            'urlFragment':'#'+actual.fragment if actual.fragment else ''}}}
    guard=SimpleNamespace(ensure_observable=AsyncMock(),send=send,
        target_aliases={'task-target':13},session_aliases={'task-session':6})
    trace=Trace();trace.enable_agent_trace()
    browser=SimpleNamespace(page=page,setup_page=page,guard=guard,trace=trace,
        browser=SimpleNamespace(context=SimpleNamespace(active_page=AsyncMock(return_value=page))),
        policy=OriginPolicy({f'{expected.scheme}://{expected.netloc}'}),budget=lambda:None)
    tools=object.__new__(V2BrowserTools)
    tools.browser=browser;tools.terminal=None;tools.diagnostic_mode=False
    tools.ui=SimpleNamespace(origin=f'{expected.scheme}://{expected.netloc}',
        tenant=expected.path.strip('/').split('/')[0] if tenant is None else tenant)
    tools.references={'old-reference':{}};tools.images={'old-image':{}}
    tools.observed_bindings={'campaign_name':{}};tools.next_allowed=True
    monkeypatch.setattr(module,'protected_document',lambda _:tuple(document))
    monkeypatch.setattr(baseline,'reject_security_negatives',AsyncMock())
    return tools,current,document


async def test_existing_setup_fragment_contract_is_accepted(monkeypatch):
    from modules.whatsapp.contract import SETUP_URL
    tools,_,document=protected_tools(monkeypatch,SETUP_URL+'#setup')
    assert await tools.protect()==tuple(document)


async def test_same_tenant_route_change_invalidates_old_references(monkeypatch):
    from modules.whatsapp.contract import SETUP_URL
    tools,current,_=protected_tools(monkeypatch)
    await tools.protect()
    current.url=SETUP_URL+'#setup'
    await tools.protect()
    assert tools.references=={}
    assert tools.images=={}
    assert tools.observed_bindings=={}
    assert tools.next_allowed is False


@pytest.mark.parametrize('suffix',['#unknown','#audience','#setup/other'])
async def test_unapproved_fragment_is_not_reported_as_tenant_mismatch(monkeypatch,suffix):
    from modules.whatsapp.contract import SETUP_URL,V2Stop
    tools,_,_=protected_tools(monkeypatch,SETUP_URL+suffix)
    with pytest.raises(V2Stop) as caught:await tools.protect()
    assert caught.value.code=='PROTECTED_TASK_FRAGMENT_UNSUPPORTED'
    assert caught.value.owner=='authorization'
    assert caught.value.unsafe is True


async def test_actual_other_tenant_still_stops_before_security_read(monkeypatch):
    from modules.whatsapp.contract import SETUP_URL,V2Stop
    import modules.whatsapp.browser_baseline as baseline
    target=urlsplit(SETUP_URL)
    tools,_,_=protected_tools(monkeypatch,f'{target.scheme}://{target.netloc}/other-tenant/admin/')
    with pytest.raises(V2Stop,match='PROTECTED_TENANT_ROUTE_MISMATCH') as caught:
        await tools.protect()
    assert caught.value.owner=='authorization' and caught.value.unsafe
    baseline.reject_security_negatives.assert_not_awaited()


async def test_origin_mismatch_is_authorization_failure(monkeypatch):
    from modules.whatsapp.contract import V2Stop
    tools,_,_=protected_tools(monkeypatch,'https://unapproved.invalid/other-tenant/')
    with pytest.raises(V2Stop) as caught:await tools.protect()
    assert caught.value.owner=='authorization'
    assert caught.value.code=='PROTECTED_ORIGIN_POLICY_REJECTED'


async def test_same_tenant_canonical_path_need_not_equal_initial_url(monkeypatch):
    from modules.whatsapp.contract import SETUP_URL
    tools,current,_=protected_tools(monkeypatch)
    await tools.protect()
    current.url=SETUP_URL+'/?canonical=1#setup'
    await tools.protect()


async def test_new_document_in_owned_tab_discards_previous_control_evidence(monkeypatch):
    tools,_,document=protected_tools(monkeypatch)
    tools.native_open_attempts={('setup','waba')};tools.preparations={('setup','waba'):'OPENED'}
    tools.failed_approaches={('setup','waba'):{'semantic'}}
    tools.waba_opened=True;tools.waba_selected=True
    await tools.protect()
    document[3]+=1;document[5]='next-loader'
    await tools.protect()
    assert tools.references=={} and tools.observed_bindings=={}
    assert not tools.native_open_attempts and not tools.preparations and not tools.failed_approaches
    assert tools.waba_opened is False and tools.waba_selected is False


async def test_active_wrong_tab_is_still_blocked(monkeypatch):
    from modules.whatsapp.contract import V2Stop
    tools,_,_=protected_tools(monkeypatch)
    tools.browser.browser.context.active_page=AsyncMock(return_value=SimpleNamespace(page_id='other-page'))
    with pytest.raises(V2Stop,match='PROTECTED_TASK_TAB_NOT_ACTIVE'):
        await tools.protect()


async def test_origin_only_ui_uses_fixed_authorized_route_tenant_for_reads(monkeypatch):
    tools,_,document=protected_tools(monkeypatch)
    tools.ui.tenant=None
    assert await tools.protect()==tuple(document)


def test_authorization_owner_cannot_be_presented_as_browser_control_failure():
    from modules.whatsapp.diagnostic_artifacts import classify_human_assisted_outcome
    assert classify_human_assisted_outcome({'status':'BLOCKED_WHATSAPP_TOOLS',
        'failure':{'code':'PROTECTED_TENANT_ROUTE_MISMATCH','owner':'authorization'}},[])=='BLOCKED_SECURITY_OR_SCOPE'


async def test_hash_change_during_paired_read_is_retried_without_adopting_another_target(monkeypatch):
    from modules.whatsapp.contract import SETUP_URL
    tools,current,document=protected_tools(monkeypatch)
    original=tools.browser.guard.send;calls=[]
    async def changing(method,**kwargs):
        calls.append(kwargs['session'])
        frame=await original(method,**kwargs)
        if len(calls)==1:current.url=SETUP_URL+'#setup'
        return frame
    tools.browser.guard.send=changing
    assert await tools.protect()==tuple(document)
    assert calls==['task-session','task-session']


async def test_incoherent_frame_loader_remains_blocked(monkeypatch):
    from modules.whatsapp.contract import V2Stop
    tools,_,_=protected_tools(monkeypatch)
    original=tools.browser.guard.send
    async def stale(method,**kwargs):
        tree=await original(method,**kwargs)
        tree['frameTree']['frame']['loaderId']='stale-loader'
        return tree
    tools.browser.guard.send=stale
    with pytest.raises(V2Stop,match='PROTECTED_URL_OR_DOCUMENT_MISMATCH'):
        await tools.protect()


async def test_route_evidence_is_redacted_and_does_not_certify_authentication(monkeypatch):
    import json
    from modules.whatsapp.contract import SETUP_URL,V2Stop
    tools,current,_=protected_tools(monkeypatch)
    await tools.protect()
    current.url=SETUP_URL+'?search=PRIVATE_QUERY#PRIVATE_FRAGMENT'
    with pytest.raises(V2Stop):await tools.protect()
    events=[e['agent_execution'] for e in tools.browser.trace.events
        if e.get('agent_execution',{}).get('kind')=='protected_task_route']
    assert events[-1]['failed_predicates']==['supported_task_fragment']
    assert events[-1]['initial_route']['fragment_class']=='NONE'
    assert events[-1]['route']['fragment_class']=='UNEXPECTED'
    assert events[-1]['changed_components']==['query','fragment']
    assert all(e['authentication_grant'] is False for e in events)
    assert 'PRIVATE_QUERY' not in json.dumps(events)
    assert 'PRIVATE_FRAGMENT' not in json.dumps(events)
    assert urlsplit(SETUP_URL).path not in json.dumps(events)


def test_saved_failure_was_before_page_observation_and_authentication_is_still_unverified():
    import json
    from pathlib import Path
    from app.state import Observation,Result
    from modules.login.verifier import verify
    directory=Path('outputs/human-assisted/login-whatsapp-5181fe3cbb45498c85d60c23741b4f92')
    if not directory.exists():pytest.skip('Historical run unavailable')
    actions=json.loads((directory/'whatsapp-doer-actions.json').read_text())
    action=next(e for e in actions['events'] if e['kind']=='browser_action')
    assert action['action']=='observe' and action['attempted'] is False
    assert action['owner']=='authorization' and action['error']=='PROTECTED_TENANT_ROUTE_MISMATCH'
    assert json.loads((directory/'whatsapp-page-observations.json').read_text())['latest_observation'] is None
    login=json.loads((directory/'login-verification.json').read_text())
    assert login['gates']['evidence_configured']=='FAIL'
    assert all(login['gates'][key]=='NOT_CHECKED' for key in ('dashboard','tenant_matches','readonly_access'))
    assert login['authentication_verified'] is False
    assert verify(Observation(expected_host=True))==Result.UNEXPECTED_PAGE


@pytest.mark.parametrize('point',['authorize_write','final_protection'])
async def test_transition_revokes_inflight_controller_certificate_before_journal_or_click(monkeypatch,point):
    from modules.whatsapp.contract import V2Stop
    from modules.whatsapp.tools import V2BrowserTools
    import modules.whatsapp.tools as module
    tools=object.__new__(V2BrowserTools)
    tools.stage='setup';tools.next_allowed=True
    tools.authorization=SimpleNamespace(persistence=True)
    tools.ui=SimpleNamespace(require_persistence=Mock(),next_selectors={'setup':'#observed-next'},
        stage_selectors={'setup':'#observed-stage'})
    tools.payload=SimpleNamespace(check_schedule=Mock())
    tools.browser=SimpleNamespace(page=SimpleNamespace(evaluate=AsyncMock(side_effect=[True,'Next Step'])))
    async def authorize():
        if point=='authorize_write':tools.next_allowed=False
    tools.authorize_write=authorize
    checks=[]
    async def protect():
        checks.append(True)
        if point=='final_protection' and len(checks)==2:tools.next_allowed=False
        return ('same-current-document',)
    tools.protect=protect
    locator=SimpleNamespace(is_visible=AsyncMock(return_value=True),click=AsyncMock())
    monkeypatch.setattr(module,'observed_node',AsyncMock(return_value='same-current-node'))
    monkeypatch.setattr(module,'stagehand_locator',AsyncMock(return_value=locator))
    tools.write_journal=Mock(side_effect=AssertionError('stale certificate reached persistence journal'))
    with pytest.raises(V2Stop,match='OBSERVER_STAGE_CERTIFICATE_REQUIRED'):
        await tools._advance()
    tools.write_journal.assert_not_called()
    locator.click.assert_not_awaited()


def test_setup_landing_trace_omits_query_and_opaque_route_values():
    from modules.whatsapp.destination import landing_route_parts
    parts = landing_route_parts('https://tenant.example/admin/path?token=private#step?code=private')
    assert parts == {'hostname': 'tenant.example', 'pathname': '/admin/path', 'fragment': '[redacted]'}


def test_setup_landing_wait_cannot_cross_host_or_tenant_boundary():
    from modules.whatsapp.contract import SETUP_URL
    from modules.whatsapp.destination import classify_setup_page, same_tenant_landing_path
    admin = SETUP_URL.split('/campaign/', 1)[0]
    assert same_tenant_landing_path(admin)
    assert same_tenant_landing_path(admin + '/other')
    assert not classify_setup_page(admin + '/other')['route_match']
    assert not same_tenant_landing_path(admin + '-other')
    assert not same_tenant_landing_path(admin + '/%2e%2e/other')
    assert not same_tenant_landing_path(admin.replace('https://', 'http://'))
    assert not same_tenant_landing_path(admin + '?private=1')


def test_exact_observed_preview_route_is_admitted_only_for_preview_stage():
    from modules.whatsapp.contract import SETUP_URL
    from modules.whatsapp.destination import campaign_step_route, classify_setup_page
    preview = SETUP_URL.split('/create/', 1)[0] + '/preview?campaign=opaque'
    assert campaign_step_route(preview, 'preview')
    assert not campaign_step_route(preview, 'schedule')
    assert not classify_setup_page(preview)['route_match']
    assert not campaign_step_route(preview.replace('tprappin2r', 'other-host'), 'preview')
    assert not campaign_step_route(preview.replace('bajajfinservmarket', 'wrong'), 'preview')
    assert not campaign_step_route(preview.replace('/preview?', '/preview-extra?'), 'preview')
