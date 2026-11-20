"""Passive evidence inventory: bounded DOM reads and completed, observed GET bodies.

Bodies are reduced immediately to key names and exact-match booleans in memory.
No HTTP request is issued, no private response value is persisted, no hint is approval.
"""
import asyncio
import base64
from collections import Counter
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit
from uuid import uuid4
from app.safety import require_live_browser
from modules.login.evidence import EvidenceConfig
from browser.evidence_diagnostics import EvidenceFailure
from modules.login.discovery import DOM_DISCOVERY

AUTH_NAMES={'authenticated','isauthenticated','is_authenticated','logged_in','loggedin','isloggedin','is_logged_in'}
AUTH_REVIEW_FIELDS=AUTH_NAMES|{'status','state','success','code','result'}
AUTH_REVIEW_CONTAINERS={'session','auth','authentication','context','data','result','response','user','account','tenant'}
AUTH_REVIEW_LABELS={'active','inactive','authenticated','unauthenticated','logged_in','logged_out',
    'ok','success','error','failed','expired','denied'}
NEGATIVE_AUTH_LABELS={'inactive','unauthenticated','logged_out','error','failed','expired','denied','false'}


def identity_facts(data,account):
    """Bounded reduction; only key paths and exact comparison outcomes survive."""
    auth=[];tenant=[];identity_hashes={};scalar_hashes={};scalar_display={};negative_auth=[];stack=[(data,[],0)];nodes=0;limited=False;limit_reason=None
    structure={'root_type':'OBJECT' if isinstance(data,dict) else 'ARRAY' if isinstance(data,list) else 'SCALAR','objects':0,'arrays':0,'scalar_fields':0,'sensitive_fields_skipped':0,'array_limit_reached':False}
    array_paths=[];array_contexts={}
    while stack:
        obj,path,depth=stack.pop();nodes+=1
        if nodes>4096 or depth>8:
            limited=True;limit_reason='NODE_LIMIT' if nodes>4096 else 'DEPTH_LIMIT';break
        if isinstance(obj,list):
            structure['arrays']+=1
            array_paths.append(path)
            if len(obj)>32:
                structure['array_limit_reached']=limited=True;limit_reason='ARRAY_MEMBER_LIMIT';break
            for index,value in reversed(list(enumerate(obj))):
                if isinstance(value,(dict,list)):stack.append((value,path+[str(index)],depth+1))
            continue
        if not isinstance(obj,dict):continue
        structure['objects']+=1
        for key,value in obj.items():
            nodes+=1
            if nodes>4096:limited=True;limit_reason='NODE_LIMIT';break
            if not isinstance(key,str) or len(key)>80:continue
            if any(part in key.lower() for part in ('password','token','secret','cookie','authorization','credential')):
                structure['sensitive_fields_skipped']+=1;continue
            if not isinstance(value,(dict,list)):structure['scalar_fields']+=1
            parts=path+[key]
            pointer='/'+'/'.join(k.replace('~','~0').replace('/','~1') for k in parts)
            reference=key if not path else pointer
            if len(reference)>80:continue
            array_contexts[reference]=['/'+'/'.join(parts[:len(prefix)+1]) for prefix in array_paths
                if parts[:len(prefix)]==prefix and len(parts)>len(prefix) and parts[len(prefix)].isdigit()]
            if key.lower() in AUTH_NAMES and value is True:auth.append(reference)
            if key.lower() in AUTH_REVIEW_FIELDS and all(p.isdigit() or p.lower() in AUTH_REVIEW_CONTAINERS for p in path):
                if ((type(value) is str and value in NEGATIVE_AUTH_LABELS) or
                        (key.lower() in AUTH_NAMES|{'success'} and (value is False or type(value) is int and value==0))):
                    negative_auth.append(reference)
            if (key.lower() in AUTH_REVIEW_FIELDS and all(p.isdigit() or p.lower() in AUTH_REVIEW_CONTAINERS for p in path)
                    and type(value) in (bool,int,str) and len(str(value))<=256):
                scalar_hashes[reference]=hashlib.sha256(json.dumps(value,ensure_ascii=True,separators=(',',':')).encode()).hexdigest()
                # Only neutral status labels and tiny scalar flags may be shown
                # during explicit operator review. Arbitrary values stay private.
                if (type(value) is str and value in AUTH_REVIEW_LABELS) or type(value) is bool or (type(value) is int and value in {0,1}):
                    scalar_display[reference]=value
            if isinstance(value,str) and any(t in key.lower() for t in ('tenant','account','company','organization','organisation')):identity_hashes[reference]=hashlib.sha256(value.encode()).digest()
            if any(t in key.lower() for t in ('tenant','account','company','organization','organisation')) and isinstance(value,str) and value==account:tenant.append(reference)
            if isinstance(value,(dict,list)):stack.append((value,parts,depth+1))
    return {'structure':structure,'array_contexts':{} if limited else array_contexts,'auth_keys':[] if limited else auth,'tenant_keys':[] if limited else tenant,'traversal_limit_reached':limited,'traversal_limit_reason':limit_reason,'identity_hashes':{} if limited else identity_hashes,'scalar_hashes':{} if limited else scalar_hashes,'scalar_display':{} if limited else scalar_display,'negative_authentication_keys':[] if limited else negative_auth}


def resource_pair_supported(facts,authentication_key,tenant_key):
    """Never combine authentication and tenant identities across array members."""
    contexts=facts.get('array_contexts',{})
    return contexts.get(authentication_key,[])==contexts.get(tenant_key,[])


def timestamp():
    return datetime.now(timezone.utc).isoformat()


