"""Owned, capped read-only DOM channel; never shares the navigation guard socket.

Only fixed counters and categories are exposed. Protocol identifiers and response
bodies stay transient and are never logged by this module.
"""
import asyncio
from contextlib import asynccontextmanager
import json
from urllib.parse import urlsplit

import httpx
import websockets
from websockets.exceptions import PayloadTooBig

from browser.cdp_endpoint import browser_websocket


MAX_BYTES=4*1024*1024
START_TIMEOUT=5
DOM_METHODS=frozenset({'DOM.getDocument','DOM.describeNode'})
MUTATIONS=frozenset({'DOM.documentUpdated','DOM.childNodeInserted','DOM.childNodeRemoved',
    'DOM.childNodeCountUpdated','DOM.attributeModified','DOM.attributeRemoved',
    'DOM.characterDataModified','DOM.shadowRootPushed','DOM.shadowRootPopped'})


class MetadataChannelError(RuntimeError):
    """Fixed categories only; never expose protocol/transport exception text."""
    def __init__(self,category):
        self.category=self.code=category
        super().__init__(category)


def transport_category(error):
    # The websocket parser can reject an oversized message before delivering raw
    # bytes. Class/close codes classify that limit without inspecting private text.
    if isinstance(error,PayloadTooBig) or getattr(getattr(error,'sent',None),'code',None)==1009:
        return 'METADATA_BYTE_LIMIT_REACHED'
    return 'METADATA_CHANNEL_UNAVAILABLE'


