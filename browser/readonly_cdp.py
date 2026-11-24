"""Separate read-only CDP channel; optional discovery never owns the safety transport."""
import asyncio
import json
import httpx
import websockets
from time import monotonic


class ReadOnlyCommandError(RuntimeError):
    """Fixed categories only; never retain/serialize the private CDP error text."""
    def __init__(self, error):
        message=str(error.get('message','')).lower()
        self.code=error.get('code') if isinstance(error.get('code'),int) else None
        self.category=('BODY_EVICTED' if 'evict' in message else
            'NO_RESOURCE_FOR_REQUEST' if 'no resource with given identifier' in message else
            'BODY_NOT_AVAILABLE' if 'no data found' in message else
            'SESSION_UNAVAILABLE' if 'session' in message and ('closed' in message or 'not found' in message) else
            'CDP_COMMAND_REJECTED')
        super().__init__(self.category)


class ReadOnlyCDP:
    def __init__(self,observer,diagnostic=None,*,browser_errors=False):
        self.observer=observer;self.ws=None;self.reader=None;self.pending={};self.serial=0
        self.closed=False;self.failure=None
        self.ready=False
        self.diagnostic=diagnostic;self.active_sessions=set()
        self.browser_errors=browser_errors
    def record(self,state,**data):
        if self.diagnostic:self.diagnostic('READONLY_CDP',{'state':state,**data})
    async def start(self,cdp_url,target_ids):
        async with httpx.AsyncClient(trust_env=False) as client:
            info=(await client.get(cdp_url+'/json/version')).json()
        from browser.cdp_endpoint import browser_websocket
        self.ws=await websockets.connect(browser_websocket(cdp_url,info),max_size=2**22,ping_interval=None)
        self.reader=asyncio.create_task(self._read())
        self.record('CDP_CONNECTED',guard={'connection_level':'BROWSER','owner':'READONLY_OBSERVER'})
        # Snapshot the supplied ownership set; never iterate a set changing on guard events.
        owned=tuple(target_ids)
        current=await self.send('Target.getTargets')
        pages={t['targetId'] for t in current['targetInfos'] if t['type']=='page'}
        for target in owned:
            if target not in pages:
                self.record('STALE_TARGET_SKIPPED');continue
            attached=await self.send('Target.attachToTarget',{'targetId':target,'flatten':True})
            self.active_sessions.add(attached['sessionId'])
            self.record('TARGET_ATTACHED')
            await self.send('Network.enable',{'maxTotalBufferSize':1048576,'maxResourceBufferSize':262144,'maxPostDataSize':0},attached['sessionId'])
            if self.browser_errors:
                await self.send('Runtime.enable',session=attached['sessionId'])
                await self.send('Log.enable',session=attached['sessionId'])
        if not self.active_sessions:raise RuntimeError('No owned live discovery target')
        self.ready=True
    async def send(self,method,params=None,session=None):
        if self.closed or self.failure:raise RuntimeError('Read-only monitor unavailable')
        self.serial+=1;ident=self.serial
        future=asyncio.get_running_loop().create_future();self.pending[ident]=future
        message={'id':ident,'method':method,'params':params or {}}
        if session:message['sessionId']=session
        started=monotonic()
        try:
            await self.ws.send(json.dumps(message))
            result=await asyncio.wait_for(future,5)
            self.record('COMMAND_COMPLETED',command=method,duration_ms=round(1000*(monotonic()-started),1))
            return result
        except Exception as error:
            self.record('COMMAND_FAILED',command=method,duration_ms=round(1000*(monotonic()-started),1),
                category='TIMEOUT' if isinstance(error,TimeoutError) else getattr(error,'category','COMMAND_OR_TRANSPORT_FAILURE'),
                cdp_error_code=getattr(error,'code',None))
            raise
        finally:self.pending.pop(ident,None)
    async def _read(self):
        ending='EOF'
        try:
            async for raw in self.ws:
                event=json.loads(raw)
                if 'id' in event:
                    future=self.pending.get(event['id'])
                    if future and not future.done():
                        if 'error' in event:future.set_exception(ReadOnlyCommandError(event['error']))
                        else:future.set_result(event.get('result',{}))
                elif event.get('method') in {'Network.requestWillBeSent','Network.responseReceived',
                        'Network.loadingFinished','Network.loadingFailed'}:
                    self.observer(event)
                elif self.browser_errors and event.get('method') in {
                        'Runtime.exceptionThrown','Runtime.consoleAPICalled','Log.entryAdded'}:
                    self.observer(event)
                elif event.get('method')=='Target.detachedFromTarget':
                    self.active_sessions.discard(event.get('params',{}).get('sessionId'))
                    self.record('TARGET_DETACHED')
                    self.observer(event)
        except asyncio.CancelledError:
            ending='CANCELLED'
            raise
        except Exception:
            ending='EXCEPTION'
            self.failure='DISCOVERY_TRANSPORT_UNAVAILABLE'
        finally:
            self.record('READER_TERMINATED',status='EXPECTED' if self.closed else 'UNEXPECTED',
                guard={'connection_level':'BROWSER','reader_state':ending,'websocket_close_code':getattr(self.ws,'close_code',None)})
            if not self.closed:
                self.failure=self.failure or 'DISCOVERY_TRANSPORT_CLOSED'
                for future in self.pending.values():
                    if not future.done():future.set_exception(RuntimeError('Read-only monitor lost'))
    async def close(self):
        if self.closed:return
        self.closed=True
        if self.reader:
            self.reader.cancel();await asyncio.gather(self.reader,return_exceptions=True)
        for future in self.pending.values():
            if not future.done():future.cancel()
        self.pending.clear()
        if self.ws:await asyncio.wait_for(self.ws.close(),2)