async def login_page_context(browser):
    """Paired current SDK/CDP context facts; no URL, target ID or title export."""
    guard=browser.guard
    def identity():
        owner=guard.session_states.get(guard.primary_session) if guard else None
        if not owner or not guard.primary_observable() or guard.blocking_hosts:return None
        return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id)
    result={'state':'LOGIN_CONTEXT_READ','primary_document_coherent':False,'sdk_page_active':False}
    try:
        await guard.ensure_observable();before=identity()
        if before is None:raise EvidenceFailure('LOGIN_CONTEXT_UNAVAILABLE')
        first=await browser.page.url()
        tree=await guard.send('Page.getFrameTree',session=before[1]);frame=tree.get('frameTree',{}).get('frame',{})
        from modules.whatsapp.destination import cdp_frame_url
        frame_url=cdp_frame_url(frame)  # Existing helper handles separately reported SPA fragments.
        active=await browser.browser.context.active_page();last=await browser.page.url()
        result.update(expected_host=urlsplit(last).hostname==browser.config.expected_host,
            sdk_page_active=bool(active and active.page_id==browser.page.page_id),
            primary_session_alias=guard.session_aliases.get(before[1]),primary_target_alias=guard.target_aliases.get(before[0]),
            generation=before[2],document_generation=before[3],top_level_frame=True,
            page_path_sha256=hashlib.sha256(urlsplit(last).path.encode()).hexdigest())
        result['primary_document_coherent']=bool(before==identity() and first==last==frame_url and
            frame.get('id')==before[4] and (frame.get('loaderId') or None)==before[5] and
            browser.policy.permits(last) and result['sdk_page_active'])
    except Exception:result['code']='LOGIN_CONTEXT_UNAVAILABLE'
    browser.trace.lifecycle('LOGIN_CONTEXT',result)
    return result


async def certify_custom_host_scope(browser,snapshot,expression):
    """Resolve a JS uncertainty using the existing bounded protected inventory.

    A hyphenated element is not evidence of a closed shadow root. Only a complete,
    complete two-pass CDP inventory may discharge that uncertainty; unrelated
    mutations require stable positive scoped predicates. All inaccessible roots,
    genuine gaps and security negatives remain blockers. No certificate is reused.
    """
    coverage=snapshot.get('_scope_diagnostics',snapshot)
    proof={'state':'LOGIN_SCOPE_NOT_REQUIRED','certified':False,'inventory_requested':False}
    if not coverage.get('inaccessible_or_unknown_hosts'):return snapshot,proof
    from browser.metadata_channel import metadata_channel
    from browser.shadow_inventory import shadow_inventory
    from modules.login.negative_scan import NEGATIVE_SNAPSHOT
    guard=browser.guard;latest=snapshot;negative=None
    def identity():
        owner=guard.session_states.get(guard.primary_session)
        return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id) if owner and guard.primary_observable() and not guard.blocking_hosts else None
    before=identity();proof.update(state='LOGIN_SCOPE_UNAVAILABLE',inventory_requested=True)
    try:
        if before is None:raise EvidenceFailure('LOGIN_CONTEXT_UNAVAILABLE')
        browser.budget()
        async def observe():
            nonlocal latest,negative
            if before!=identity():raise EvidenceFailure('LOGIN_DOCUMENT_CHANGED')
            latest=await browser.page.evaluate(expression)
            negative=await browser.page.evaluate(NEGATIVE_SNAPSHOT.replace('SETTINGS',json.dumps({'origins':sorted(browser.policy.origins)})))
        async with metadata_channel(browser,before[1]) as reader:
            optional={}
            if getattr(browser,'rendering_diagnostics_enabled',False):
                from browser.diagnostics import closed_host_render_state
                async def observe_host(backend):return await closed_host_render_state(browser,backend)
                optional['host_observer']=observe_host
            inventory=await shadow_inventory(reader,before[1],browser.policy.origins,observe=observe,primary_frame=before[4],**optional)
        current=latest.get('_scope_diagnostics',latest)
        relevant_keys=('dashboard','tenant_matches','mismatch')
        relevant_stable=all(type(snapshot.get(k)) is bool and snapshot.get(k)==latest.get(k) for k in relevant_keys)
        relevant_stable=bool(relevant_stable and snapshot.get('dashboard') and snapshot.get('tenant_matches') and not snapshot.get('mismatch'))
        limits=any(v is True for k,v in inventory.items() if k not in {'inventory_complete','inventory_changed'} and isinstance(v,bool))
        if inventory.get('inventory_changed') and not relevant_stable:limits=True
        gaps=any(current.get(k) for k in ('permitted_frames_unavailable','document_limit_reached','node_limit_reached','shadow_roots_uninspected','shadow_limit_reached'))
        flags=(negative or {}).get('flags',{})
        negatives={k:flags.get(k) for k in ('credential_form','challenge','rejected','expired','account_restricted')}
        proof.update(inventory,relevant_evidence_stable=relevant_stable,negative_flags=negatives,negative_scope_complete=flags.get('negative_scope_complete'),
            document_current=before is not None and before==identity())
        proof['certified']=bool(proof['document_current'] and inventory.get('inventory_complete') is True and
            inventory.get('inventory_passes')==2 and not limits and not gaps and
            inventory.get('inaccessible_shadow_roots')==0 and inventory.get('unknown_shadow_roots')==0 and
            inventory.get('inventory_open_shadow_roots')==current.get('open_shadow_roots_inspected') and
            flags.get('negative_scope_complete') is True and all(value is False for value in negatives.values()))
        proof['state']='LOGIN_SCOPE_CERTIFIED' if proof['certified'] else 'LOGIN_SCOPE_UNAVAILABLE'
    except Exception:proof['code']='PROTECTED_SCOPE_CERTIFICATE_UNAVAILABLE'
    browser.trace.lifecycle('LOGIN_SCOPE',proof)
    return latest,proof


