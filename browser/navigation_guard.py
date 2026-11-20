"""CDP navigation safety guard only. Browser actions stay in Stagehand.

Intercept Document requests before dispatch, including server redirects and popups.
No headers, bodies, cookies or full request URLs leave this object's memory.
"""
import asyncio
import json
import hashlib
from urllib.parse import urlsplit
import httpx
import websockets
from time import monotonic
from browser.guard_state import GuardSession,SessionDetached


class GuardProtocolError(RuntimeError):
    """Keep Chromium's safe diagnostic identity, never its arbitrary error text."""
    def __init__(self,error):
        super().__init__('CDP_PROTOCOL_REJECTED')
        value=error.get('code')
        self.protocol_code=value if type(value) is int else None
        message=error.get('message')
        self.protocol_reason={'Invalid InterceptionId.':'INVALID_INTERCEPTION_ID',
            'Invalid InterceptionId':'INVALID_INTERCEPTION_ID',
            'Session with given id not found.':'SESSION_NOT_FOUND',
            'No target with given id found':'TARGET_NOT_FOUND'}.get(message,'UNCLASSIFIED_PROTOCOL_ERROR')
        self.protocol_error_sha256=hashlib.sha256(json.dumps(error,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def protocol_diagnostic(error):
    return {key:getattr(error,key) for key in ('protocol_code','protocol_reason','protocol_error_sha256')
        if isinstance(error,GuardProtocolError)}

# Only page/frame targets can issue Document navigations. Never pause unrelated workers.
DOCUMENT_TARGET_FILTER=[{'type':'page'},{'type':'iframe'},{'exclude':True}]

def auto_attach_settings():
    return {'autoAttach':True,'waitForDebuggerOnStart':True,'flatten':True,
        'filter':[dict(item) for item in DOCUMENT_TARGET_FILTER]}

class NavigationGuard:
    def __init__(self, policy,diagnostic=None):
        self.policy = policy
        self.blocked_hosts = set()
        self.blocked_documents = []
        self.session_frames = {}
        self.pending = {}
        self.serial = 0
        self.tasks = set()
        self.failed = False
        self.sessions = set()
        self.reader = self.ws = None
        self.closing = False
        self.readonly_observer=None
        self.failure_diagnostic=None
        self.page_targets=set()
        self.diagnostic=diagnostic
        self.lifecycle=[]
        self.target_aliases={};self.session_aliases={};self.session_targets={}
        self.operations={};self.detached=set()
        self.started=monotonic()
        self.session_states={};self.target_generations={};self.current_sessions={}
        self.primary_session=None
        self.explicit_page_roots=set()
        self.frame_parents={};self.frame_owners={}
        self.paused_requests={};self.request_aliases={}
        self.detach_grace_seconds=.25
        self.lifecycle_changed=asyncio.Event()
        self.connection_level='UNKNOWN';self.reader_state='NOT_STARTED'
        self.context_snapshot=lambda:{}
        self.last_document_decision=None
        self.navigation_observer=None
        self.document_requests={};self.network_serial=0;self.frame_aliases={}

    def notify_navigation(self,kind,session,url,*,redirect=False):
        # Optional, scoped telemetry cannot change interception/authorization.
        if self.navigation_observer:
            try:self.navigation_observer(kind,session,url,redirect=redirect)
            except Exception:self.record('NAVIGATION_DIAGNOSTIC_UNAVAILABLE',session=session)

    def record(self,state,session=None,target=None,**details):
        if target:self.target_aliases.setdefault(target,len(self.target_aliases)+1)
        if session:self.session_aliases.setdefault(session,len(self.session_aliases)+1)
        item={'state':state,'elapsed_ms':round(1000*(monotonic()-self.started),1),**details}
        if session:
            item['session']=self.session_aliases[session]
            owner=self.session_states.get(session)
            item['attached']=bool(owner and owner.attached)
            if owner:
                item['generation']=owner.generation
                item['attached']=owner.attached
                target=target or owner.target
        target=target or self.session_targets.get(session)
        if target:
            self.target_aliases.setdefault(target,len(self.target_aliases)+1)
            item['target']=self.target_aliases[target]
        self.lifecycle.append(item)
        if self.diagnostic:self.diagnostic('GUARD',item)

    def boundary_context(self):
        owner=self.session_states.get(self.primary_session)
        task=self.reader
        try:runtime=self.context_snapshot()
        except Exception:runtime={'process_state':'UNKNOWN'}
        return {'connection_level':self.connection_level,'reader_state':self.reader_state,
            'reader_task_done':bool(task and task.done()),
            'reader_task_cancelling':bool(task and hasattr(task,'cancelling') and task.cancelling()),
            'websocket_close_code':getattr(self.ws,'close_code',None),
            'primary_session':self.session_aliases.get(self.primary_session),
            'primary_generation':owner.generation if owner else None,
            'primary_attached':bool(owner and owner.attached),'primary_destroyed':bool(owner and owner.destroyed),
            'primary_crashed':bool(owner and owner.crashed),
            'document_generation':owner.document_generation if owner else None,
            'outstanding_commands':[{'command':command,'session':self.session_aliases.get(session)} for session,command in list(self.operations.values())[:8]],
            'paused_documents':len(self.paused_requests),'last_document_decision':self.last_document_decision,
            'guard_close_started':self.closing,**runtime}

    def fail_closed(self,stage,error=None,code=None,category=None):
        if self.failed or self.closing:return
        context=self.boundary_context()
        self.failed=True
        self.lifecycle_changed.set()
        kinds={'TimeoutError':'TIMEOUT','JSONDecodeError':'INVALID_PROTOCOL_JSON',
            'ConnectionClosedError':'CONNECTION_CLOSED','ConnectionClosedOK':'CONNECTION_CLOSED',
            'RuntimeError':'COMMAND_OR_PROTOCOL_FAILURE','GuardProtocolError':'COMMAND_OR_PROTOCOL_FAILURE'}
        self.failure_diagnostic={'stage':stage,'category':category or (kinds.get(type(error).__name__,'PROTOCOL_FAILURE') if error else 'PROTECTION_UNCERTAIN'),'guard':context,**protocol_diagnostic(error)}
        if code:self.failure_diagnostic['code']=code
        session=getattr(error,'guard_session',None)
        method=getattr(error,'guard_method',None)
        if not method and stage in {'READER','READER_END','READER_CANCELLED'}:method='WebSocket.read'
        if not method and category=='CHILD_RETIREMENT_UNPROVEN':method='WAIT_CHILD_RETIREMENT_PROOF'
        if method:self.failure_diagnostic['command']=method
        if session:
            self.failure_diagnostic['session']=self.session_aliases.get(session)
            self.failure_diagnostic['detached']=session in self.detached
        for direction in ('rcvd','sent'):
            close=getattr(error,direction,None)
            code=getattr(close,'code',None)
            if isinstance(code,int):self.failure_diagnostic[direction+'_close_code']=code
        self.record('GUARD_FAILURE',session=session,**{k:v for k,v in self.failure_diagnostic.items() if k!='session'})

    @property
    def blocking_hosts(self):
        """Only proven child frames are nonfatal; unknown context stays fail-closed."""
        child_hosts={d['host'] for d in self.blocked_documents if d['scope']=='subframe'}
        fatal_hosts={d['host'] for d in self.blocked_documents if d['scope']!='subframe'}
        return (self.blocked_hosts-child_hosts)|fatal_hosts

    def snapshot(self):
        return {'failed':self.failed,'primary_observable':self.primary_observable(),
            'paused_documents':len(self.paused_requests),'discovered_targets':len(self.target_aliases),
            'live_sessions':len(self.session_targets),'protected_targets':len(self.sessions),
            'detached_sessions':len(self.detached),'pending_commands':len(self.pending)}

    def primary_observable(self):
        return self.session_observable(self.primary_session)

    def session_observable(self,session):
        if self.reader is not None and (self.reader.done() or (hasattr(self.reader,'cancelling') and self.reader.cancelling())):
            return False
        owner=self.session_states.get(session)
        return bool(not self.failed and not self.closing and owner and owner.attached and not owner.crashed and owner.intercepting and owner.protected
            and self.current_sessions.get(owner.target)==owner.session)

    def admit_protected_tab(self,binding):
        source=self.session_states.get(binding.source_session)
        owner=self.session_states.get(binding.session)
        if (binding.guard is not self or binding.source_session!=self.primary_session or not self.primary_observable()
            or not source or source.target!=binding.source_target or source.generation!=binding.source_generation
            or source.document_generation!=binding.source_document_generation or not owner or owner.kind!='page'
            or owner.target!=binding.target or owner.generation!=binding.generation
            or (owner.document_generation,owner.frame_id,owner.loader_id)!=(binding.blank_document_generation,binding.blank_frame_id,binding.blank_loader_id)
            or not self.session_observable(owner.session) or self.paused_requests):
            self.fail_closed('EXPLICIT_TAB_ADMISSION',code='DOCUMENT_PROTECTION_UNCERTAIN')
            raise PermissionError('Explicit protected tab admission failed')
        self.explicit_page_roots.add(owner.session)
        self.record('EXPLICIT_PAGE_ROOT_ADMITTED',session=owner.session,code='SETUP_TAB_PROTECTED')

    def parent_is_protected(self,owner):
        seen=set()
        while owner and owner.session not in seen:
            seen.add(owner.session)
            if owner.session==self.primary_session or owner.session in self.explicit_page_roots:return self.session_observable(owner.session)
            if not owner.attached or not owner.intercepting or not owner.parent_verified:return False
            parent=self.session_states.get(owner.parent)
            if not parent or parent.generation!=owner.parent_generation:return False
            owner=parent
        return False

    def register_attachment(self,event):
        p=event['params'];session=p['sessionId']
        if session in self.session_states:return self.session_states[session]
        info=p['targetInfo'];target=info.get('targetId');kind=info['type']
        generation=self.target_generations.get(target,0)+1
        self.target_generations[target]=generation
        parent=event.get('sessionId')
        if parent not in self.session_states:
            parent=self.current_sessions.get(info.get('parentId')) or self.frame_owners.get(info.get('parentFrameId'))
        ancestor=self.session_states.get(parent)
        verified=bool(kind=='iframe' and self.parent_is_protected(ancestor))
        owner=GuardSession(session,target,kind,generation,parent,ancestor.generation if ancestor else None,verified)
        root=ancestor
        while root and root.kind=='iframe':root=self.session_states.get(root.parent)
        main=root if verified and root else self.session_states.get(self.primary_session)
        owner.root_session=main.session if verified and main else None
        owner.primary_document_generation=main.document_generation if main else 0
        self.session_states[session]=owner
        self.session_targets[session]=target;self.current_sessions[target]=session
        self.lifecycle_changed.set()
        if kind=='page' and self.primary_session is None:
            self.primary_session=session;self.explicit_page_roots.add(session)
        self.record('TARGET_ATTACHED',session=session,target=target,
            kind=kind if kind in {'page','iframe'} else 'other',parent_verified=verified,
            waiting_for_debugger=bool(p.get('waitingForDebugger')))
        return owner

    def document_scope(self,session,frame):
        context=self.session_frames.get(session)
        if not context:return 'unknown'
        kind,root=context;owner=self.session_states.get(session)
        if kind=='iframe':
            return 'subframe' if owner and owner.parent_verified else 'unknown'
        if kind!='page' or not root or not frame:return 'unknown'
        if frame==root:return 'top_level'
        seen=set();current=frame
        while current and current not in seen:
            seen.add(current);current=self.frame_parents.get(current)
            if current==root:return 'subframe'
        return 'unknown'

    def remember_tree(self,node,session,parent=None):
        frame=node.get('frame',{});ident=frame.get('id')
        if ident:
            self.frame_parents[ident]=frame.get('parentId',parent)
            self.frame_owners[ident]=session
        for child in node.get('childFrames',[]):self.remember_tree(child,session,ident)

    def frame_event(self,event):
        session=event.get('sessionId');p=event['params']
        if event['method']=='Page.navigatedWithinDocument':
            owner=self.session_states.get(session)
            if owner and owner.kind=='page' and p.get('frameId')==owner.frame_id:
                self.notify_navigation('SAME_DOCUMENT_NAVIGATION',session,p.get('url',''))
                self.lifecycle_changed.set()
            return
        if event['method']=='Page.frameDetached':
            if p.get('reason')=='remove':
                removed={p['frameId']}
                while True:
                    children={f for f,parent in self.frame_parents.items() if parent in removed}
                    if children<=removed:break
                    removed|=children
                for owner in self.session_states.values():
                    if owner.kind=='iframe' and owner.parent_verified and owner.frame_id in removed and session in {owner.parent,owner.root_session,self.primary_session}:
                        owner.frame_retired=True;owner.gone.set()
                for frame in removed:self.frame_parents.pop(frame,None);self.frame_owners.pop(frame,None)
                self.lifecycle_changed.set()
            self.record('FRAME_DETACHED',session=session,reason='REMOVED' if p.get('reason')=='remove' else 'SWAPPED')
            return
        if event['method']=='Page.frameAttached':
            self.frame_parents[p['frameId']]=p['parentFrameId'];self.frame_owners[p['frameId']]=session
        else:
            frame=p['frame'];ident=frame['id']
            self.frame_parents[ident]=frame.get('parentId');self.frame_owners[ident]=session
            owner=self.session_states.get(session)
            if owner and not frame.get('parentId'):
                owner.frame_id=ident
                loader=frame.get('loaderId')
                if loader and loader!=owner.loader_id:
                    owner.loader_id=loader;owner.document_generation+=1
                    self.lifecycle_changed.set()
            if owner and owner.kind=='page' and not frame.get('parentId'):
                self.session_frames[session]=('page',ident)
                self.record('DOCUMENT_NAVIGATED',session=session,scope='top_level',primary_observable=self.primary_observable())
                self.notify_navigation('DOCUMENT_COMMITTED',session,frame.get('url',''))

    async def ensure_observable(self):
        pending=[o.settling for o in self.session_states.values() if o.settling and not o.settling.done()]
        if pending:await asyncio.gather(*(asyncio.shield(task) for task in pending))
        if not self.primary_observable():
            self.fail_closed('OBSERVATION',code='PRIMARY_TARGET_UNAVAILABLE')
            raise PermissionError('Primary document observation unavailable')

    async def start(self, cdp_url):
        async with httpx.AsyncClient(trust_env=False) as client:
            info = (await client.get(cdp_url + "/json/version")).json()
        # CDP command deadlines enforce liveness; avoid optional websocket ping policy.
        from browser.cdp_endpoint import browser_websocket
        endpoint=browser_websocket(cdp_url,info)
        self.connection_level="BROWSER"
        self.ws = await websockets.connect(endpoint, max_size=2**22,ping_interval=None)
        self.reader = asyncio.create_task(self._read())
        self.reader_state='RUNNING'
        self.record('CDP_CONNECTED',guard={'connection_level':self.connection_level})
        await self.send('Target.setDiscoverTargets',{'discover':True})
        await self.send('Target.setAutoAttach',auto_attach_settings())
        targets = await self.send("Target.getTargets")
        for target in targets["targetInfos"]:
            if target["type"] == "page":
                # AutoAttach already reports existing targets.
                for _ in range(100):
                    if target["targetId"] in self.sessions:
                        break
                    await asyncio.sleep(.02)
                else:
                    raise RuntimeError("Navigation guard did not attach")

    async def send(self, method, params=None, session=None):
        if (self.failed or self.closing) and method!='Fetch.failRequest':
            raise PermissionError('Navigation guard failed closed')
        owner=self.session_states.get(session)
        if owner and (not owner.attached or self.current_sessions.get(owner.target)!=session):
            error=SessionDetached('CDP session generation is no longer attached')
            error.guard_session=session;error.guard_method=method
            self.record('STALE_COMMAND_PREVENTED',session=session,command=method,code='CHILD_TARGET_DETACHED' if owner.kind=='iframe' else 'PRIMARY_TARGET_UNAVAILABLE')
            raise error
        self.serial += 1
        serial = self.serial
        future = asyncio.get_running_loop().create_future()
        self.pending[serial] = future
        message = {"id": serial, "method": method, "params": params or {}}
        if session:
            message["sessionId"] = session
        started=monotonic()
        tracked=method not in {'Fetch.continueRequest','Fetch.failRequest'}
        if tracked:self.record('COMMAND_STARTED',session=session,command=method)
        self.operations[serial]=(session,method)
        try:
            await self.ws.send(json.dumps(message))
            result=await asyncio.wait_for(future, 10)
            if tracked:self.record('COMMAND_COMPLETED',session=session,command=method,duration_ms=round(1000*(monotonic()-started),1))
            return result
        except Exception as error:
            error.guard_session=session;error.guard_method=method
            self.record('COMMAND_FAILED',session=session,command=method,duration_ms=round(1000*(monotonic()-started),1),
                category='TIMEOUT' if isinstance(error,TimeoutError) else 'COMMAND_OR_TRANSPORT_FAILURE',detached=session in self.detached,**protocol_diagnostic(error))
            raise
        finally:
            self.pending.pop(serial,None);self.operations.pop(serial,None)

    async def _read(self):
        try:
            async for raw in self.ws:
                event = json.loads(raw)
                if "id" in event:
                    future = self.pending.pop(event["id"], None)
                    if future and not future.done():
                        if "error" in event:
                            future.set_exception(GuardProtocolError(event['error']))
                        else:
                            future.set_result(event.get("result", {}))
                elif event.get("method") in {"Target.attachedToTarget", "Fetch.requestPaused"}:
                    if event['method']=='Target.attachedToTarget':self.register_attachment(event)
                    else:self.register_request(event)
                    task = asyncio.create_task(self._event(event))
                    self.tasks.add(task)
                    task.add_done_callback(self.tasks.discard)
                elif event.get('method') in {'Target.targetCreated','Target.targetInfoChanged','Target.targetDestroyed','Target.detachedFromTarget','Target.targetCrashed'}:
                    self.target_event(event)
                elif event.get('method') in {'Page.frameAttached','Page.frameNavigated','Page.frameDetached','Page.navigatedWithinDocument'}:
                    self.frame_event(event)
                elif event.get('method') in {'Network.requestWillBeSent','Network.responseReceived','Network.loadingFinished','Network.loadingFailed'}:
                    self.network_event(event)
                    if self.readonly_observer and event['method'] in {'Network.requestWillBeSent','Network.responseReceived'}:
                        try:self.readonly_observer(event)
                        except Exception:self.readonly_observer=None
        except asyncio.CancelledError as error:
            self.reader_state='CANCELLED'
            if not self.closing:self.fail_closed('READER_CANCELLED',error,code='PRIMARY_TARGET_UNAVAILABLE',category='READER_CANCELLED')
            raise
        except Exception as error:
            self.reader_state='EXCEPTION'
            if not self.closing:self.fail_closed('READER',error,code='PRIMARY_TARGET_UNAVAILABLE')
        finally:
            if self.reader_state=='RUNNING':self.reader_state='EOF'
            self.record('READER_TERMINATED',guard=self.boundary_context(),status='EXPECTED' if self.closing else 'UNEXPECTED')
            if not self.closing:
                if not self.failure_diagnostic:self.fail_closed('READER_END',code='PRIMARY_TARGET_UNAVAILABLE',category='UNEXPECTED_READER_END')
                for future in self.pending.values():
                    if not future.done():future.set_exception(RuntimeError('Guard connection unavailable'))

    def target_event(self,event):
        p=event.get('params',{});method=event['method'];info=p.get('targetInfo',{})
        target=p.get('targetId') or info.get('targetId');session=p.get('sessionId')
        if method=='Target.targetCrashed':
            session=self.current_sessions.get(target);owner=self.session_states.get(session)
            if owner:
                owner.crashed=True
                self.record('TARGET_CRASHED',session=session)
                if session==self.primary_session:
                    self.fail_closed('TARGET_CRASH',code='PRIMARY_TARGET_UNAVAILABLE',category='TARGET_CRASHED')
                else:self.mark_detached(session)
            else:self.fail_closed('TARGET_CRASH',code='DOCUMENT_PROTECTION_UNCERTAIN',category='UNKNOWN_TARGET_CRASH')
            return
        state={'Target.targetCreated':'TARGET_DISCOVERED','Target.targetInfoChanged':'TARGET_CHANGED',
            'Target.targetDestroyed':'TARGET_DESTROYED','Target.detachedFromTarget':'TARGET_DETACHED'}[method]
        kind=info.get('type')
        self.record(state,session=session,target=target,
            **({'kind':kind if kind in {'page','iframe','worker','service_worker','shared_worker'} else 'other'} if kind else {}))
        if method=='Target.targetInfoChanged' and kind=='page':
            self.notify_navigation('TARGET_URL_CHANGED',self.current_sessions.get(target),info.get('url',''))
        self.lifecycle_changed.set()
        if method=='Target.detachedFromTarget':self.mark_detached(session)
        elif method=='Target.targetDestroyed':
            for owner in list(self.session_states.values()):
                if owner.target==target:
                    owner.destroyed=True;owner.gone.set();self.lifecycle_changed.set()
                    self.retire_destroyed_requests(owner)
                    self.mark_detached(owner.session)

    def mark_detached(self,session):
        owner=self.session_states.get(session)
        self.detached.add(session)
        if not owner:
            if not self.closing:self.fail_closed('TARGET_DETACH',code='PRIMARY_TARGET_UNAVAILABLE')
            return
        owner.attached=False;owner.protected=False
        self.lifecycle_changed.set()
        self.session_targets.pop(session,None);self.session_frames.pop(session,None)
        if self.current_sessions.get(owner.target)==session:
            self.current_sessions.pop(owner.target,None)
            self.sessions.discard(owner.target);self.page_targets.discard(owner.target)
        for ident,(active,_) in list(self.operations.items()):
            future=self.pending.get(ident)
            if active==session and future and not future.done():future.set_exception(SessionDetached('CDP session detached'))
        if self.closing:return
        if owner.kind!='iframe' or not owner.parent_verified or not self.primary_observable():
            error=SessionDetached('Document protection unavailable');error.guard_session=session
            error.guard_method=next((command for active,command in self.operations.values() if active==session),None)
            self.fail_closed('TARGET_DETACH',error,code='PRIMARY_TARGET_UNAVAILABLE')
        elif owner.settling is None:
            owner.settling=asyncio.create_task(self.settle_detached(owner))
            self.tasks.add(owner.settling);owner.settling.add_done_callback(self.tasks.discard)

    async def prove_frame_retirement(self,owner,deadline):
        main=self.session_states.get(owner.root_session or self.primary_session)
        if not (owner.frame_id and owner.parent_verified and self.primary_observable() and main
                and self.parent_is_protected(main) and main.document_generation>owner.primary_document_generation):return
        # Read the same protected session. Never adopt/reconnect a replacement page.
        remaining=deadline-monotonic()
        if remaining<=0:return
        tree=await asyncio.wait_for(self.send('Page.getFrameTree',session=main.session),remaining)
        root=tree.get('frameTree',{});frame=root.get('frame',{})
        def frame_ids(node):
            ids={node.get('frame',{}).get('id')}
            for child in node.get('childFrames',[]):ids|=frame_ids(child)
            return ids
        if (frame.get('id')==main.frame_id and frame.get('loaderId')==main.loader_id
                and owner.frame_id not in frame_ids(root) and self.primary_observable() and self.session_observable(main.session)):
            owner.frame_retired=True;owner.gone.set()
            self.record('CHILD_DOCUMENT_RETIRED',session=owner.session,
                guard={'proof':'NEW_PRIMARY_DOCUMENT_FRAME_ABSENT','document_generation':main.document_generation})

    async def settle_detached(self,owner):
        deadline=monotonic()+self.detach_grace_seconds
        while not (owner.destroyed or owner.frame_retired) and not self.failed and not self.closing:
            self.lifecycle_changed.clear()
            try:
                await self.prove_frame_retirement(owner,deadline)
                if owner.destroyed or owner.frame_retired:break
                await asyncio.wait_for(self.lifecycle_changed.wait(),max(.001,deadline-monotonic()))
            except TimeoutError:break
            except Exception as error:
                self.fail_closed('TARGET_RETIREMENT',error,code='DOCUMENT_PROTECTION_UNCERTAIN');return False
        if not (owner.destroyed or owner.frame_retired):
            self.fail_closed('TARGET_DETACH',code='DOCUMENT_PROTECTION_UNCERTAIN',category='CHILD_RETIREMENT_UNPROVEN');return False
        self.retire_destroyed_requests(owner)
        def live_descendants():
            return any(o.attached and o.parent==owner.session and o.parent_generation==owner.generation for o in self.session_states.values())
        while (self.paused_requests or live_descendants()) and monotonic()<deadline and not self.failed:await asyncio.sleep(.005)
        parent=self.session_states.get(owner.parent)
        safe=bool(owner.parent_verified and parent and parent.generation==owner.parent_generation
            and self.parent_is_protected(parent) and not self.paused_requests and not live_descendants())
        if not safe:
            self.fail_closed('TARGET_DETACH',code='DOCUMENT_PROTECTION_UNCERTAIN',category='CHILD_PROTECTION_UNPROVEN');return False
        owner.completed=True
        self.record('CHILD_TARGET_DETACHED',session=owner.session,primary_observable=True,
            pending_documents=0,completion='DESTROYED_CHILD_GENERATION' if owner.destroyed else 'RETIRED_CHILD_DOCUMENT',code='CHILD_TARGET_DETACHED')
        return True

    def retire_destroyed_requests(self,owner):
        if not (owner.destroyed or owner.frame_retired):return
        for key,item in list(self.paused_requests.items()):
            if key[0]==owner.session:
                self.record('REQUEST_COMPLETED',session=owner.session,request=item['alias'],
                    completion='TARGET_DESTROYED' if owner.destroyed else 'DOCUMENT_RETIRED',code='CHILD_TARGET_DETACHED')
                self.paused_requests.pop(key,None)

    async def detached_completion(self,owner):
        if not owner or owner.attached:return False
        if owner.settling:
            safe=await asyncio.shield(owner.settling)
            if safe:self.retire_destroyed_requests(owner)
            return safe
        return False

    def network_event(self,event):
        """Independent document lifecycle only; never retain headers/body/URL."""
        p=event.get('params',{});session=event.get('sessionId');key=(session,p.get('requestId'))
        owner=self.session_states.get(session);method=event['method']
        if not owner or not owner.attached:return
        if method=='Network.requestWillBeSent' and p.get('type')=='Document':
            self.network_serial+=1
            self.document_requests.pop(key,None)
            self.document_requests[key]={'alias':self.network_serial,'generation':owner.generation,
                'frame':p.get('frameId'),'responded':False,'terminal':None,'cancelled':False}
            while len(self.document_requests)>256:self.document_requests.pop(next(iter(self.document_requests)))
        elif key in self.document_requests:
            item=self.document_requests[key]
            if item['generation']!=owner.generation:return
            if method=='Network.responseReceived':item['responded']=True
            elif method in {'Network.loadingFinished','Network.loadingFailed'}:
                item.update(terminal=method,cancelled=method=='Network.loadingFailed' and p.get('canceled') is True)
                self.record('DOCUMENT_REQUEST_TERMINAL',session=session,network_request=item['alias'],
                    completion='CANCELLED' if item['cancelled'] else 'FINISHED' if method=='Network.loadingFinished' else 'FAILED',
                    response_observed=item['responded'])
                self.lifecycle_changed.set()

    async def prove_cancelled_request(self,session,item):
        """No resend: recover only a denied child whose exact network load ended."""
        deadline=monotonic()+.25
        while True:
            self.lifecycle_changed.clear()
            owner=self.session_states.get(session);network=item.get('network_record')
            if (item['scope']!='subframe' or not item.get('document_request') or not owner or owner.generation!=item.get('generation')
                or not self.session_observable(session) or not self.primary_observable()
                or not self.parent_is_protected(owner) or not network
                or self.document_requests.get((session,item.get('network_id'))) is not network
                or network['generation']!=owner.generation or not item.get('frame_id')
                or network['frame']!=item['frame_id'] or network['responded']):return False
            if network['terminal'] is not None:
                return network['terminal']=='Network.loadingFailed' and network['cancelled'] is True
            remaining=deadline-monotonic()
            if remaining<=0:return False
            try:await asyncio.wait_for(self.lifecycle_changed.wait(),remaining)
            except TimeoutError:return False

    def register_request(self,event):
        p=event['params'];session=event.get('sessionId');key=(session,p['requestId'])
        if key in self.paused_requests:return self.paused_requests[key]
        self.request_aliases.setdefault(key,len(self.request_aliases)+1)
        owner=self.session_states.get(session)
        network=self.document_requests.get((session,p.get('networkId')))
        frame=p.get('frameId')
        if frame:self.frame_aliases.setdefault(frame,len(self.frame_aliases)+1)
        item={'alias':self.request_aliases[key],'scope':self.document_scope(session,frame),
            'frame_id':frame,'generation':owner.generation if owner else None,
            'network_id':p.get('networkId'),'network_record':network,
            'document_request':p.get('resourceType')=='Document'}
        self.paused_requests[key]=item
        self.record('REQUEST_PAUSED',session=session,request=item['alias'],scope=item['scope'],
            kind=owner.kind if owner else 'unknown',resource='Document' if p.get('resourceType')=='Document' else 'other',
            redirect=bool(p.get('redirectedRequestId')),primary_observable=self.primary_observable(),
            frame=self.frame_aliases.get(frame),network_request=network['alias'] if network else None,
            request_id_sha256=hashlib.sha256(p['requestId'].encode()).hexdigest())
        if item['scope']=='top_level' and p.get('resourceType')=='Document':
            self.notify_navigation('DOCUMENT_REQUEST',session,p.get('request',{}).get('url',''),redirect=bool(p.get('redirectedRequestId')))
        return item

    async def handle_request(self,event):
        p=event['params'];session=event.get('sessionId');key=(session,p['requestId'])
        item=self.register_request(event);owner=self.session_states.get(session)
        if owner and not owner.attached:
            if await self.detached_completion(owner):return
            raise SessionDetached('Paused document belongs to unavailable generation')
        url=p['request']['url'];allowed=self.policy.permits(url)
        if owner is None and not self.failed:
            self.fail_closed('DOCUMENT_INTERCEPTION',code='PRIMARY_TARGET_UNAVAILABLE')
        reason='GUARD_FAILED' if self.failed else 'ORIGIN_APPROVED' if allowed else 'DOCUMENT_BLOCKED_BY_POLICY'
        decision='Fetch.continueRequest' if allowed and not self.failed else 'Fetch.failRequest'
        if not allowed:
            host=urlsplit(url).hostname or 'unknown';self.blocked_hosts.add(host)
            record={'host':host,'scope':item['scope']}
            if record not in self.blocked_documents:self.blocked_documents.append(record)
        self.record('REQUEST_DECISION',session=session,request=item['alias'],scope=item['scope'],
            kind=owner.kind if owner else 'unknown',resource='Document' if p.get('resourceType')=='Document' else 'other',
            policy='ALLOW' if allowed else 'DENY',command=decision,code=reason,
            primary_observable=self.primary_observable())
        try:
            await self.send(decision,{'requestId':p['requestId'],**({'errorReason':'BlockedByClient'} if decision=='Fetch.failRequest' else {})},session)
            self.paused_requests.pop(key,None)
            if decision=='Fetch.continueRequest':
                self.last_document_decision={'request':item['alias'],'scope':item['scope'],
                    'session':self.session_aliases.get(session),'generation':owner.generation if owner else None,
                    'elapsed_ms':round((monotonic()-self.started)*1000,1)}
            self.record('REQUEST_COMPLETED',session=session,request=item['alias'],command=decision,
                completion='CONTINUED' if decision=='Fetch.continueRequest' else 'CLIENT_BLOCKED')
        except SessionDetached:
            if await self.detached_completion(owner):return
            raise
        except GuardProtocolError as error:
            if (decision=='Fetch.failRequest' and error.protocol_code==-32602
                and error.protocol_reason=='INVALID_INTERCEPTION_ID'
                and await self.prove_cancelled_request(session,item)):
                self.paused_requests.pop(key,None)
                self.record('REQUEST_COMPLETED',session=session,request=item['alias'],command=decision,
                    network_request=item['network_record']['alias'],completion='CANCELLED_NETWORK_PROVEN',
                    code='REQUEST_CANCELLATION_INDEPENDENTLY_CONFIRMED',**protocol_diagnostic(error))
                return
            raise

    async def _event(self,event):
        owner=None
        try:
            p=event['params']
            if event['method']=='Fetch.requestPaused':
                await self.handle_request(event);return
            if self.failed:return
            owner=self.register_attachment(event);session=owner.session
            if not owner.attached:
                if await self.detached_completion(owner):return
                raise SessionDetached('Target detached before setup')
            if owner.kind not in {'page','iframe'}:raise RuntimeError('Unexpected target type')
            self.session_frames[session]=(owner.kind,None)
            await self.send('Fetch.enable',{'patterns':[{'urlPattern':'*','resourceType':'Document','requestStage':'Request'}]},session)
            await self.send('Network.enable',session=session)
            owner.intercepting=True
            self.lifecycle_changed.set()
            await self.send('Target.setAutoAttach',auto_attach_settings(),session)
            await self.send('Runtime.runIfWaitingForDebugger',session=session)
            if owner.kind in {'page','iframe'}:
                await self.send('Page.enable',session=session)
                tree=await self.send('Page.getFrameTree',session=session)
                root=tree.get('frameTree',{}).get('frame',{}).get('id')
                if not root:raise RuntimeError('Protected page frame identity unavailable')
                self.session_frames[session]=(owner.kind,root)
                self.remember_tree(tree.get('frameTree',{}),session)
                owner.frame_id=root
                loader=tree.get('frameTree',{}).get('frame',{}).get('loaderId')
                if loader and loader!=owner.loader_id:
                    owner.loader_id=loader;owner.document_generation+=1
            if not owner.attached:raise SessionDetached('Target disappeared during setup')
            owner.protected=True
            self.sessions.add(owner.target)
            if owner.kind=='page':self.page_targets.add(owner.target)
            self.lifecycle_changed.set()
            self.record('TARGET_PROTECTED',session=session,kind=owner.kind,primary_observable=self.primary_observable())
        except SessionDetached as error:
            owner=owner or self.session_states.get(event.get('sessionId'))
            if await self.detached_completion(owner):return
            if not self.closing:self.fail_closed('TARGET_ATTACH' if owner else 'DOCUMENT_INTERCEPTION',error,code='PRIMARY_TARGET_UNAVAILABLE')
        except Exception as error:
            if not self.closing:self.fail_closed('TARGET_ATTACH' if event.get('method')=='Target.attachedToTarget' else 'DOCUMENT_INTERCEPTION',error,code='PROTOCOL_COMMAND_FAILED')

    async def close(self):
        self.closing = True
        self.lifecycle_changed.set()
        if self.reader:
            self.reader.cancel()
            await asyncio.gather(self.reader, return_exceptions=True)
        for task in list(self.tasks):
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        for future in self.pending.values():
            if not future.done():
                future.cancel()
        self.pending.clear()
        if self.ws:
            await self.ws.close()
