"""Ownership/limits/cleanup with HTTP and websocket doubles only."""
import asyncio
from collections import deque
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from browser.metadata_channel import metadata_channel,MetadataChannelError


class SocketDouble:
    def __init__(self):
        self.queue=deque();self.sent=[];self.closed=False
        self.dom_result={'root':{'nodeType':9,'backendNodeId':1}}
        self.dom_events=[];self.on_dom=None;self.cancel_dom=False

    async def send(self,raw):
        message=json.loads(raw);self.sent.append(message)
        method=message['method']
        if method=='Target.attachToTarget':result={'sessionId':'private-metadata-session'}
        elif method=='Target.detachFromTarget':result={}
        else:
            if self.cancel_dom:self.queue.append(asyncio.CancelledError())
            if self.on_dom:self.on_dom()
            self.queue.extend(self.dom_events);self.dom_events=[]
            result=self.dom_result
        self.queue.append({'id':message['id'],'result':result})

    async def recv(self):
        response=self.queue.popleft()
        if isinstance(response,BaseException):raise response
        return json.dumps(response)

    async def close(self):self.closed=True


@pytest.fixture
def channel_fixture(monkeypatch):
    import browser.metadata_channel as module
    owner=SimpleNamespace(target='private-owned-target',session='private-guard-session',kind='page',
        generation=1,document_generation=1,frame_id='private-frame',loader_id='private-loader')
    guard=SimpleNamespace(failed=False,closing=False,blocking_hosts=set(),
        primary_session=owner.session,explicit_page_roots=set(),session_states={owner.session:owner},
        current_sessions={owner.target:owner.session},primary_observable=lambda:True,
        session_observable=lambda session:session==owner.session,send=AsyncMock())
    browser=SimpleNamespace(guard=guard,close_started=False,browser=SimpleNamespace(cdp_url='http://127.0.0.1:9000'))
    ws=SocketDouble();calls={'http':0,'websocket':0};connect_options={}
    class Response:
        status_code=200
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def aiter_bytes(self):
            yield json.dumps({'webSocketDebuggerUrl':'ws://127.0.0.1:9000/devtools/browser/private-endpoint'}).encode()
    class Client:
        def __init__(self,**kwargs):assert kwargs['trust_env'] is False and kwargs['follow_redirects'] is False
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        def stream(self,method,url):
            calls['http']+=1
            assert method=='GET' and url=='http://127.0.0.1:9000/json/version'
            return Response()
    async def connect(endpoint,**kwargs):
        calls['websocket']+=1;connect_options.update(kwargs)
        assert endpoint=='ws://127.0.0.1:9000/devtools/browser/private-endpoint'
        return ws
    monkeypatch.setattr(module.httpx,'AsyncClient',Client)
    monkeypatch.setattr(module.websockets,'connect',connect)
    return SimpleNamespace(browser=browser,guard=guard,owner=owner,ws=ws,calls=calls,
        connect_options=connect_options,module=module,response=Response)


async def test_only_exact_owned_target_is_attached_and_dom_session_is_mapped(channel_fixture):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session) as channel:
        result=await channel.send('DOM.getDocument',{'depth':4,'pierce':True},session=f.owner.session)
        assert result==f.ws.dom_result and channel.protocol_commands==2
        assert channel.command_counts['DOM.getDocument']==1 and channel.bytes_received>0
        assert f.ws.sent[0]['params']=={'targetId':f.owner.target,'flatten':True}
        assert f.ws.sent[1]['sessionId']=='private-metadata-session'
    assert f.ws.closed and f.ws.sent[-1]['method']=='Target.detachFromTarget'
    assert f.ws.sent[-1]['params']=={'sessionId':'private-metadata-session'}
    assert not f.guard.failed
    f.guard.send.assert_not_called()
    assert f.connect_options['max_size']==4*1024*1024 and f.connect_options['compression'] is None


@pytest.mark.parametrize('method',['Runtime.evaluate','Fetch.disable','Network.enable','Target.createTarget','DOM.setAttributeValue'])
async def test_method_allowlist_prevents_mutations_and_unrelated_protocol_access(channel_fixture,method):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session) as channel:
        with pytest.raises(MetadataChannelError,match='METADATA_METHOD_NOT_ALLOWED'):
            await channel.send(method,{'private':'FORBIDDEN_PRIVATE_MARKER'})
        assert len(f.ws.sent)==1
    assert f.ws.closed and not f.guard.failed


async def test_foreign_session_cannot_be_selected(channel_fixture):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session) as channel:
        with pytest.raises(MetadataChannelError,match='METADATA_SESSION_NOT_OWNED'):
            await channel.send('DOM.getDocument',session='foreign-private-session')
        assert len(f.ws.sent)==1


@pytest.mark.parametrize('during',[False,True])
async def test_generation_drift_blocks_result_before_or_after_command(channel_fixture,during):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session) as channel:
        def drift():f.owner.document_generation+=1
        if during:f.ws.on_dom=drift
        else:drift()
        with pytest.raises(MetadataChannelError,match='METADATA_DOCUMENT_CHANGED'):
            await channel.send('DOM.getDocument')
        assert channel.command_counts['DOM.getDocument']==int(during)
    assert f.ws.closed and not f.guard.failed