class ReadOnlyDiscovery:
    def __init__(self,browser,*,targeted_capture=False):
        self.targeted_capture=targeted_capture
        self.browser=browser;self.requests={};self.resources=[];self.enabled=False;self.channel=None
        self.tasks=set();self.closed=False;self.serial=0;self.latest_requests={}
        self.slots=asyncio.Semaphore(4)
        self.sample_count=3;self.sample_interval=.35
        self.navigation_epoch=0
        self.context_changed=asyncio.Event()
        self.navigation_listener=None;self.previous_navigation_listener=None
        self.contract_choices=[];self.contract_choices_digest=None
        self._operator_candidate_refs={}
        self._operator_candidate_summaries={}
        self._operator_candidate_context=None
        self._operator_candidate_error=None

    def eligible(self,url):
        parsed=urlsplit(url)
        return (parsed.hostname==self.browser.config.expected_host and not parsed.fragment and not parsed.username and not parsed.password
            and self.browser.policy.permits(url))

    def observe_network(self,event):
        if self.closed:return
        p=event.get('params',{});session=event.get('sessionId');key=(session,p.get('requestId'))
        method=event.get('method')
        if method=='Target.detachedFromTarget':
            detached=p.get('sessionId')
            for item in self.resources:
                if item['session']==detached:item['detached']=True
        elif method=='Network.requestWillBeSent':
            # Redirects reuse request IDs: invalidate previous ownership/eligibility first.
            self.requests.pop(key,None)
            request=p.get('request',{});url=request.get('url','')
            if session and key[1] and request.get('method')=='GET' and self.eligible(url):
                while len(self.requests)>=128:
                    dropped=self.requests.pop(next(iter(self.requests)))
                    dropped['state']='REQUEST_TRACKING_LIMIT'
                self.serial+=1
                self.requests[key]={'url':url,'path':(urlsplit(url).path or '/')+('?' + urlsplit(url).query if urlsplit(url).query else ''), 'session':session,
                    'navigation_epoch':self.navigation_epoch,
                    'request_id':key[1],'loader':p.get('loaderId'),'frame':p.get('frameId'),
                    'alias':self.serial,'observed_at':timestamp(),'started':monotonic(),'state':'WAITING_RESPONSE'}
                path=self.requests[key]['path']
                self.latest_requests.pop(path,None)
                self.latest_requests[path]=self.serial
                while len(self.latest_requests)>128:self.latest_requests.pop(next(iter(self.latest_requests)))
        elif method=='Network.responseReceived' and key in self.requests:
            response=p.get('response',{});item=self.requests[key]
            final_url=response.get('url',item['url'])
            if not self.eligible(final_url) or final_url!=item['url']:
                self.requests.pop(key,None);return
            if response.get('status')==200 and 'json' in response.get('mimeType','').lower():
                item['state']='WAITING_LOADING_FINISHED';item['response_at']=timestamp()
                item['cache_source']='SERVICE_WORKER' if response.get('fromServiceWorker') else 'DISK' if response.get('fromDiskCache') else 'NETWORK_OR_MEMORY'
                if item not in self.resources:
                    self.resources.append(item)
                    if len(self.resources)>32:self.resources.pop(0)
        elif method=='Network.loadingFailed' and key in self.requests:
            item=self.requests.pop(key);item['state']='LOADING_FAILED'
        elif method=='Network.loadingFinished' and key in self.requests:
            item=self.requests.pop(key)
            if item['state']!='WAITING_LOADING_FINISHED':return
            item['finished']=monotonic()
            if p.get('encodedDataLength',0)>262144:item['state']='BODY_TOO_LARGE';return
            if len(self.tasks)>=8:item['state']='CAPTURE_LIMIT_REACHED';return
            item['state']='CAPTURE_PENDING'
            task=asyncio.create_task(self.capture(item));self.tasks.add(task);task.add_done_callback(self.tasks.discard)

    async def capture(self,item):
        try:
            async with self.slots:
                active=getattr(self.channel,'active_sessions',set())
                if item.get('detached') or item['session'] not in active:
                    item['state']='SESSION_UNAVAILABLE';return
                if self.closed or not self.channel or self.channel.failure:
                    item['state']='TRANSPORT_UNAVAILABLE';return
                response=await self.channel.send('Network.getResponseBody',{'requestId':item['request_id']},item['session'])
                body=response.get('body','')
                if len(body)>350000:item['state']='BODY_TOO_LARGE';return
                if response.get('base64Encoded'):body=base64.b64decode(body,validate=True).decode('utf-8')
                if len(body.encode('utf-8'))>262144:item['state']='BODY_TOO_LARGE';return
                data=json.loads(body)
                item['structure']={'root_type':'OBJECT' if isinstance(data,dict) else 'ARRAY' if isinstance(data,list) else 'SCALAR'}
                if not isinstance(data,(dict,list)):item['state']='JSON_OBJECT_REQUIRED';return
                expected=self.browser.expected_account
                if expected is None:item['state']='EXPECTED_ACCOUNT_UNAVAILABLE';return
                account=expected.get_secret_value()
                if self.browser.evidence:
                    from modules.login.evidence import reviewed_account
                    account=reviewed_account(self.browser.evidence,account)
                item['facts']=identity_facts(data,account)
                if self.targeted_capture:
                    from browser.login_signal_capture import inspect_identity_branches, safe_request_locator

                    item['targeted_capture'] = {'request': safe_request_locator(item['path']),
                                                **inspect_identity_branches(data, account)}
                auth=item['facts']['auth_keys'];tenant=item['facts']['tenant_keys']
                item['support']=('NEGATIVE_AUTHENTICATION_RESOURCE' if item['facts']['negative_authentication_keys'] else 'AUTHENTICATED_TRUE_MISSING' if not auth else 'EXACT_TENANT_MISSING' if not tenant else
                    'AMBIGUOUS_INDICATOR_KEYS' if len(auth)!=1 or len(tenant)!=1 else 'EXPLICIT_AUTH_AND_EXACT_TENANT')
                item['account_context']=hashlib.sha256(expected.get_secret_value().encode('utf-8')).digest()
                item['captured']=monotonic();item['captured_at']=timestamp();item['state']='CAPTURED'
        except asyncio.CancelledError:
            item['state']='CAPTURE_CANCELLED';raise
        except Exception as error:
            item['state']=getattr(error,'category','BODY_TIMEOUT' if isinstance(error,TimeoutError) else
                'BODY_INVALID_JSON_OR_ENCODING' if isinstance(error,(ValueError,UnicodeError)) else 'BODY_CAPTURE_FAILED')
        finally:
            self.browser.trace.lifecycle('DISCOVERY_RESOURCE',{'state':item['state'],'resource':item['alias'],
                'duration_ms':round(1000*(monotonic()-item.get('finished',item['started'])),1),
                'cache_source':item.get('cache_source','UNKNOWN')})

    async def start(self):
        self.authorize()
        guard=self.browser.guard
        self.previous_navigation_listener=guard.navigation_observer
        def navigation(kind,session,url,**kwargs):
            if session==guard.primary_session and kind in {'SAME_DOCUMENT_NAVIGATION','DOCUMENT_COMMITTED'}:
                self.navigation_epoch+=1
                self.context_changed.set()
            if self.previous_navigation_listener:
                self.previous_navigation_listener(kind,session,url,**kwargs)
        self.navigation_listener=navigation;guard.navigation_observer=navigation
        from browser.readonly_cdp import ReadOnlyCDP
        self.channel=ReadOnlyCDP(self.observe_network,diagnostic=self.browser.trace.lifecycle)
        await self.channel.start(self.browser.browser.cdp_url,self.browser.guard.page_targets)
        self.enabled=True

    def authorize(self):
        if self.browser.fixture:
            from app.safety import require_fixture_browser
            require_fixture_browser(self.browser)
        else:require_live_browser(self.browser)

    async def close(self):
        self.closed=True
        guard=self.browser.guard
        if self.navigation_listener is not None and guard.navigation_observer is self.navigation_listener:
            guard.navigation_observer=self.previous_navigation_listener
        for task in tuple(self.tasks):task.cancel()
        await asyncio.gather(*tuple(self.tasks),return_exceptions=True)
        if self.channel:await self.channel.close()
        self.requests.clear();self.resources.clear();self.latest_requests.clear()

    async def protected(self,consume=True):
        guard=self.browser.guard
        if hasattr(guard,'ensure_observable'):await guard.ensure_observable()
        url=await self.browser.page.url()
        if not self.eligible(url) or guard.failed or guard.blocking_hosts:
            # Page queries/fragments are harmless to DOM inspection; resource queries are not.
            parsed=urlsplit(url)
            if not self.browser.policy.permits(url) or parsed.hostname!=self.browser.config.expected_host or guard.failed or guard.blocking_hosts:
                raise EvidenceFailure('OBSERVATION_POLICY_BLOCKED')
        if consume:self.browser.budget()

    def current_resource(self,item):
        if not self.channel or not self.channel.ready or self.channel.failure:return 'DISCOVERY_TRANSPORT_UNAVAILABLE'
        guard=self.browser.guard
        owner=getattr(guard,'session_states',{}).get(getattr(guard,'primary_session',None))
        if not owner or not item.get('loader') or not item.get('frame'):
            return 'DOCUMENT_OWNERSHIP_UNPROVEN'
        if item.get('detached') or item['session'] not in getattr(self.channel,'active_sessions',set()):return 'SESSION_UNAVAILABLE'
        if self.latest_requests.get(item['path'])!=item['alias']:return 'RESOURCE_REPLACED_OR_PENDING'
        if item['loader']!=owner.loader_id or item['frame']!=owner.frame_id:return 'DIFFERENT_DOCUMENT'
        if item.get('navigation_epoch')!=self.navigation_epoch:return 'SPA_CONTEXT_CHANGED'
        expected=self.browser.expected_account
        if expected is None or item.get('account_context')!=hashlib.sha256(expected.get_secret_value().encode('utf-8')).digest():return 'ACCOUNT_CONTEXT_CHANGED'
        if monotonic()-item.get('captured',0)>120:return 'CAPTURE_EXPIRED'
        return 'CURRENT_DOCUMENT'

    def resource_diagnostics(self, operator_window_started=None):
        resource_diagnostics = []
        for item in self.resources:
            facts = item.get('facts', {})
            captured = item['state'] == 'CAPTURED'
            truncated = facts.get('traversal_limit_reached', False)
            assessed = captured and not truncated
            resource_diagnostics.append({
                'resource': item['alias'], 'state': item['state'], 'query_bearing': '?' in item['path'],
                'support': ('AUTH_TENANT_INDICATORS_UNCHECKED_TRAVERSAL_LIMIT' if truncated
                            else item.get('support', 'NO_SUPPORTED_FACTS')),
                'document_status': self.current_resource(item) if captured else 'NOT_CHECKED',
                'authenticated_indicator_count': len(facts.get('auth_keys', [])) if assessed else None,
                'exact_tenant_indicator_count': len(facts.get('tenant_keys', [])) if assessed else None,
                'json_traversal_limit_reached': truncated,
                'observed_during_operator_window': operator_window_started is not None
                    and item['started'] >= operator_window_started,
            })
        return resource_diagnostics

    def targeted_resource_diagnostics(self, operator_window_started=None):
        from browser.login_signal_capture import safe_request_locator

        return [
            {**row, **item.get('targeted_capture', {}),
             'request': safe_request_locator(item['path']),
             'legacy_traversal_limit': item.get('facts', {}).get('traversal_limit_reason'),
             'cache_source': item.get('cache_source', 'UNKNOWN')}
            for row, item in zip(self.resource_diagnostics(operator_window_started), self.resources)
        ]

    def check_resource(self,evidence):
        # No body re-read, endpoint request, reconnect or session-ID substitution.
        if not self.channel or not self.channel.ready or self.channel.failure:return False,'DISCOVERY_TRANSPORT_UNAVAILABLE'
        candidates=[r for r in self.resources if r['path']==evidence.readonly_path]
        if not candidates:return False,'READONLY_RESOURCE_NOT_OBSERVED'
        item=max(candidates,key=lambda r:r['alias'])
        if self.latest_requests.get(item['path'])!=item['alias']:return False,'RESOURCE_REPLACED_OR_PENDING'
        if item['state']!='CAPTURED':return False,item['state']
        state=self.current_resource(item)
        if state!='CURRENT_DOCUMENT':return False,state
        facts=item['facts']
        if not resource_pair_supported(facts,evidence.authenticated_json_key,evidence.tenant_json_key):
            return False,'RESOURCE_IDENTITY_CONTEXT_MISMATCH'
        if facts.get('negative_authentication_keys'):return False,'NEGATIVE_AUTHENTICATION_RESOURCE'
        if evidence.authenticated_value_sha256:
            if facts.get('scalar_hashes',{}).get(evidence.authenticated_json_key)!=evidence.authenticated_value_sha256:
                return False,'REVIEWED_AUTHENTICATION_CONTRACT_NOT_MATCHED'
        elif evidence.authenticated_json_key not in facts['auth_keys']:return False,'AUTHENTICATED_INDICATOR_MISSING'
        if evidence.identity_mapping:
            from modules.login.evidence import reviewed_account
            target=reviewed_account(evidence,self.browser.expected_account.get_secret_value())
            if facts.get('identity_hashes',{}).get(evidence.tenant_json_key)!=hashlib.sha256(target.encode()).digest():return False,'READONLY_TENANT_MISMATCH'
        elif evidence.tenant_json_key not in facts['tenant_keys']:return False,'READONLY_TENANT_MISMATCH'
        return True,'PASSIVE_AUTHENTICATED_JSON_CONFIRMED'

    @staticmethod
    def _operator_context(context):
        if not context or context.get('primary_document_coherent') is not True:return None
        keys=('primary_target_alias','primary_session_alias','generation','document_generation','page_path_sha256')
        value=tuple(context.get(key) for key in keys)
        return value if all(item is not None for item in value) else None

    @staticmethod
    def _candidate_target_key(candidate):
        if not isinstance(candidate,dict) or not isinstance(candidate.get('selector'),str):return None
        return (candidate['selector'],tuple(candidate.get('frame_selectors') or ()),
            tuple(candidate.get('shadow_selectors') or ()))

    @staticmethod
    def _candidate_sample_signature(sample):
        keys=('element_type','role','selector_eligible','frame_depth','shadow_depth',
            'potential_dashboard','potential_navigation','expected_account_match','collector_gate')
        return tuple(sample.get(key) for key in keys)+(tuple(sample.get('scope_exclusions') or ()),)

    def _store_operator_candidates(self,dom,targets,expression,page_context):
        self._operator_candidate_refs={};self._operator_candidate_summaries={}
        self._operator_candidate_context=self._operator_context(page_context)
        self._operator_candidate_error=None
        if self._operator_candidate_context is None:return
        diagnostics=dom.get('dom_diagnostics',{})
        samples=diagnostics.get('semantic_samples',[])
        samples_by_ref={sample.get('element_ref'):sample for sample in samples if isinstance(sample,dict)}
        targets_by_ref={target.get('element_ref'):target for target in targets if isinstance(target,dict)}
        snapshot=uuid4().hex

        def add(kind,sample,target,candidate=None):
            number=1+sum(record['kind']==kind for record in self._operator_candidate_refs.values())
            reference=f'candidate-{snapshot}-{kind}-{number}'
            target_key=self._candidate_target_key(target)
            summary={
                'reference':reference,
                'kind':kind,
                'visible':sample.get('visible') if sample else False,
                'element_type':sample.get('element_type','UNAVAILABLE') if sample else 'UNAVAILABLE',
                'role':sample.get('role','OTHER_OR_ABSENT') if sample else 'OTHER_OR_ABSENT',
                'collector_gate':sample.get('collector_gate','REFERENCE_NOT_CAPTURED') if sample else 'REFERENCE_NOT_CAPTURED',
                'scope':'SHADOW_OR_FRAME' if sample and (sample.get('frame_depth',0) or sample.get('shadow_depth',0)) else 'TOP_LEVEL',
                'binding_available':bool(target_key and sample and sample.get('selector_eligible') is True),
                'confidence':candidate.get('confidence') if candidate else 'OPERATOR_INSPECTION_ONLY',
            }
            self._operator_candidate_refs[reference]={
                'reference':reference,'kind':kind,'sample':dict(sample) if sample else None,
                'target':dict(target) if target else None,'target_key':target_key,
                'candidate':dict(candidate) if candidate else None,
                'expression':expression,'context':self._operator_candidate_context,
            }
            self._operator_candidate_summaries[reference]=summary

        for kind in ('dashboard','tenant','navigation'):
            candidates=dom.get(f'{kind}_candidates',[])
            for candidate in candidates:
                target_key=self._candidate_target_key(candidate)
                target=next((item for item in targets if self._candidate_target_key(item)==target_key),None)
                sample=samples_by_ref.get(target.get('element_ref')) if target else None
                add(kind,sample,target,candidate)
        for sample in samples:
            target=targets_by_ref.get(sample.get('element_ref'))
            add('sample',sample,target)

    async def operator_candidates(self,kind):
        self.authorize()
        if kind not in {'dashboard','tenant','navigation','sample'}:
            raise ValueError('Unsupported operator candidate type')
        if self._operator_candidate_context is None:
            raise EvidenceFailure(self._operator_candidate_error or 'CANDIDATE_DOCUMENT_UNVERIFIED')
        await self.protected(consume=False)
        context=await login_page_context(self.browser)
        if self._operator_context(context)!=self._operator_candidate_context:
            raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        return [dict(summary) for summary in self._operator_candidate_summaries.values()
            if summary['kind']==kind]

    async def bind_candidate(self,reference):
        self.authorize()
        if not isinstance(reference,str):
            raise EvidenceFailure('CANDIDATE_REFERENCE_UNOBSERVED')
        record=self._operator_candidate_refs.get(reference)
        if record is None:raise EvidenceFailure('CANDIDATE_REFERENCE_UNOBSERVED')
        if not record['target'] or not record['sample'] or not record['target_key']:
            raise EvidenceFailure('CANDIDATE_REFERENCE_UNRESOLVABLE')
        if record['sample'].get('visible') is not True:
            raise EvidenceFailure('CANDIDATE_NOT_VISIBLE')
        await self.protected()
        context=await login_page_context(self.browser)
        if self._operator_context(context)!=record['context']:
            raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        current=await self.browser.page.evaluate(record['expression'])
        await self.protected(consume=False)
        final_context=await login_page_context(self.browser)
        if self._operator_context(final_context)!=record['context']:
            raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        diagnostics=current.get('dom_diagnostics',{})
        sample=next((item for item in diagnostics.get('semantic_samples',[])
            if item.get('element_ref')==record['sample'].get('element_ref')),None)
        if sample is None:raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        if sample.get('visible') is not True:raise EvidenceFailure('CANDIDATE_NOT_VISIBLE')
        if self._candidate_sample_signature(sample)!=self._candidate_sample_signature(record['sample']):
            raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        target=next((item for item in current.get('_render_targets',[])
            if item.get('element_ref')==record['sample'].get('element_ref')),None)
        if self._candidate_target_key(target)!=record['target_key']:
            raise EvidenceFailure('CANDIDATE_REFERENCE_STALE')
        kind=record['kind']
        if kind!='sample' and not any(self._candidate_target_key(item)==record['target_key']
                for item in current.get(f'{kind}_candidates',[])):
            raise EvidenceFailure('CANDIDATE_CLASSIFICATION_CHANGED')
        return {
            'reference':reference,
            'kind':kind,
            'state':'CURRENT_VISIBLE_DOM_REFERENCE',
            'visible':True,
            'document_generation':final_context.get('document_generation'),
            'scope':self._operator_candidate_summaries[reference]['scope'],
            'collector_gate':sample.get('collector_gate'),
            'selector_sha256':hashlib.sha256(target['selector'].encode('utf-8')).hexdigest(),
        }

    async def prepare(self, *, calibration_only=False, operator_window_started=None):
        initial_epoch = self.navigation_epoch
        self.contract_choices=[];self.contract_choices_digest=None
        self.authorize();await self.protected(consume=False)
        browser=self.browser
        initial_page_context=await login_page_context(browser)
        initial_candidate_context=self._operator_context(initial_page_context)
        account=browser.expected_account.get_secret_value() if browser.expected_account else ''
        if browser.evidence:
            from modules.login.evidence import reviewed_account
            account=reviewed_account(browser.evidence,account)
        settings={'account':account,
            'origins':sorted(browser.policy.origins),'calibration':calibration_only,
            'targeted_capture':calibration_only and self.targeted_capture}
        expression=DOM_DISCOVERY.replace('SETTINGS',json.dumps(settings))
        dom={}
        # Three snapshots in the existing protected page, no navigation or HTTP calls.
        for sample in range(self.sample_count):
            if sample:await asyncio.sleep(self.sample_interval)
            await self.protected()
            dom=await browser.page.evaluate(expression)
        if self.tasks:await asyncio.wait(tuple(self.tasks),timeout=1)
        await self.protected(consume=False)
        page_context=await login_page_context(browser)
        dom,scope_certificate=await certify_custom_host_scope(browser,dom,expression)
        page_context=await login_page_context(browser)
        candidate_context_stable=(
            initial_candidate_context is not None and
            initial_candidate_context==self._operator_context(page_context)
        )
        if getattr(browser,'rendering_diagnostics_enabled',False):
            snapshot_hash=hashlib.sha256(json.dumps(dom.get('dom_diagnostics',{}),sort_keys=True).encode()).hexdigest()
            comparison_key=(page_context.get('document_generation'),page_context.get('page_path_sha256'),snapshot_hash)
            if getattr(self,'render_comparison_key',None)!=comparison_key and getattr(self,'render_comparison_count',0)<2:
                from browser.diagnostics import ScreenshotDiagnostics
                self.rendered_ui_comparison=await ScreenshotDiagnostics(browser).compare(dom,scope_certificate,enabled=True)
                self.render_comparison_key=comparison_key
                self.render_comparison_count=getattr(self,'render_comparison_count',0)+1
            elif getattr(self,'render_comparison_key',None)!=comparison_key:
                self.rendered_ui_comparison={**getattr(self,'rendered_ui_comparison',{}),
                    'status':'STALE_DIAGNOSTIC_NOT_CURRENT_PROOF','matches_current_dom':False}
            browser.rendered_ui_comparison=self.rendered_ui_comparison
        comparison=getattr(self,'rendered_ui_comparison',{})
        targets=dom.pop('_render_targets',[])
        available=bool(self.channel and getattr(self.channel,'ready',False) and not self.channel.failure)
        dashboards=dom.get('dashboard_candidates',[]);tenants=dom.get('tenant_candidates',[]);navigation=dom.get('navigation_candidates',[])
        shells=[d for d in dashboards if d['kind']=='APPLICATION_SHELL']
        # A unique semantic application shell is preferable to headings inside it.
        # Every candidate still requires operator review and independent proof.
        selectable_dashboards=shells if shells else dashboards
        matches=[];states=Counter();auth_count=tenant_count=0
        # Most recent observation per path wins; never pin a pre-login response by URL.
        latest={}
        for resource in self.resources:
            previous=latest.get(resource['path'])
            if previous is None or resource['alias']>previous['alias']:latest[resource['path']]=resource
        for item in self.resources:
            states[item['state']]+=1
        for item in latest.values():
            if item['state']!='CAPTURED':continue
            facts=item['facts'];auth_count+=bool(facts['auth_keys']);tenant_count+=bool(facts['tenant_keys'])
            if '?' not in item['path'] and not facts.get('negative_authentication_keys') and len(facts['auth_keys'])==len(facts['tenant_keys'])==1 and self.current_resource(item)=='CURRENT_DOCUMENT' and resource_pair_supported(facts,facts['auth_keys'][0],facts['tenant_keys'][0]):
                matches.append({'readonly_path':item['path'],'tenant_json_key':facts['tenant_keys'][0],'authenticated_json_key':facts['auth_keys'][0]})
        observed_at=timestamp()
        for candidate in dashboards+tenants+navigation:
            candidate.update(observed_at=observed_at,provenance='PROTECTED_RENDERED_DOM')
        if calibration_only:
            from urllib.parse import quote

            stable = candidate_context_stable and initial_epoch == self.navigation_epoch
            private_markers = (account, quote(account, safe='')) if account else ()
            def safe_bindings(records):
                return [record for record in records
                        if not any(marker in json.dumps(record, ensure_ascii=False) for marker in private_markers)]

            dashboards = safe_bindings([item for item in selectable_dashboards
                if item['kind'] in {'CONTENT_HEADING', 'APPLICATION_SHELL'}]) if stable else []
            tenants = safe_bindings(tenants) if stable and account else []
            resources = safe_bindings(matches) if stable and available else []
            labels = safe_bindings(dom.get('calibration_tenant_labels', [])) if stable and account else []
            resource_diagnostics = self.resource_diagnostics(operator_window_started)
            failed_checks = [
                code for passed, code in (
                    (stable, 'PROTECTED_DOCUMENT_CONTEXT_CHANGED'),
                    (available, 'PASSIVE_RESOURCE_TRANSPORT_UNAVAILABLE'),
                    (len(dashboards) == 1, 'UNIQUE_VISIBLE_DASHBOARD_MARKER_UNAVAILABLE'),
                    (len(tenants) == 1, 'UNIQUE_VISIBLE_EXACT_TENANT_MARKER_UNAVAILABLE'),
                    (len(resources) == 1, 'CURRENT_AUTHENTICATED_EXACT_TENANT_RESOURCE_UNAVAILABLE'),
                ) if not passed]
            targeted = {}
            if self.targeted_capture:
                targeted = {
                    'targeted_dom': dom.get('targeted_capture', {}) if stable else {'status': 'DOCUMENT_CHANGED'},
                    'targeted_resources': self.targeted_resource_diagnostics(operator_window_started),
                    'document_context_stable': stable,
                    'passive_transport_available': available,
                }
            return {
                'status': 'SIGNALS_UNRESOLVED' if failed_checks else 'CANDIDATES_PRESENT',
                **targeted,
                'authentication_verified': False, 'failed_checks': failed_checks,
                'page_context': page_context, 'scope_certificate': scope_certificate,
                'readiness': getattr(browser, 'login_readiness', None),
                'dom_diagnostics': dom.get('dom_diagnostics', {}),
                'resource_diagnostics': resource_diagnostics,
                'dashboard_candidates': dashboards, 'tenant_candidates': tenants,
                'calibration_tenant_labels': labels,
                'read_only_resources': resources, 'network_requests_replayed': 0,
            }
        self._store_operator_candidates(dom,targets,expression,page_context if candidate_context_stable else None)
        if not candidate_context_stable:
            current_candidate_context=self._operator_context(page_context)
            self._operator_candidate_error=(
                'CANDIDATE_REFERENCE_STALE' if initial_candidate_context is not None and current_candidate_context is not None
                else 'CANDIDATE_DOCUMENT_UNVERIFIED'
            )
        path=Path(browser.config.evidence_file);path.parent.mkdir(parents=True,exist_ok=True)
        inventory={'reviewed':False,'source_run_id':browser.trace.run_id,'observed_at':observed_at,
            'provenance':'PROTECTED_RENDERED_DOM_AND_COMPLETED_PASSIVE_GET',
            'page_context':page_context,'scope_certificate':scope_certificate,
            'dashboard_candidates':dashboards,'navigation_candidates':navigation,'tenant_candidates':tenants,
            'dom_diagnostics':dom.get('dom_diagnostics',{}),
            'rendered_ui_comparison':comparison,
            'dashboard_selectors':dom.get('dashboard_selectors',[]),'tenant_selectors':dom.get('tenant_selectors',[]),
            'observed_json_get_paths':[p for p in latest if '?' not in p], 'query_resources_observed':sum('?' in p for p in latest),'supported_json_indicators':[
                {**m,'source_resource':latest[m['readonly_path']]['alias'],
                    'captured_at':latest[m['readonly_path']].get('captured_at'),'provenance':'COMPLETED_PASSIVE_GET'} for m in matches],
            'resource_diagnostics':[{'resource':r['alias'],'state':r['state'],'observed_at':r['observed_at'],
                'structure':r.get('facts',{}).get('structure',r.get('structure',{})),'support':r.get('support','NO_SUPPORTED_FACTS'),
                'query_bearing':'?' in r['path'],'nested_identity_paths':bool(r.get('facts',{}).get('auth_keys') and any(k.startswith('/') for k in r['facts']['auth_keys'])), 'json_traversal_limit_reached':r.get('facts',{}).get('traversal_limit_reached',False),
                'authenticated_indicator_count':len(r.get('facts',{}).get('auth_keys',[])),
                'exact_tenant_indicator_count':len(r.get('facts',{}).get('tenant_keys',[])),
                'captured_at':r.get('captured_at'),'document_status':self.current_resource(r) if r['state']=='CAPTURED' else 'NOT_CHECKED'} for r in self.resources],
            'resource_contract_review_options':[{'source_resource':r['alias'],
                'scalar_keys':list(r.get('facts',{}).get('scalar_hashes',{})),
                'scalar_value_sha256':r.get('facts',{}).get('scalar_hashes',{}),
                'requires_explicit_contract_review':True} for r in self.resources if r['state']=='CAPTURED'],
            'response_bodies_saved':False,'private_values_saved':False}
        path.with_name('netcore-evidence-discovery.json').write_text(json.dumps(inventory,indent=2),encoding='utf-8')
        existing=EvidenceConfig.for_session(path,review_mode=True);written=False
        limitations=[]
        for name in ('permitted_frames_unavailable','document_limit_reached','node_limit_reached','shadow_roots_uninspected','inaccessible_or_unknown_hosts','shadow_limit_reached'):
            if name=='inaccessible_or_unknown_hosts' and scope_certificate.get('certified') is True:continue
            if dom.get(name):limitations.append(name.upper())
        if not candidate_context_stable:limitations.append('CANDIDATE_DOCUMENT_CHANGED_DURING_OBSERVATION')
        if not page_context.get('primary_document_coherent'):limitations.append('LOGIN_PAGE_CONTEXT_UNVERIFIED')
        # A private unreviewed candidate is input to verification, not its result.
        # Requiring the final custom-host certificate here is circular: scoped
        # verification needs this very candidate. Inaccessible roots still block A.
        draft_ready=available and len(selectable_dashboards)==len(tenants)==1 and not [
            gap for gap in limitations if gap!='INACCESSIBLE_OR_UNKNOWN_HOSTS']
        draft_ready=draft_ready and not any(v is True for v in scope_certificate.get('negative_flags',{}).values()) and not any(getattr(browser,'login_security_negatives',{}).values())
        if draft_ready and not (existing and existing.reviewed):
            d,t=selectable_dashboards[0],tenants[0]
            if d['kind'] in {'CONTENT_HEADING','APPLICATION_SHELL'}:
                fields=dict(dashboard_selector=d['selector'],dashboard_frame=d['frame_selectors'],
                    tenant_selector=t['selector'],tenant_frame=t['frame_selectors'],dashboard_shadow=d.get('shadow_selectors',[]),tenant_shadow=t.get('shadow_selectors',[]))
                if len(matches)==1:
                    candidate=EvidenceConfig(reviewed=False,**fields,**matches[0])
                    path.write_text(candidate.model_dump_json(indent=2),encoding='utf-8');written=True
                context=await browser.authentication_context()
                if context is not None:
                    for item in latest.values():
                        facts=item.get('facts',{})
                        if '?' in item['path'] or item['state']!='CAPTURED' or facts.get('negative_authentication_keys') or self.current_resource(item)!='CURRENT_DOCUMENT' or len(facts.get('tenant_keys',[]))!=1:continue
                        for key,value in facts.get('scalar_display',{}).items():
                            if key in facts['auth_keys'] or len(self.contract_choices)>=9:continue
                            if not resource_pair_supported(facts,key,facts['tenant_keys'][0]):continue
                            binding={**fields,'readonly_path':item['path'],'tenant_json_key':facts['tenant_keys'][0],
                                'authenticated_json_key':key,'authenticated_value_sha256':facts['scalar_hashes'][key]}
                            self.contract_choices.append({'resource':item['alias'],'context':context,
                                'binding':binding,'observed_value':value})
                    if self.contract_choices:
                        self.contract_choices_digest=hashlib.sha256(json.dumps(self.contract_choices,sort_keys=True).encode()).hexdigest()
        gaps=list(limitations)
        if not available:gaps.append('DISCOVERY_TRANSPORT_UNAVAILABLE')
        if len(selectable_dashboards)!=1:gaps.append('DASHBOARD_INDICATOR_UNRESOLVED_OR_AMBIGUOUS')
        if len(tenants)!=1:gaps.append('TENANT_INDICATOR_UNRESOLVED_OR_AMBIGUOUS')
        if len(matches)!=1:gaps.append('AUTHENTICATED_JSON_INDICATOR_UNRESOLVED_OR_AMBIGUOUS')
        if len(dashboards)==1 and dashboards[0]['kind']=='UNSCOPED_HEADING':gaps.append('DASHBOARD_CONTEXT_REQUIRES_OPERATOR_REVIEW')
        return {'status':'PENDING_OPERATOR_OBSERVATION' if gaps else 'DISCOVERED_UNREVIEWED',
            'transport_status':'AVAILABLE' if available else 'UNAVAILABLE','dashboard_candidates':len(dashboards),
            'dom_diagnostics':dom.get('dom_diagnostics',{}),'resource_diagnostics':inventory['resource_diagnostics'],
            'rendered_ui_comparison':comparison,
            'render_comparison_status':comparison.get('status','NOT_ENABLED'),
            'render_comparison_screenshot':comparison.get('screenshot_status','NOT_CAPTURED'),
            'render_comparison_ax_ignored':comparison.get('ax_ignored'),
            'tenant_candidates':len(tenants),'navigation_candidates':len(navigation),'observed_json_resources':len(self.resources),
            'candidate_type_counts':{'dashboard':dict(Counter(c['kind'] for c in dashboards)),
                'tenant':dict(Counter(c['kind'] for c in tenants)),'navigation':dict(Counter(c['kind'] for c in navigation))},
            'supported_json_resources':len(matches),'unreviewed_draft_written':written,'gaps':gaps,
            'resource_contract_options':len(self.contract_choices),
            'authenticated_boolean_resources':auth_count,'matching_tenant_resources':tenant_count,
            'cached_response_unavailable':sum(v for k,v in states.items() if k!='CAPTURED'),
            'resource_status_counts':dict(states),'documents_inspected':dom.get('documents_inspected',1),
            'resource_document_status_counts':dict(Counter(self.current_resource(r) for r in self.resources if r['state']=='CAPTURED')),
            'json_support_counts':dict(Counter(r['support'] for r in self.resources if r['state']=='CAPTURED')),
            'permitted_frames_unavailable':dom.get('permitted_frames_unavailable',0),
            'external_frames_skipped':dom.get('external_frames_skipped',0),
            'open_shadow_roots_inspected':dom.get('open_shadow_roots_inspected',0),'inaccessible_or_unknown_hosts':dom.get('inaccessible_or_unknown_hosts',0),'shadow_roots_uninspected':dom.get('shadow_roots_uninspected',0),'node_limit_reached':dom.get('node_limit_reached',False),'shadow_limit_reached':dom.get('shadow_limit_reached',False),'query_resources_observed':sum('?' in p for p in latest),'closed_root_coverage':'NOT_ESTABLISHED_BY_DOM',
            'observation_samples':self.sample_count,'confidence_limitations':limitations+['CANDIDATES_ARE_NOT_AUTHENTICATION_PROOF','TENANT_REQUIRES_EXACT_EXPECTED_ACCOUNT','RESOURCE_CONTRACT_REQUIRES_EXPLICIT_REVIEW'],
            'browser_actions_remaining':max(0,browser.config.max_actions-browser.trace.actions),
            'next_steps':['REVIEW_PRIVATE_CONTENT_HEADING_AND_TENANT_CANDIDATES','REVIEW_RESOURCE_STATE_AND_DOCUMENT_OWNERSHIP_COUNTS',
                'SELECT_ONLY_OBSERVED_INDICATORS_THEN_PREVIEW','DO_NOT_APPROVE_GAPS_OR_RESUBMIT_CREDENTIALS'],
            'page_context':page_context,'scope_certificate':scope_certificate,
            'operator_approval_required':True,'network_requests_replayed':0}