class MetadataChannel:
    def __init__(self,browser,guard_session,max_bytes):
        if type(max_bytes) is not int or not 1<=max_bytes<=MAX_BYTES:
            raise MetadataChannelError('METADATA_BYTE_LIMIT_INVALID')
        self._browser=browser;self._guard=browser.guard;self._guard_session=guard_session
        self._max_bytes=max_bytes;self._identity=None;self._session=None;self._ws=None
        self._serial=0;self._lock=asyncio.Lock();self._closed=False;self._failed=False
        self.mutation_epoch=0;self.bytes_received=0;self.protocol_commands=0
        self.command_counts={method:0 for method in (*sorted(DOM_METHODS),
            'Target.attachToTarget','Target.detachFromTarget')}

    def _owned_identity(self):
        guard=self._guard;browser=self._browser
        if (guard is None or browser.guard is not guard or browser.close_started or guard.failed
            or guard.closing or guard.blocking_hosts or not guard.primary_observable()
            or not guard.session_observable(self._guard_session)):
            raise MetadataChannelError('METADATA_OWNERSHIP_UNAVAILABLE')
        owner=guard.session_states.get(self._guard_session)
        if (not owner or owner.kind!='page' or not owner.target
            or guard.current_sessions.get(owner.target)!=self._guard_session
            or self._guard_session!=guard.primary_session and self._guard_session not in guard.explicit_page_roots):
            raise MetadataChannelError('METADATA_OWNERSHIP_UNAVAILABLE')
        return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id)

    def _assert_owned(self):
        if self._owned_identity()!=self._identity:
            raise MetadataChannelError('METADATA_DOCUMENT_CHANGED')

    def _consume(self,size):
        self.bytes_received+=size
        if size>self._max_bytes or self.bytes_received>self._max_bytes:
            self._failed=True
            raise MetadataChannelError('METADATA_BYTE_LIMIT_REACHED')

    async def start(self):
        try:
            async with asyncio.timeout(START_TIMEOUT):await self._start()
        except TimeoutError:
            self._failed=True
            raise MetadataChannelError('METADATA_CHANNEL_TIMEOUT') from None

    async def _start(self):
        try:
            self._identity=self._owned_identity()
            endpoint=self._browser.browser.cdp_url
            base=urlsplit(endpoint)
            # Validate before the first HTTP request; never follow a redirect.
            if (base.scheme!='http' or base.hostname not in {'127.0.0.1','localhost','::1'}
                or base.port is None or base.username or base.password or base.query or base.fragment
                or base.path not in {'','/'}):
                raise MetadataChannelError('METADATA_ENDPOINT_INVALID')
            chunks=[];version_size=0
            async with httpx.AsyncClient(trust_env=False,timeout=2,follow_redirects=False) as client:
                async with client.stream('GET',endpoint.rstrip('/')+'/json/version') as response:
                    if response.status_code!=200:raise MetadataChannelError('METADATA_ENDPOINT_UNAVAILABLE')
                    async for chunk in response.aiter_bytes():
                        version_size+=len(chunk);self._consume(len(chunk))
                        if version_size>16384:raise MetadataChannelError('METADATA_ENDPOINT_INVALID')
                        chunks.append(chunk)
            version=json.loads(b''.join(chunks))
            if not isinstance(version,dict):raise MetadataChannelError('METADATA_ENDPOINT_INVALID')
            websocket=browser_websocket(endpoint,version)
            self._assert_owned()
            self._ws=await websockets.connect(websocket,max_size=self._max_bytes,max_queue=4,
                ping_interval=None,compression=None,open_timeout=2,close_timeout=.25)
            self._assert_owned()
            attached=await self._request('Target.attachToTarget',{'targetId':self._identity[0],'flatten':True})
            session=attached.get('sessionId')
            if not isinstance(session,str) or not session:raise MetadataChannelError('METADATA_ATTACH_UNAVAILABLE')
            self._session=session
            self._assert_owned()
        except MetadataChannelError:
            raise
        except asyncio.CancelledError:
            raise
        except Exception as error:
            self._failed=True
            raise MetadataChannelError(transport_category(error)) from None

    async def _request(self,method,params=None,session=None):
        self._serial+=1;ident=self._serial
        self.protocol_commands+=1;self.command_counts[method]+=1
        message={'id':ident,'method':method,'params':params or {}}
        if session is not None:message['sessionId']=session
        await self._ws.send(json.dumps(message))
        while True:
            raw=await self._ws.recv()
            self._consume(len(raw.encode('utf-8')) if isinstance(raw,str) else len(raw))
            event=json.loads(raw)
            if not isinstance(event,dict):raise MetadataChannelError('METADATA_PROTOCOL_INVALID')
            if event.get('id')==ident:
                if 'error' in event:raise MetadataChannelError('METADATA_COMMAND_REJECTED')
                result=event.get('result')
                if not isinstance(result,dict):raise MetadataChannelError('METADATA_PROTOCOL_INVALID')
                return result
            method_name=event.get('method')
            if event.get('sessionId')==self._session and method_name in MUTATIONS:
                self.mutation_epoch+=1
            if method_name=='Target.detachedFromTarget' and event.get('params',{}).get('sessionId')==self._session:
                self._failed=True
                raise MetadataChannelError('METADATA_SESSION_UNAVAILABLE')
            # DOM.setChildNodes is frontend hydration, not a page mutation.

    async def send(self,method,params=None,session=None):
        if method not in DOM_METHODS:raise MetadataChannelError('METADATA_METHOD_NOT_ALLOWED')
        if session is not None and session!=self._guard_session:
            raise MetadataChannelError('METADATA_SESSION_NOT_OWNED')
        if self._closed or self._failed or self._ws is None or self._session is None:
            raise MetadataChannelError('METADATA_CHANNEL_UNAVAILABLE')
        async with self._lock:
            try:
                self._assert_owned()
                result=await self._request(method,params,self._session)
                self._assert_owned()
                return result
            except MetadataChannelError:
                raise
            except asyncio.CancelledError:
                raise
            except Exception as error:
                self._failed=True
                raise MetadataChannelError(transport_category(error)) from None

    async def close(self):
        if self._closed:return
        self._closed=True
        try:
            if self._ws is not None and self._session is not None and not self._failed:
                try:
                    async with asyncio.timeout(.25):
                        await self._request('Target.detachFromTarget',{'sessionId':self._session})
                except (Exception,asyncio.CancelledError):pass
        finally:
            if self._ws is not None:
                try:
                    async with asyncio.timeout(.5):await self._ws.close()
                except (Exception,asyncio.CancelledError):pass
            self._session=None;self._ws=None


@asynccontextmanager
async def metadata_channel(browser,guard_session,max_bytes=MAX_BYTES):
    channel=MetadataChannel(browser,guard_session,max_bytes)
    try:
        await channel.start()
        yield channel
    finally:
        await channel.close()