async def test_unprotected_target_is_rejected_before_http_or_attach(channel_fixture):
    f=channel_fixture;f.guard.failed=True
    with pytest.raises(MetadataChannelError,match='METADATA_OWNERSHIP_UNAVAILABLE'):
        async with metadata_channel(f.browser,f.owner.session):pass
    assert f.calls=={'http':0,'websocket':0} and not f.ws.sent


async def test_nonloopback_endpoint_cannot_receive_even_version_request(channel_fixture):
    f=channel_fixture;f.browser.browser.cdp_url='http://remote.invalid:9000'
    with pytest.raises(MetadataChannelError,match='METADATA_ENDPOINT_INVALID'):
        async with metadata_channel(f.browser,f.owner.session):pass
    assert f.calls=={'http':0,'websocket':0}


async def test_size_limit_is_both_per_message_and_aggregate(channel_fixture):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session,max_bytes=1024) as channel:
        f.ws.dom_result={'padding':'x'*600}
        await channel.send('DOM.getDocument')
        assert channel.bytes_received<1024
        with pytest.raises(MetadataChannelError,match='METADATA_BYTE_LIMIT_REACHED'):
            await channel.send('DOM.getDocument')
        assert channel.bytes_received>1024
    assert f.ws.closed and not f.guard.failed


async def test_oversized_message_is_rejected_without_exposing_contents(channel_fixture):
    f=channel_fixture
    with pytest.raises(MetadataChannelError) as caught:
        async with metadata_channel(f.browser,f.owner.session,max_bytes=1024) as channel:
            f.ws.dom_result={'padding':'FORBIDDEN_PRIVATE_MARKER'*100}
            await channel.send('DOM.describeNode',{'backendNodeId':123})
    assert caught.value.code=='METADATA_BYTE_LIMIT_REACHED'
    assert 'FORBIDDEN_PRIVATE_MARKER' not in str(caught.value)
    assert f.ws.closed and not f.guard.failed


async def test_cancellation_closes_only_owned_metadata_transport(channel_fixture):
    f=channel_fixture
    with pytest.raises(asyncio.CancelledError):
        async with metadata_channel(f.browser,f.owner.session) as channel:
            f.ws.cancel_dom=True
            await channel.send('DOM.getDocument')
    assert f.ws.closed and not f.guard.failed
    f.guard.send.assert_not_called()


async def test_only_real_mutations_on_own_session_increment_epoch(channel_fixture):
    f=channel_fixture
    async with metadata_channel(f.browser,f.owner.session) as channel:
        f.ws.dom_events=[
            {'method':'DOM.setChildNodes','sessionId':'private-metadata-session','params':{}},
            {'method':'DOM.shadowRootPushed','sessionId':'private-metadata-session','params':{'private':'FORBIDDEN_PRIVATE_MARKER'}},
            {'method':'DOM.childNodeRemoved','sessionId':'private-metadata-session','params':{}},
            {'method':'DOM.characterDataModified','sessionId':'foreign-session','params':{}},
        ]
        await channel.send('DOM.getDocument')
        assert channel.mutation_epoch==2


async def test_transport_exception_text_is_never_exposed(channel_fixture,monkeypatch):
    f=channel_fixture
    async def unavailable(*args,**kwargs):raise OSError('FORBIDDEN_PRIVATE_MARKER')
    monkeypatch.setattr(f.module.websockets,'connect',unavailable)
    with pytest.raises(MetadataChannelError) as caught:
        async with metadata_channel(f.browser,f.owner.session):pass
    assert caught.value.code=='METADATA_CHANNEL_UNAVAILABLE'
    assert 'FORBIDDEN_PRIVATE_MARKER' not in str(caught.value)
    assert caught.value.__suppress_context__
    assert not f.guard.failed


async def test_websocket_parser_size_rejection_is_a_fixed_limit_category(channel_fixture):
    f=channel_fixture
    class TooLarge(Exception):sent=SimpleNamespace(code=1009)
    with pytest.raises(MetadataChannelError) as caught:
        async with metadata_channel(f.browser,f.owner.session) as channel:
            f.ws.recv=AsyncMock(side_effect=TooLarge('FORBIDDEN_PRIVATE_MARKER'))
            await channel.send('DOM.getDocument')
    assert caught.value.code=='METADATA_BYTE_LIMIT_REACHED'
    assert 'FORBIDDEN_PRIVATE_MARKER' not in str(caught.value)
    assert f.ws.closed and not f.guard.failed


async def test_stalled_attachment_is_bounded_and_closes_own_socket(channel_fixture,monkeypatch):
    f=channel_fixture
    async def stalled():await asyncio.Future()
    f.ws.recv=stalled
    monkeypatch.setattr(f.module,'START_TIMEOUT',.01)
    with pytest.raises(MetadataChannelError) as caught:
        async with metadata_channel(f.browser,f.owner.session):pass
    assert caught.value.code=='METADATA_CHANNEL_TIMEOUT'
    assert f.ws.closed and not f.guard.failed
    f.guard.send.assert_not_called()


async def test_body_error_closes_channel_without_touching_browser(channel_fixture):
    f=channel_fixture
    with pytest.raises(ValueError):
        async with metadata_channel(f.browser,f.owner.session):raise ValueError('local double failure')
    assert f.ws.closed and not f.browser.close_started and not f.guard.failed
