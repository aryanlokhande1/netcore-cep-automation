"""Protected read-only Setup observation and separately authorized fixed actions."""
import asyncio
import base64
import hashlib
import json
import re
from urllib.parse import urlsplit
from uuid import uuid4
from time import monotonic
from langchain_core.tools import tool
from app.safety import require_live_browser,require_fixture_browser
from browser.observed_control import lost_observed_node,observed_node,protected_document,stagehand_locator
from modules.whatsapp.authorization import MutationGrant,confirm_scoped_mutation
from modules.whatsapp.contract import validate_capability,reference_schema,resolved_reference_action,CapabilityError,SETUP_URL,VALUES,LABELS,ORDER,FIELD_FACTS,SetupStop
from modules.whatsapp.dom import INSTALL_GUARD
from modules.whatsapp.observation import observe_setup,wait_for_setup_change
from modules.whatsapp.safety import read_safety_snapshot,safety_diagnostic
from modules.whatsapp.session import PocGrant
from modules.whatsapp.destination import classify_setup_page,validate_setup_destination,DestinationRejected,navigation_route_category,cdp_frame_url,navigation_url_difference


class FixtureGrant:
    """Application-only fixture authority; never a fabricated live handoff."""
    def __init__(self,browser):
        require_fixture_browser(browser)
        self.browser=browser;self.run_id=browser.trace.run_id
        self.login_submissions=browser.login_submissions;self.route='FIXTURE_ONLY'


class SetupTools:
    def __init__(self,browser,grant,*,authorized_mutations=False,mutation_review=None):
        if not isinstance(grant,(PocGrant,FixtureGrant)) or grant.browser is not browser or grant.run_id!=browser.trace.run_id:
            raise SetupStop('POC_GRANT_REQUIRED')
        if isinstance(grant,FixtureGrant) and not browser.fixture:raise SetupStop('FIXTURE_AUTHORITY_REQUIRED')
        self.browser=browser;self.grant=grant;self.destination=SETUP_URL
        self.authorized_mutations=authorized_mutations is True
        self.mutation_review=mutation_review or confirm_scoped_mutation
        self.references={};self.reference_keys={};self.completed=set();self.lock=asyncio.Lock()
        self.event_start=len(browser.trace.events);self.terminal_error=False
        self.final_gates=None;self.final_gates_current=False;self.navigation_count=1
        self.last_preflight_failure=None;self.safety_status=None;self.form_identity=None
        self.mutation_grant=None;self.review_attempted=False;self.mutation_denied=None
        self.risk_summary=None;self.review_evidence=None;self.phase='OBSERVATION';self.last_stop=None
        self.milestones=set();self.guard_installed=False;self.current=None;self.page_understanding=None
        self.skill_provenance=None;self.events=[];self.started=monotonic();self.action_start=browser.trace.actions
        self.field_seconds={field:0.0 for field in ORDER};self.last_verified_field=None
        self.failed_approaches={field:set() for field in ORDER};self.recovery_pending={}
        self.preparation={field:'DIRECT' for field in ORDER};self.active_approach=None;self.write_started=False
        self.browser_operations=[]
        self.observation_sequence=0;self.snapshot=None
        self.destination_ready=False
        self.navigation_evidence={'requested_entry_url':None,'final_route':'NOT_OBSERVED','destination_ready':False,'events':[]}
        self.browser.guard.navigation_observer=self.navigation_observed
        self.bound_sdk_page=browser.page if browser.setup_binding is not None else None

    def navigation_observed(self,kind,session,url,*,redirect=False):
        binding=getattr(self.browser,'setup_binding',None)
        if (binding is None or session!=binding.session or self.browser.close_started
            or self.browser.guard.closing):return
        if kind=='REQUESTED_SETUP_NAVIGATION' and url==self.destination:
            self.navigation_evidence['requested_entry_url']=self.destination
        route=navigation_route_category(url,self.destination)
        if kind in {'DOCUMENT_COMMITTED','SAME_DOCUMENT_NAVIGATION','TARGET_URL_CHANGED'}:
            self.navigation_evidence.update(final_route=route,final_route_basis='GUARD_EVENT')
        if route!='INTENDED_WHATSAPP_ROUTE':
            self.destination_ready=False;self.navigation_evidence['destination_ready']=False
        event={'kind':kind,'route':route,'http_redirect_request':bool(redirect),
            'elapsed_seconds':round(monotonic()-self.started,3),
            'setup_target_alias':self.browser.guard.target_aliases.get(binding.target),
            'setup_session_alias':self.browser.guard.session_aliases.get(binding.session)}
        if len(self.navigation_evidence['events'])<32:self.navigation_evidence['events'].append(event)
        else:self.navigation_evidence['events_truncated']=True
        self.browser.trace.lifecycle('WHATSAPP_NAVIGATION',event)

    def require_page1_ready(self):
        if not self.destination_ready:raise SetupStop('SETUP_PAGE1_FORM_NOT_READY')

    def pending_predicates(self):
        data=self.current or {};pending=[]
        if not data.get('setup_active'):pending.append('ACTIVE_SETUP_STEP_UNESTABLISHED')
        if not data.get('page_identity'):pending.append('PAGE_IDENTITY_UNESTABLISHED')
        if not data.get('form_recognized'):pending.append('FORM_NOT_LOCATED')
        elif self.form_identity is None:pending.append('ACTION_FORM_BINDING_UNESTABLISHED')
        for predicates in self.remaining_field_predicates().values():
            for predicate in predicates:
                if predicate not in pending:pending.append(predicate)
        return pending

    def remaining_field_predicates(self):
        pending={}
        for field in ORDER:
            if field in self.completed:continue
            d=(self.current or {}).get('fields',{}).get(field,{})
            predicates=[]
            if not d.get('discovered'):predicates.append('FIELD_CONTROL_UNRESOLVED')
            elif d.get('kind') not in {'TEXT_INPUT','NATIVE_SELECT','OWNED_COMBOBOX'}:predicates.append('FIELD_CONTROL_UNSUPPORTED')
            if d.get('discovered') and not d.get('ownership_verified'):predicates.append('FIELD_OWNERSHIP_UNESTABLISHED')
            if d.get('discovered') and not d.get('enabled') and not d.get('selected'):predicates.append('FIELD_DISABLED_UNVERIFIED')
            if predicates:pending[field]=predicates
        return pending

    def blocked_diagnostic(self):
        """Explain a voluntary model stop without manufacturing a tool failure."""
        if self.last_stop:return dict(self.last_stop)
        pending=self.pending_predicates()
        predicate=pending[0] if pending else 'PERMITTED_OPERATIONS_NOT_ATTEMPTED'
        return {'phase':self.phase,'reason':'SETUP_'+predicate,'failed_predicate':predicate,
            'failure_owner':'unknown','decision_source':'DOER_REPORT','pending_predicates':pending,
            'remaining_field_predicates':self.remaining_field_predicates(),
            'snapshot':self.snapshot}

    def read_only_probe_pending(self):
        return bool(not self.terminal_error and self.references and
            not any(e['tool']=='inspect_control' and e.get('result',{}).get('ok') for e in self.events))

    def execution_summary(self):
        return {'status':'SETUP_FILLED_UNSAVED' if self.final_gates_current else 'BLOCKED',
            'field_facts':{f:FIELD_FACTS[f] for f in ORDER if f in self.completed},
            'remaining_fields':[f for f in ORDER if f not in self.completed],
            'last_verified_field':self.last_verified_field,'skill_provenance':self.skill_provenance,
            'browser_actions':self.browser.trace.actions-self.action_start,
            'browser_operations':list(self.browser_operations),
            'per_field_seconds':{f:round(s,6) for f,s in self.field_seconds.items()},
            'elapsed_seconds':round(monotonic()-self.started,3),'tool_results':list(self.events),
            'failed_approaches':{f:sorted(v) for f,v in self.failed_approaches.items() if v},
            'failure':{**self.last_stop,'classification':'CODE_FIX_REQUIRED' if self.last_stop['reason']=='CODE_FIX_REQUIRED' else 'BLOCKED',
                'failure_owner':'tool' if self.last_stop['reason']=='CODE_FIX_REQUIRED' else 'unknown',
                'retry_safe':not self.terminal_error} if self.last_stop and not self.final_gates_current else None,
            'navigation':self.navigation_evidence}

    async def invoke_recorded(self,name,call,*args):
        start=monotonic();field=self.references.get(args[0],{}).get('field') if args else None
        event={'index':len(self.events),'tool':name,'field':field};self.events.append(event)
        result={}
        try:
            result=await call(*args)
            return result
        except asyncio.CancelledError:
            interrupted_write=name in {'fill_text','click_observed_control','select_observed_option'} and self.write_started
            if interrupted_write and field:self.completed.discard(field)
            result=self.failed(SetupStop('SETUP_ACTION_UNCERTAIN_NO_RETRY' if interrupted_write else 'SETUP_CANCELLED_BEFORE_WRITE'))
            raise
        except Exception as error:
            result=self.failed(error)
            return result
        finally:
            seconds=monotonic()-start
            if field:self.field_seconds[field]+=seconds
            # Public summaries only: no selectors, entered values, or opaque identities.
            event.update(seconds=round(seconds,6),result={k:result[k] for k in
                ('ok','error','terminal','retry_safe','classification','failure_owner','verified_fields','page_state','failed_predicate') if k in result})
            if isinstance(result.get('fields'),list):
                event['result']['observation']={k:result[k] for k in
                    ('snapshot','dom_form_recognized','action_form_identity_bound','page_identity','setup_active',
                    'observation_complete','identity_basis','identity_diagnostics','global_safety',
                    'pending_predicates','permitted_read_only_operations') if k in result}
                event['result']['observation']['fields']=[{k:f[k] for k in
                    ('field','kind','discovered','visible','enabled','ownership_verified','same_form','action_safe',
                    'field_action_status','matches_required_value','exact_existing_options') if k in f}
                    for f in result['fields'] if f.get('field') in ORDER]

    def recovery_check(self,field,approach):
        pending=self.recovery_pending.get(field)
        if pending and not pending['reinspected']:raise SetupStop('RECOVERY_REINSPECTION_REQUIRED')
        if approach in self.failed_approaches[field]:raise SetupStop('FAILED_APPROACH_ALREADY_TRIED')

    @classmethod
    def for_fixture(cls,browser,fixture_setup_url,*,authorized_mutations=False,mutation_review=None):
        require_fixture_browser(browser)
        value=urlsplit(fixture_setup_url)
        origin=value.scheme+'://'+value.netloc
        if origin not in browser.policy.origins or value.username or value.password or value.query or value.fragment or not value.path:
            raise SetupStop('EXACT_FIXTURE_SETUP_URL_REQUIRED')
        obj=cls(browser,FixtureGrant(browser),authorized_mutations=authorized_mutations,mutation_review=mutation_review)
        obj.destination=fixture_setup_url
        if not hasattr(browser,'task_phase'):browser.task_phase='setup'
        return obj

    def milestone(self,code,*,once=False,**data):
        if once and code in self.milestones:return
        self.milestones.add(code)
        self.browser.trace.lifecycle('WHATSAPP',{'state':code,'stage':self.phase,**data})
        if code=='SCOPED_SAFETY_STATUS':
            print(code+' global='+data['global_status']+' read_only_observation=ALLOWED',flush=True)
        else:print(code,flush=True)

    def fact(self,kind,gates=None):
        if kind in self.completed_facts():return
        self.browser.trace.lifecycle('WHATSAPP',{'state':'SETUP_OBSERVATION','code':kind,**({'gates':gates} if gates else {})})
        self.browser.trace.fact(kind)
        print('TAG_VERIFIED' if kind=='EXISTING_TAG_VERIFIED' else kind,flush=True)

    def completed_facts(self):return {f['kind'] for f in self.browser.trace.step_facts}

    async def destination_check(self,identity=None,*,probe=False):
        try:
            try:
                async with asyncio.timeout(4):
                    for attempt in range(1,4):
                        state,reason=await self.url_sample(identity,attempt,probe=probe)
                        if state is not None:
                            self.page_understanding=state
                            self.navigation_evidence.pop('failure',None)
                            return state
                        if attempt<3:await wait_for_setup_change(self.browser)
                    raise SetupStop(reason)
            except TimeoutError:raise SetupStop('SETUP_NAVIGATION_OBSERVATION_UNAVAILABLE') from None
        except DestinationRejected as error:
            self.destination_ready=False;self.form_identity=None;self.navigation_evidence['destination_ready']=False
            self.last_preflight_failure={'failed_predicates':[error.predicate],**error.diagnostic}
            self.browser.trace.lifecycle('WHATSAPP',{'state':'SETUP_DESTINATION_REJECTED','stage':self.phase,
                'code':error.code,'reason':error.predicate,**error.diagnostic})
            print('SETUP_DESTINATION_REJECTED phase='+self.phase+' predicate='+error.predicate,flush=True)
            raise
        except SetupStop as error:
            self.destination_ready=False;self.form_identity=None;self.navigation_evidence['destination_ready']=False
            self.navigation_evidence['failure']=error.code
            self.browser.trace.lifecycle('WHATSAPP_NAVIGATION',{'state':'FAILED','code':error.code,
                'final_route':self.navigation_evidence['final_route']})
            raise

    async def url_sample(self,identity,attempt,*,probe=True):
        """Bracket one protected CDP read with SDK reads; never re-navigate."""
        now=lambda:round(monotonic()-self.started,6)
        sample={'attempt':attempt,'started_at':now(),'identity_observation_supplied':identity is not None,
            'identity_observed_at':self.navigation_evidence.get('page1_observation',{}).get('observed_at') if identity is not None else None,
            'sdk_url_present':False,'cdp_url_present':False,'sdk_reread_present':False,
            'sdk_observed_at':None,'cdp_observed_at':None,'sdk_reread_at':None}
        samples=self.navigation_evidence.setdefault('url_samples',[])
        if len(samples)<32:samples.append(sample)
        else:samples[-1]=sample;self.navigation_evidence['url_samples_truncated']=True
        try:
            before=protected_document(self.browser)
            guard=self.browser.guard
            sdk_page=self.browser.page
            pinned=getattr(self.browser,'setup_page',None) or self.bound_sdk_page
            sample.update(setup_target_alias=guard.target_aliases.get(before[0]),setup_session_alias=guard.session_aliases.get(before[1]),
                target_generation=before[2],document_generation=before[3],sdk_is_source_page=sdk_page is self.browser.login_page,
                sdk_page_binding_matches=pinned is not None and sdk_page is pinned)
            if not sample['sdk_page_binding_matches']:raise SetupStop('SETUP_NAVIGATION_SDK_PAGE_BINDING_MISMATCH')
            first=await sdk_page.url();sample.update(sdk_observed_at=now(),sdk_url_present=isinstance(first,str) and bool(first))
            reply=await guard.send('Page.getFrameTree',session=before[1]);sample['cdp_observed_at']=now()
            frame=(reply.get('frameTree') or {}).get('frame') or {}
            frame_url=cdp_frame_url(frame)
            second=await sdk_page.url();sample['sdk_reread_at']=now()
            if self.browser.page is not sdk_page:
                sample['sdk_page_binding_matches']=False
                raise SetupStop('SETUP_NAVIGATION_SDK_PAGE_BINDING_MISMATCH')
            sample.update(sdk_url_present=isinstance(first,str) and bool(first),cdp_url_present=frame_url is not None,
                sdk_reread_present=isinstance(second,str) and bool(second),
                cdp_fragment_field_present='urlFragment' in frame,
                frame_id_matches=frame.get('id')==before[4],loader_matches=(frame.get('loaderId') or None)==before[5],
                protected_document_unchanged=before==protected_document(self.browser),
                sdk_route=navigation_route_category(first,self.destination) if first else 'MISSING',
                cdp_route=navigation_route_category(frame_url,self.destination) if frame_url else 'MISSING',
                sdk_reread_route=navigation_route_category(second,self.destination) if second else 'MISSING')
            self.navigation_evidence.update(final_route=sample['cdp_route'],sdk_route=sample['sdk_reread_route'],
                final_route_basis='PROTECTED_FRAME_QUERY',protected_target_verified=sample['protected_document_unchanged'] and sample['frame_id_matches'] and sample['loader_matches'])
            if not sample['protected_document_unchanged']:raise SetupStop('SETUP_NAVIGATION_DOCUMENT_NOT_COHERENT')
            if ('id' in frame and not sample['frame_id_matches'] or
                'loaderId' in frame and not sample['loader_matches']):raise SetupStop('SETUP_NAVIGATION_DOCUMENT_NOT_COHERENT')
            if 'id' not in frame or 'loaderId' not in frame or not all(sample[k] for k in ('sdk_url_present','cdp_url_present','sdk_reread_present')):
                sample['result']='MISSING_OBSERVATION'
                self.navigation_evidence['sdk_and_cdp_urls_agree']=False
                return None,'SETUP_NAVIGATION_OBSERVATION_UNAVAILABLE'
            authoritative=classify_setup_page(frame_url,self.destination,identity)
            classify_setup_page(first,self.destination);state=classify_setup_page(second,self.destination,identity)
            sample.update(sdk_before_cdp_differences=navigation_url_difference(first,frame_url),
                sdk_after_cdp_differences=navigation_url_difference(second,frame_url),
                sdk_during_sample_differences=navigation_url_difference(first,second))
            agree=not any(sample[k] for k in ('sdk_before_cdp_differences','sdk_after_cdp_differences','sdk_during_sample_differences'))
            self.navigation_evidence['sdk_and_cdp_urls_agree']=agree
            if sample['cdp_route']=='TENANT_DASHBOARD':raise SetupStop('SETUP_REDIRECTED_TO_DASHBOARD')
            if not agree:
                sample['result']='CHANGED_DURING_SAMPLE' if sample['sdk_during_sample_differences'] else 'URL_DISAGREEMENT'
                if any(sample[k]!='INTENDED_WHATSAPP_ROUTE' for k in ('sdk_route','cdp_route','sdk_reread_route')):
                    raise SetupStop('SETUP_NAVIGATION_TARGET_URL_MISMATCH')
                return None,'SETUP_NAVIGATION_TARGET_URL_MISMATCH'
            if not authoritative['route_match']:raise SetupStop('SETUP_NAVIGATION_WRONG_ROUTE')
            if not probe:validate_setup_destination(second,self.destination,identity)
            sample['result']='COHERENT'
            return state,None
        except SetupStop as error:
            sample['result']=error.code
            raise
        except TimeoutError:
            sample['result']='READ_TIMEOUT'
            raise
        finally:
            sample['completed_at']=now()
            self.browser.trace.lifecycle('WHATSAPP_NAVIGATION',{'state':'URL_SAMPLE',**sample})

    async def protected(self):
        if self.browser.fixture:require_fixture_browser(self.browser)
        else:require_live_browser(self.browser)
        if self.browser.task_phase not in {'setup','inspection'} or self.terminal_error:raise SetupStop('SETUP_PHASE_CLOSED')
        if any(e.get('tool_phase')=='denied' or e.get('tool_name')=='FORBIDDEN' for e in self.browser.trace.events[self.event_start:]):
            raise SetupStop('FORBIDDEN_TOOL_ATTEMPTED')
        await self.browser.guard.ensure_observable()
        protected_document(self.browser)
        await self.destination_check(probe=True)
        routes={'FIXTURE_ONLY'} if isinstance(self.grant,FixtureGrant) else {'SUBMITTED_ONCE_POC','EXISTING_SESSION_VERIFIED'}
        if self.browser.login_submissions!=self.grant.login_submissions or self.grant.route not in routes:
            raise SetupStop('LOGIN_HANDOFF_CHANGED')
        self.browser.budget()

    def preflight_failure(self,diagnostic):
        self.last_preflight_failure={**diagnostic,'poc_route':self.grant.route,'guard_failed':bool(self.browser.guard.failed),
            'strict_independent_authentication_reapplied':False}
        self.browser.trace.lifecycle('WHATSAPP',{'state':'SETUP_PREFLIGHT_FAILED','stage':self.phase,**self.last_preflight_failure})

    async def scan(self,*,include_inventory=False):
        await self.protected()
        before=protected_document(self.browser)
        self.safety_status=safety_diagnostic(await read_safety_snapshot(self.browser,include_inventory=include_inventory))
        code=self.safety_status['code']
        if code in {'AUTHENTICATION_OR_SECURITY_STOP','SAFETY_OBSERVATION_INVALID'}:
            self.preflight_failure(self.safety_status);raise SetupStop(code)
        # Coverage uncertainty is evidence for action authorization, not a read gate.
        self.milestone('SCOPED_SAFETY_STATUS',global_status=code or 'COMPLETE',complete_negative_coverage=code is None)
        data=await observe_setup(self.browser)
        if not isinstance(data,dict) or not isinstance(data.get('fields'),dict):raise SetupStop('SETUP_OBSERVATION_INVALID')
        self.navigation_evidence['page1_observation']={'observed_at':round(monotonic()-self.started,6),
            'page_identity':data.get('page_identity') is True,'form_recognized':data.get('form_recognized') is True,
            'setup_active':data.get('setup_active') is True,'observation_complete':data.get('observation_complete') is True}
        await self.browser.guard.ensure_observable()
        state=await self.destination_check(data,probe=True)
        if protected_document(self.browser)!=before:raise SetupStop('SETUP_DOCUMENT_CHANGED')
        self.current=data
        self.observation_sequence+=1
        self.snapshot={'observer':'observe_setup','sequence':self.observation_sequence,
            'elapsed_seconds':round(monotonic()-self.started,3)}
        for field in list(self.completed):
            d=data['fields'].get(field,{})
            if (not all(d.get(k) for k in ('selected','discovered','selector_unique','ownership_verified','same_form','visible'))
                or d.get('has_other_tags') or d.get('exact_option_count',0)>1
                or d.get('kind') not in ({'TEXT_INPUT'} if field=='campaign_name' else {'NATIVE_SELECT','OWNED_COMBOBOX'})):
                self.completed.discard(field);self.final_gates_current=False
        self.milestone('SETUP_PAGE_OBSERVED',once=True)
        self.browser.trace.lifecycle('WHATSAPP',{'state':'SETUP_PAGE_IDENTITY','stage':self.phase,
            'page_state':state['page_state'],**state['diagnostic']})
        if data.get('form_recognized') and state['page_state']=='SETUP_CONFIRMED':
            self.form_identity=await observed_node(self.browser,data['form_selector'])
            # history.replaceState does not change loader/document generation.
            # Reconfirm the protected target route after the awaited form binding.
            await self.destination_check(data,probe=True)
            self.destination_ready=True;self.navigation_evidence['destination_ready']=True
            self.milestone('SETUP_FORM_RECOGNIZED',once=True)
            self.fact('SETUP_FORM_DETECTED')
            if not isinstance(self.grant,FixtureGrant):self.fact('WHATSAPP_DESTINATION_OPENED')
        else:
            self.form_identity=None;self.destination_ready=False;self.navigation_evidence['destination_ready']=False
        return data

    async def open(self):
        if not isinstance(self.grant,FixtureGrant):
            await self.browser.open_setup_tab(self.grant)
            await self.browser.navigate(self.destination)
            await self.browser.page.wait_for_load_state('domcontentloaded',timeout=5000)
        # Read-only readiness may remain observable on the exact intended route.
        # A dashboard/other-route transition is terminal, never re-navigated.
        await self.protected()
        observed=await self.observe_page()
        if not observed.get('ok'):raise SetupStop(observed['error'])
        return observed

    async def _reference(self,field,role,selector):
        identity=await observed_node(self.browser,selector)
        key=(field,role,identity)
        ref=self.reference_keys.get(key) or uuid4().hex
        self.reference_keys[key]=ref
        self.references[ref]={'field':field,'role':role,'selector':selector,'identity':identity,'form':self.form_identity}
        return ref

    async def present(self,data):
        fields=[]
        for key in ORDER:
            d=data['fields'].get(key,{})
            item={'field':key,'label':LABELS[key],'discovered':d.get('discovered') is True,
                'kind':d.get('kind','UNRESOLVED'),'matches_required_value':d.get('selected') is True,
                'visible':d.get('visible') is True,'enabled':d.get('enabled') is True,
                'unobscured':d.get('unobscured'),'exact_existing_options':d.get('exact_option_count',0),
                'ownership_verified':d.get('ownership_verified') is True,'same_form':d.get('same_form') is True,
                'action_safe':d.get('action_safe') is True,
                'field_action_status':('CONTROL_UNRESOLVED' if not d.get('discovered') else
                    'UNSUPPORTED_CONTROL' if d.get('kind') not in {'TEXT_INPUT','NATIVE_SELECT','OWNED_COMBOBOX'} else
                    'OWNERSHIP_UNESTABLISHED' if not d.get('ownership_verified') else 'ACTION_BOUNDARY_CHECKS_REQUIRED'),
                'reference':None,'options':[]}
            if d.get('discovered') and d.get('selector_unique') and d.get('selector'):
                item['reference']=await self._reference(key,'control',d['selector'])
                if d.get('exact_option_count')==1 and d.get('option_selector') and d.get('option_selector_unique'):
                    option=await self._reference(key,'option',d['option_selector'])
                    item['options']=[{'reference':option,'exact_required_value':True,'existing_only':True}]
                if d.get('search_selector') and d.get('search_selector_unique') and d.get('search_visible'):
                    item['search_reference']=await self._reference(key,'search',d['search_selector'])
            fields.append(item)
        return {'ok':True,'scope':'SETUP_ONLY','page_state':self.page_understanding['page_state'],
            'destination_diagnostic':self.page_understanding['diagnostic'],'page_identity':bool(data.get('page_identity')),
            'form_recognized':self.form_identity is not None,'setup_active':bool(data.get('setup_active')),
            'destination_ready':self.destination_ready,'navigation':self.navigation_evidence,
            'dom_form_recognized':data.get('form_recognized') is True,
            'action_form_identity_bound':self.form_identity is not None,'form_recognized_semantics':'ACTION_FORM_IDENTITY_BOUND',
            'snapshot':self.snapshot,'pending_predicates':self.pending_predicates(),
            'remaining_field_predicates':self.remaining_field_predicates(),
            'identity_basis':data.get('identity_basis','UNREPORTED_BY_OBSERVER'),
            'identity_diagnostics':{k:v for k,v in (data.get('identity_diagnostics') or {}).items()
                if k in {'whatsapp_text_observed','setup_active','form_signature','same_form_field_count','distinct_field_controls'} and type(v) in {bool,int}},
            'permitted_read_only_operations':(['inspect_control','scroll_to_control','verify_field'] if
                any(f['reference'] for f in fields) else ['observe_page']),
            'mutation_checks':'DEFERRED_TO_ACTION_BOUNDARY; READ_ONLY_IS_NOT_A_FINAL_DENIAL',
            'observation_complete':bool(data.get('observation_complete')),'fields':fields,
            'verified_fields':sorted(self.completed),'global_safety':self.safety_status,
            'mutation_authorization':self.authorization_summary(),'server_persistence':'UNDETERMINED'}

    def authorization_summary(self):
        if self.mutation_grant:
            summary=self.mutation_grant.summary()
            summary['authorization_time_coverage_complete']=self.mutation_grant.route=='GLOBAL_COMPLETE'
            summary['complete_negative_coverage']=bool(self.safety_status and self.safety_status['code'] is None)
            summary['current_scope_requires_reauthorization']=self.mutation_grant.route=='GLOBAL_COMPLETE' and not summary['complete_negative_coverage']
            return summary
        return {'status':'READ_ONLY','reason':self.mutation_denied or 'SEPARATE_MUTATION_DECISION_REQUIRED',
            'explicit_setup_authorization':self.authorized_mutations,'complete_negative_coverage':bool(self.safety_status and self.safety_status['code'] is None),
            'independent_authentication_certified':False}

    def failed(self,error):
        defect=isinstance(error,(TypeError,AttributeError,KeyError,NotImplementedError))
        code='CODE_FIX_REQUIRED' if defect else error.code if isinstance(error,SetupStop) else 'SETUP_ACTION_UNCERTAIN_NO_RETRY'
        soft={'MUTATION_AUTHORIZATION_REQUIRED','MUTATION_REVIEW_DECLINED','MUTATION_REVIEW_TIMEOUT',
            'MUTATION_REVIEW_UNAVAILABLE','SCOPED_RISK_EVIDENCE_UNAVAILABLE','MUTATION_SCOPE_CHANGED',
            'RECOVERY_REINSPECTION_REQUIRED','FAILED_APPROACH_ALREADY_TRIED'}
        safe=(not self.write_started and self.active_approach is not None and
            code in {'SETUP_CONTROL_OBSCURED_OR_OFFSCREEN','EXACT_EXISTING_OPTION_UNAVAILABLE'})
        if safe:
            field,approach=self.active_approach
            self.failed_approaches[field].add(approach)
            self.recovery_pending[field]={'reinspected':False}
            if len(self.failed_approaches[field])>=2:code='STUCK_TWO_APPROACHES_FAILED'
            else:soft.add(code)
        if code not in soft:self.terminal_error=True
        predicate=error.predicate if isinstance(error,DestinationRejected) else code
        self.mutation_grant=None;self.last_stop={'phase':self.phase,'reason':code,'failed_predicate':predicate}
        if self.last_preflight_failure is None and self.page_understanding:
            self.last_preflight_failure={'failed_predicates':[predicate],**self.page_understanding['diagnostic']}
        if code in soft and code.startswith(('MUTATION_','SCOPED_')):self.mutation_denied=code
        self.browser.trace.add('error',error='POLICY');self.browser.trace.events[-1].update(stop_code=code,setup_phase=self.phase)
        print('SETUP_STOP phase='+self.phase+' reason='+code+' predicate='+predicate,flush=True)
        if self.phase=='AUTHORIZATION':self.milestone('SCOPED_AUTHORIZATION_RESULT',result='DENIED',reason=code)
        return {'ok':False,'error':code,'phase':self.phase,'failed_predicate':predicate,'terminal':self.terminal_error,
            'retry_safe':not self.terminal_error,'classification':'CODE_FIX_REQUIRED' if defect else 'BLOCKED',
            'failure_owner':'tool' if defect else 'unknown',
            'read_only_observation_available':not self.terminal_error,'mutation_authorization':self.authorization_summary()}

    async def observe_page(self):
        async with self.lock:
            self.phase='OBSERVATION'
            try:
                for attempt in range(3):
                    data=await self.scan()
                    if self.form_identity is not None or attempt==2:break
                    self.milestone('SETUP_REOBSERVATION_PENDING',attempt=attempt+1,**self.page_understanding['diagnostic'])
                    await wait_for_setup_change(self.browser)
                for pending in self.recovery_pending.values():pending['reinspected']=True
                return await self.present(data)
            except Exception as error:return self.failed(error)

    async def inspect(self):return await self.observe_page()

    async def _resolve(self,reference,data,roles=('control','search','option')):
        entry=self.references.get(reference)
        if not entry or entry['role'] not in roles:raise SetupStop('UNOBSERVED_SETUP_ELEMENT')
        d=data['fields'].get(entry['field'],{})
        selector=d.get({'control':'selector','search':'search_selector','option':'option_selector'}[entry['role']])
        if not d.get('discovered') or selector!=entry['selector'] or self.form_identity!=entry['form']:
            raise SetupStop('STALE_CONTROL_REFERENCE')
        if await observed_node(self.browser,selector)!=entry['identity']:raise SetupStop('STALE_CONTROL_REFERENCE')
        return entry,d

    async def inspect_control(self,reference):
        async with self.lock:
            self.phase='OBSERVATION'
            try:
                data=await self.scan();entry,_=await self._resolve(reference,data)
                validate_capability('inspect_field',field=entry['field'])
                if entry['field'] in self.recovery_pending:self.recovery_pending[entry['field']]['reinspected']=True
                return await self.present(data)
            except Exception as error:return self.failed(error)

    async def scroll_to_control(self,reference):
        async with self.lock:
            self.phase='OBSERVATION';self.active_approach=None;self.write_started=False
            try:
                data=await self.scan();self.require_page1_ready();entry,d=await self._resolve(reference,data,('control',))
                validate_capability('scroll_to_field',field=entry['field'])
                if not d.get('visible'):raise SetupStop('HIDDEN_SETUP_CONTROL')
                before=protected_document(self.browser)
                operation={'field':entry['field'],'operation':'scroll','outcome':'ATTEMPTED','verified':False}
                self.browser_operations.append(operation)
                await self.browser.guard.send('DOM.scrollIntoViewIfNeeded',{'backendNodeId':entry['identity'][-1]},before[1])
                if protected_document(self.browser)!=before:raise SetupStop('SETUP_DOCUMENT_CHANGED')
                self.preparation[entry['field']]='SCROLLED'
                result=await self.present(await self.scan())
                operation.update(outcome='RETURNED',verified=True)
                return result
            except Exception as error:return self.failed(error)

    def _form_required(self,data):
        if not self.form_identity or not all(data.get(k) for k in ('form_recognized','setup_active','page_identity','observation_complete')):
            raise SetupStop('SETUP_FORM_NOT_ESTABLISHED')

    async def _authorize(self,data,entry,d):
        self.phase='AUTHORIZATION'
        await self.destination_check(data)
        self._form_required(data)
        if not self.authorized_mutations:raise SetupStop('MUTATION_AUTHORIZATION_REQUIRED')
        if self.mutation_denied:raise SetupStop(self.mutation_denied)
        if self.browser.task_phase!='setup':raise SetupStop('SETUP_WRITES_CLOSED')
        # Full inventory is evidence for this one write boundary, never startup
        # or read-only tool admission. Refresh identity after the bounded work.
        data=await self.scan(include_inventory=True)
        self._form_required(data);await self.destination_check(data)
        entry,d=await self._resolve(next(r for r,e in self.references.items() if e is entry),data)
        if not all(d.get(k) for k in ('ownership_verified','same_form','visible','enabled','action_safe')):raise SetupStop('SETUP_CONTROL_NOT_ACTIONABLE')
        role=entry['role'];clear=d.get({'control':'unobscured','option':'option_unobscured','search':'search_unobscured'}[role])
        if clear is not True:raise SetupStop('SETUP_CONTROL_OBSCURED_OR_OFFSCREEN')
        prefix=ORDER[:ORDER.index(entry['field'])]
        if not set(prefix)<=self.completed:
            historical={f['kind'] for f in self.browser.trace.step_facts}
            if prefix and all(FIELD_FACTS[f] in historical for f in prefix):raise SetupStop('SETUP_PREREQUISITE_CHANGED')
            raise SetupStop('SETUP_FIELD_ORDER_REQUIRED')
        if any(not data['fields'].get(k,{}).get('selected') or data['fields'][k].get('has_other_tags') for k in prefix):
            raise SetupStop('SETUP_PREREQUISITE_CHANGED')
        document=protected_document(self.browser)
        controls=tuple(sorted((e['field'],e['identity']) for e in self.references.values() if e['role']=='control' and e['form']==self.form_identity))
        controls=tuple(sorted(set(controls)))
        if self.safety_status['code'] is None:
            self.mutation_grant=MutationGrant(self.browser,self.browser.trace.run_id,document,self.form_identity,controls,'GLOBAL_COMPLETE')
        else:
            from modules.whatsapp.scope_evidence import investigate_closed_scope
            risks=await investigate_closed_scope(self.browser,data['form_selector'])
            self.risk_summary={key:value for key,value in risks.items() if key!='binding'}
            if not risks.get('complete'):raise SetupStop('SCOPED_RISK_EVIDENCE_UNAVAILABLE')
            scope_counts=self.safety_status.get('scope_reason_counts',{})
            if scope_counts.get('inventory_complete') is True and type(scope_counts.get('inaccessible_shadow_roots')) is int:
                if risks.get('closed_roots')!=scope_counts['inaccessible_shadow_roots']:raise SetupStop('MUTATION_SCOPE_CHANGED')
            prior=self.mutation_grant
            if prior:
                if (prior.route!='OPERATOR_REVIEWED_SCOPED_POC' or prior.document!=document or prior.form!=self.form_identity
                    or prior.controls!=controls or prior.risk_binding!=risks['binding']):raise SetupStop('MUTATION_SCOPE_CHANGED')
            else:
                if self.review_attempted or self.mutation_denied:raise SetupStop(self.mutation_denied or 'MUTATION_SCOPE_CHANGED')
                self.review_attempted=True
                payload={'phase':'AUTHORIZATION','form_recognized':True,'fields':list(ORDER),
                    'fixed_values_only':True,'global_safety':self.safety_status,'closed_root_investigation':self.risk_summary,
                    'remaining_uncertainty':'CLOSED_CONTENTS_AND_FUNCTION_NOT_CERTIFIED','complete_negative_coverage':False,
                    'independent_authentication_certified':False,'save_send_authorized':False}
                print('SCOPED_MUTATION_REVIEW_REQUIRED phase=AUTHORIZATION',flush=True)
                if await self.mutation_review(payload) is not True:raise SetupStop('MUTATION_REVIEW_DECLINED')
                # Explicit review does not authorize a changed page or a fresh risk.
                fresh=await self.scan(include_inventory=True)
                self._form_required(fresh);await self.destination_check(fresh)
                if protected_document(self.browser)!=document or self.form_identity!=self.references[next(r for r,e in self.references.items() if e is entry)]['form']:
                    raise SetupStop('MUTATION_SCOPE_CHANGED')
                await self._resolve(next(r for r,e in self.references.items() if e is entry),fresh)
                again=await investigate_closed_scope(self.browser,fresh['form_selector'])
                if not again.get('complete') or again['binding']!=risks['binding']:raise SetupStop('MUTATION_SCOPE_CHANGED')
                fresh_counts=self.safety_status.get('scope_reason_counts',{})
                if fresh_counts.get('inventory_complete') is True and type(fresh_counts.get('inaccessible_shadow_roots')) is int:
                    if again.get('closed_roots')!=fresh_counts['inaccessible_shadow_roots']:raise SetupStop('MUTATION_SCOPE_CHANGED')
                self.mutation_grant=MutationGrant(self.browser,self.browser.trace.run_id,document,self.form_identity,controls,
                    'OPERATOR_REVIEWED_SCOPED_POC',risks['binding'])
                self.review_evidence={**payload,'operator_approved':True,'route':'OPERATOR_REVIEWED_SCOPED_POC'}
                self.milestone('SCOPED_MUTATION_AUTHORIZED',complete_negative_coverage=False)
                data=fresh
        if not self.guard_installed:
            if not await self.browser.page.evaluate(INSTALL_GUARD):raise SetupStop('SETUP_GUARD_UNAVAILABLE')
            self.guard_installed=True
        if protected_document(self.browser)!=document:raise SetupStop('MUTATION_SCOPE_CHANGED')
        # Resolve the original observed identity after every permission/guard read.
        await stagehand_locator(self.browser,entry['selector'],entry['identity'])
        self.milestone('SCOPED_AUTHORIZATION_RESULT',result='ACTION_AUTHORIZED',route=self.mutation_grant.route)
        return data

    def _record_verified(self,field,data):
        self.completed.discard(field)
        self._form_required(data)
        try:validate_capability('verify_field',field=field)
        except CapabilityError as error:raise SetupStop(error.code) from None
        d=data['fields'].get(field,{})
        if not all(d.get(k) for k in ('discovered','selector_unique','ownership_verified','same_form','visible')):
            raise SetupStop('FIELD_MAPPING_NOT_VERIFIED')
        if d.get('kind') not in ({'TEXT_INPUT'} if field=='campaign_name' else {'NATIVE_SELECT','OWNED_COMBOBOX'}):
            raise SetupStop('FIELD_MAPPING_NOT_VERIFIED')
        if not d.get('selected') or d.get('has_other_tags') or d.get('exact_option_count',0)>1:raise SetupStop('FIELD_VALUE_NOT_VERIFIED')
        self.completed.add(field);self.last_verified_field=field;self.fact(FIELD_FACTS[field])

    async def _action(self,reference,operation,option_reference=None):
        async with self.lock:
            self.phase='AUTHORIZATION';self.active_approach=None;self.write_started=False
            try:
                data=await self.scan();self.require_page1_ready();entry,d=await self._resolve(reference,data,('control','search') if operation=='fill' else ('control',))
                field=entry['field']
                tool_name={'click':'click_observed_control','select':'select_observed_option','fill':'fill_text'}.get(operation)
                try:resolved_reference_action(tool_name,field,entry['role'])
                except CapabilityError as error:raise SetupStop(error.code) from None
                approach=operation+':'+entry['role']+':'+self.preparation[field]
                self.recovery_check(field,approach)
                if operation=='fill' and entry['role']=='control' and (field!='campaign_name' or d['kind']!='TEXT_INPUT'):
                    raise SetupStop('TEXT_FILL_SCOPE_FORBIDDEN')
                if operation=='click' and (d['kind']!='OWNED_COMBOBOX' or not d.get('open_selector')):raise SetupStop('CLICK_SCOPE_FORBIDDEN')
                if operation=='select':
                    if d.get('selected'):
                        self.phase='VERIFICATION';self._record_verified(field,data);return await self.present(data)
                    option,o=await self._resolve(option_reference,data,('option',))
                    if option['field']!=field or d.get('exact_option_count')!=1 or d.get('has_other_tags'):raise SetupStop('EXACT_EXISTING_OPTION_UNAVAILABLE')
                    if d['kind'] not in {'NATIVE_SELECT','OWNED_COMBOBOX'}:raise SetupStop('SELECT_SCOPE_FORBIDDEN')
                if operation=='fill' and entry['role']=='control' and d.get('selected'):
                    self.phase='VERIFICATION';self._record_verified(field,data);return await self.present(data)
                self.active_approach=(field,approach)
                data=await self._authorize(data,entry,d)
                entry,d=await self._resolve(reference,data,('control','search') if operation=='fill' else ('control',))
                clear=d.get({'control':'unobscured','search':'search_unobscured'}[entry['role']])
                if clear is not True or not d.get('enabled') or not d.get('action_safe'):raise SetupStop('SETUP_CONTROL_OBSCURED_OR_OFFSCREEN')
                if operation=='select':
                    option,_=await self._resolve(option_reference,data,('option',))
                    if option['field']!=field or d.get('exact_option_count')!=1 or d.get('has_other_tags'):
                        raise SetupStop('EXACT_EXISTING_OPTION_UNAVAILABLE')
                self.phase='ACTION'
                locator=await stagehand_locator(self.browser,entry['selector'],entry['identity'])
                await self.destination_check(data)
                actual=None
                def attempt():
                    self.write_started=True
                    item={'field':field,'operation':'search' if entry['role']=='search' else operation,
                        'outcome':'ATTEMPTED','verified':False}
                    self.browser_operations.append(item)
                    return item
                if operation=='fill':
                    actual=attempt();await locator.fill(VALUES[field],timeout=3000)
                elif operation=='click':
                    if not d.get('expanded'):
                        actual=attempt();await locator.click(timeout=3000)
                elif d['kind']=='NATIVE_SELECT':
                    actual=attempt();await locator.select_option([d['value']],timeout=3000)
                else:
                    if not d.get('option_visible') or not d.get('option_enabled') or d.get('option_unobscured') is not True:
                        raise SetupStop('SETUP_OPTION_NOT_ACTIONABLE')
                    choice=await stagehand_locator(self.browser,option['selector'],option['identity'])
                    actual=attempt();await choice.click(timeout=3000)
                if actual:actual['outcome']='SDK_RETURNED'
                self.phase='VERIFICATION';after=await self.scan()
                if operation=='select' or operation=='fill' and entry['role']=='control':self._record_verified(field,after)
                elif operation=='click' and not after['fields'].get(field,{}).get('expanded'):raise SetupStop('DROPDOWN_OPEN_NOT_VERIFIED')
                elif operation=='fill' and entry['role']=='search' and await locator.input_value(timeout=2000)!=VALUES[field]:raise SetupStop('FIELD_EXACT_READBACK_FAILED')
                if actual:actual.update(outcome='INDEPENDENTLY_VERIFIED',verified=True)
                if operation=='click':self.preparation[field]='DROPDOWN_OPENED'
                elif entry['role']=='search':self.preparation[field]='SEARCHED_EXISTING'
                return await self.present(after)
            except Exception as error:return self.failed(error)

    async def click_observed_control(self,reference):return await self._action(reference,'click')
    async def fill_text(self,reference):return await self._action(reference,'fill')
    async def select_observed_option(self,reference,option_reference):return await self._action(reference,'select',option_reference)

    async def verify_field(self,reference):
        async with self.lock:
            self.phase='VERIFICATION'
            try:
                data=await self.scan();entry,d=await self._resolve(reference,data,('control',))
                if not d.get('selected'):
                    self.completed.discard(entry['field']);self.final_gates_current=False
                    return {**await self.present(data),'ok':False,'error':'FIELD_NOT_YET_VERIFIED','terminal':False}
                self._record_verified(entry['field'],data)
                return await self.present(data)
            except Exception as error:return self.failed(error)

    async def fill(self,reference):
        # Compatibility helper is not exposed to the model; it uses identical gates.
        entry=self.references.get(reference)
        if not entry:return self.failed(SetupStop('UNOBSERVED_SETUP_ELEMENT'))
        if entry['field']=='campaign_name':return await self.fill_text(reference)
        data=await self.inspect_control(reference)
        if not data.get('ok'):return data
        field=next(x for x in data['fields'] if x['field']==entry['field'])
        if field['matches_required_value']:return await self.verify_field(reference)
        if not field['options']:return self.failed(SetupStop('EXACT_EXISTING_OPTION_UNAVAILABLE'))
        return await self.select_observed_option(reference,field['options'][0]['reference'])

    async def verify(self):
        async with self.lock:
            self.phase='VERIFICATION';self.final_gates_current=False
            try:
                data=await self.scan();self._form_required(data)
                if not data.get('same_form'):raise SetupStop('SETUP_FIELDS_NOT_IN_SAME_FORM')
                for field in ORDER:self._record_verified(field,data)
                # This controller-only finalization is not a model observation
                # tool. Protect the inspection hold even if values preexisted.
                if not self.guard_installed:
                    if not await self.browser.page.evaluate(INSTALL_GUARD):raise SetupStop('SETUP_GUARD_UNAVAILABLE')
                    self.guard_installed=True
                guard=await self.browser.page.evaluate('({installed:!!window.__netcoreSetupGuard,attempts:window.__netcoreSetupGuard?.attempts??null})')
                if guard.get('attempts') not in (0,None):raise SetupStop('FORBIDDEN_TRANSITION_ATTEMPTED')
                self.final_gates={field:'PASS' for field in ORDER}
                self.final_gates.update(setup_active='PASS',no_forbidden_transition='PASS')
                self.final_gates_current=True
                self.fact('SETUP_FIELDS_VERIFIED',self.final_gates);self.fact('NO_FORBIDDEN_TRANSITION_ATTEMPTED')
                return {'ok':True,'status':'SETUP_FILLED_UNSAVED','gates':dict(self.final_gates),
                    'commit_actions':'NOT_EXPOSED','server_persistence':'UNDETERMINED','mutation_authorization':self.authorization_summary()}
            except Exception as error:return self.failed(error)

    def registered_tools(self):
        raise SetupStop('RETIRED_WHATSAPP_TOOLS_USE_V2')
        @tool(args_schema=reference_schema('observe_page'))
        async def observe_page()->dict:
            """Read Setup identity/step/approved controls and uncertainty; no page writes."""
            return await self.invoke_recorded('observe_page',self.observe_page)
        @tool(args_schema=reference_schema('inspect_control'))
        async def inspect_control(reference:str)->dict:
            """Read an observed control, exact existing options and current ownership."""
            return await self.invoke_recorded('inspect_control',self.inspect_control,reference)
        @tool(args_schema=reference_schema('scroll_to_control'))
        async def scroll_to_control(reference:str)->dict:
            """Scroll only an observed unique Setup control into view; no field write."""
            return await self.invoke_recorded('scroll_to_control',self.scroll_to_control,reference)
        @tool(args_schema=reference_schema('click_observed_control'))
        async def click_observed_control(reference:str)->dict:
            """Open only an observed owned Setup dropdown after separate authorization."""
            return await self.invoke_recorded('click_observed_control',self.click_observed_control,reference)
        @tool(args_schema=reference_schema('fill_text'))
        async def fill_text(reference:str)->dict:
            """Fill only the application-fixed campaign name or owned exact-option search."""
            return await self.invoke_recorded('fill_text',self.fill_text,reference)
        @tool(args_schema=reference_schema('select_observed_option'))
        async def select_observed_option(reference:str,option_reference:str)->dict:
            """Select only the exact observed existing approved option in its owned control."""
            return await self.invoke_recorded('select_observed_option',self.select_observed_option,reference,option_reference)
        @tool(args_schema=reference_schema('verify_field'))
        async def verify_field(reference:str)->dict:
            """Read and independently verify one approved field; never authorizes a write."""
            return await self.invoke_recorded('verify_field',self.verify_field,reference)
        return [observe_page,inspect_control,scroll_to_control,click_observed_control,fill_text,select_observed_option,verify_field]


def readonly_whatsapp_destination(source,destination,origin):
    """Validate an observed menu GET destination, never synthesize a route."""
    from urllib.parse import urlsplit,unquote
    from modules.whatsapp.contract import V2Stop
    a,b=urlsplit(source),urlsplit(destination)
    decoded=b.path+'#'+b.fragment
    for _ in range(3):decoded=unquote(decoded)
    if (f'{a.scheme}://{a.netloc}'!=origin or f'{b.scheme}://{b.netloc}'!=origin
        or b.username or b.password or b.query or '%' in decoded or '\\' in decoded
        or not a.path.strip('/').split('/')[0] or a.path.strip('/').split('/')[0]!=b.path.strip('/').split('/')[0]
        or 'whatsapp' not in decoded.lower()
        or re.search(r'create|edit|delete|save|schedule|publish|activat|send|duplicate|clone|logout|login|new|test',decoded,re.I)):
        raise V2Stop('OBSERVED_READONLY_WHATSAPP_DESTINATION_REQUIRED',owner='authorization')
    return destination


class V2BrowserTools:
    """Canonical task tools. Model arguments never contain values or selectors."""

    def __init__(self,browser,payload=None,ui=None,authorization=None,directory=None,*,diagnostic_origin=None):
        from pathlib import Path
        self.diagnostic_mode=diagnostic_origin is not None
        if getattr(self,'diagnostic_mode',False):
            from urllib.parse import urlsplit
            from modules.whatsapp.contract import V2Stop
            target=urlsplit(SETUP_URL)
            expected_origin=f'{target.scheme}://{target.netloc}'
            if (payload is not None or ui is not None or authorization is not None or directory is not None or
                diagnostic_origin!=expected_origin or diagnostic_origin not in getattr(browser.policy,'origins',set())):
                raise V2Stop('INVALID_READONLY_DIAGNOSTIC_CONTEXT',owner='authorization')
            self.browser,self.payload,self.ui,self.authorization=browser,None,None,None
            self.directory=None;self.origin=diagnostic_origin
        else:
            if payload is None or ui is None or authorization is None or directory is None:
                raise ValueError('Production V2 tools require the complete campaign context')
            self.browser,self.payload,self.ui,self.authorization=browser,payload,ui,authorization
            self.directory=Path(directory);self.origin=ui.origin
        self.stage='diagnostic' if self.diagnostic_mode else 'setup';self.references={};self.events=[]
        self.images={};self.preparation='DIRECT';self.mutations=0;self.terminal=None
        self.pending_write=False;self.next_allowed=False;self.lock=asyncio.Lock()
        self.waba_selected=False;self.waba_opened=False;self.records_before=None
        self.preparations={};self.failed_approaches={};self.last_observation=None
        self.native_open_attempts=set()
        self.last_independent_readback = {}
        self.read_only=self.diagnostic_mode;self.start_state_verified=False
        self.reviewed_fields=set() if self.diagnostic_mode else set(ui.fields) if ui.reviewed else set()
        self.reviewed_stages=set() if self.diagnostic_mode else set(ui.stage_selectors) if ui.reviewed else set()
        self.observed_bindings={}
        self.preview_identity=None
        if self.diagnostic_mode:
            self.baseline=None
            return
        from modules.whatsapp.browser_baseline import BrowserBaseline
        import re as regex
        ids=regex.findall(r'\d{8,}',payload.waba)
        values={'campaign_name':payload.campaign_name,'waba':ids[-1] if ids else payload.waba,
            'business_number':payload.business_number,'tags':payload.tag}
        self.baseline=BrowserBaseline(browser,values=values,commit_guard=self.install_guard)

    async def install_guard(self):
        if getattr(self,'diagnostic_mode',False):
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('READONLY_DIAGNOSTIC_NO_PAGE_MUTATION',owner='authorization')
        # Keep global forbidden actions blocked; only the controller opens an
        # exact reviewed advance/save window. Login's existing guard is untouched.
        installed=await self.browser.page.evaluate(r'''(()=>{
          if(window.__netcoreV2Guard)return true;window.__netcoreV2Guard=true;
          const deny=e=>{e.preventDefault();e.stopImmediatePropagation();};
          document.addEventListener('click',e=>{const n=e.target.closest('button,a,input[type=submit],[role=button]');
            if(!n)return;const t=(n.textContent||n.value||'').trim();
            if(/publish|activate|send\s+(test|now)|finish\s+later/i.test(t))return deny(e);
            if((/next\s+step|save|schedule\s+now/i.test(t)||n.type==='submit')&&!window.__netcoreV2Advance){
              const grant=window.__netcoreV2Page1Next;
              const exact=grant?.kind==='draft-preview'?/^save\s+(and|&)\s+preview$/i.test(t):/^next\s+step$/i.test(t);
              if(grant?.control===n&&!grant.clicked&&exact)grant.clicked=true;
              else deny(e);
            }
          },true);
          document.addEventListener('submit',e=>{
            if(window.__netcoreV2Advance)return;
            const grant=window.__netcoreV2Page1Next;
            if(grant?.clicked&&!grant.submitted&&e.submitter===grant.control&&e.target===grant.control.form){
              grant.submitted=true;return;
            }
            deny(e);
          },true);
          return true;
        })()''')
        if installed is not True:
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('COMMIT_GUARD_NOT_INSTALLED',owner='authorization',unsafe=True)

    async def protect(self):
        from modules.whatsapp.contract import V2Stop
        from modules.whatsapp.browser_baseline import reject_security_negatives
        from urllib.parse import urlsplit
        if self.terminal:raise V2Stop(self.terminal,owner=getattr(self,'terminal_owner','browser'),unsafe=True)
        binding = getattr(self.browser, 'setup_binding', None)
        operator_binding = getattr(self, 'operator_setup_binding', None)
        if operator_binding is not None:
            login_binding = self.operator_login_binding
            stop = self.operator_setup_stop() or login_binding.stop_code
            if stop:
                raise V2Stop(stop, owner='browser', unsafe=True)
            if (binding is not operator_binding or self.browser.browser is not login_binding.browser
                    or not login_binding.same_owning_session(self.browser.guard)):
                raise V2Stop('OPERATOR_SETUP_TAB_OR_SESSION_CHANGED', owner='browser', unsafe=True)
        await self.browser.guard.ensure_observable()
        before=protected_document(self.browser)
        if operator_binding is not None and getattr(self, 'operator_approved_setup_document', before) != before:
            raise V2Stop('OPERATOR_APPROVED_SETUP_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        if self.browser.page is not self.browser.setup_page:raise V2Stop('SDK_PAGE_BINDING_CHANGED',owner='browser',unsafe=True)
        active=await self.browser.browser.context.active_page()
        if active is None or active.page_id!=self.browser.setup_page.page_id:raise V2Stop('PROTECTED_TASK_TAB_NOT_ACTIVE',owner='browser',unsafe=True)
        coherent=False
        for _ in range(3):
            first=await self.browser.page.url()
            reply=await self.browser.guard.send('Page.getFrameTree',session=before[1])
            frame=reply.get('frameTree',{}).get('frame',{})
            from modules.whatsapp.destination import cdp_frame_url
            second=await self.browser.page.url();current=cdp_frame_url(frame)
            coherent=(first==current==second and frame.get('id')==before[4] and
                (frame.get('loaderId') or None)==before[5] and before==protected_document(self.browser))
            if coherent:break
        expected_origin=self.origin if getattr(self,'diagnostic_mode',False) else self.ui.origin
        if not coherent:
            raise V2Stop('PROTECTED_URL_OR_DOCUMENT_MISMATCH',owner='browser',unsafe=True)
        from modules.whatsapp.destination import protected_route_status,navigation_url_difference
        diagnostic=getattr(self,'diagnostic_mode',False)
        status=protected_route_status(second,expected_origin=expected_origin,
            expected_tenant=None if diagnostic else self.ui.tenant,
            origin_permitted=self.browser.policy.permits(second),enforce_tenant=not diagnostic,
            campaign_stage=getattr(self, 'campaign_verified_stage', None))
        previous_url=getattr(self,'_protected_route_url',None)
        previous_document=getattr(self,'_protected_route_document',None)
        changed=(navigation_url_difference(previous_url,second) if status['route'] is not None else ['url_syntax']) if previous_url is not None else []
        document_changed=previous_document is not None and previous_document!=before
        if changed or document_changed:
            # A permitted transition is not permission to reuse stale controls or
            # a prior controller certificate. Every next interaction reobserves.
            self.references.clear();self.images.clear();self.observed_bindings.clear()
            self.next_allowed=False
        if document_changed:
            self.last_independent_readback = {}
            for cache in ('native_open_attempts','preparations','failed_approaches'):
                getattr(self,cache,{}).clear()
            self.waba_opened=False;self.waba_selected=False
        if previous_url is None or changed or document_changed or status['code']:
            self.browser.trace.agent_event('protected_task_route',role='controller',
                **status,initial_route=getattr(self,'_initial_protected_route',None),
                previous_route=getattr(self,'_last_protected_route',None),
                changed_components=changed,document_changed=document_changed,
                target_alias=getattr(self.browser.guard,'target_aliases',{}).get(before[0]),
                session_alias=getattr(self.browser.guard,'session_aliases',{}).get(before[1]),
                generation=before[2],document_generation=before[3],
                authentication_grant=False)
        if status['code']:raise V2Stop(status['code'],owner='authorization',unsafe=True)
        if operator_binding is not None:
            from modules.whatsapp.destination import classify_setup_page
            from modules.whatsapp.contract import SETUP_URL
            from modules.whatsapp.destination import campaign_step_route
            if not (classify_setup_page(second, SETUP_URL)['route_match'] or
                    campaign_step_route(second, getattr(self, 'campaign_verified_stage', None))):
                raise V2Stop('OPERATOR_SETUP_ROUTE_MISMATCH', owner='authorization', unsafe=True)
        if not hasattr(self,'_initial_protected_route'):self._initial_protected_route=status['route']
        self._last_protected_route=status['route']
        self._protected_route_url=second;self._protected_route_document=before
        if getattr(self,'diagnostic_mode',False):
            previous=getattr(self,'_diagnostic_observed_url',None)
            if previous is not None and previous!=second:
                self.references.clear()
                self.browser.trace.agent_event('diagnostic_references_invalidated',role='controller',
                    reason='CURRENT_PAGE_URL_CHANGED',authentication_grant=False)
            self._diagnostic_observed_url=second
        await reject_security_negatives(self.browser)
        if before!=protected_document(self.browser):raise V2Stop('DOCUMENT_CHANGED',owner='browser',unsafe=True)
        self.browser.budget()
        return before

    async def _whatsapp_page_observed(self):
        return await self.browser.page.evaluate(r'''(()=>
          [...document.querySelectorAll('main h1,main h2,[role=main] [role=heading],h1')].some(e=>
            e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'&&
            !e.closest('nav,[role=navigation],header,[role=banner],[role=alert],[role=dialog],dialog')&&
            /\bwhats\s*app\b/i.test((e.textContent||'').trim())))()''')

    async def observe(self,*,strategy=None):
        from modules.whatsapp.observation import explore_v2,wait_for_setup_change
        document=await self.protect()
        if strategy:self.observation_strategy=strategy
        strategy=strategy or getattr(self,'observation_strategy',None)
        extra={'strategy':strategy} if strategy else {}
        campaign_stage = getattr(self, 'campaign_verified_stage', None)
        if campaign_stage == self.stage and campaign_stage in {'audience', 'content'}:
            extra['campaign_walkthrough'] = True
            current_option = getattr(self, 'audience_option', None)
            if current_option:
                extra['audience_option'] = current_option
        field = getattr(self, 'page1_current_field', None)
        if field is not None:
            extra['current_field'] = field
        ga_fields = getattr(self, 'ga_setup_fields', None)
        mcp_ga_scope = self.stage == 'setup' and getattr(self, 'mcp_ga_scope', False)
        if mcp_ga_scope:
            extra['ga_scope'] = True
        if ga_fields:
            extra['ga_fields'] = ga_fields
            extra['ga_adjacent_keys'] = getattr(self, 'ga_adjacent_keys', False)
        data=await explore_v2(self.browser,self.ui,self.payload,self.stage,
            reviewed_fields=self.reviewed_fields,reviewed_stages=self.reviewed_stages,**extra)
        recognition_attempts=1
        while self.stage=='setup' and data.get('stage_visible') is not True and recognition_attempts<3:
            self.browser.trace.agent_event('setup_observation_retry',role='observer',
                attempt=recognition_attempts+1,
                field_candidate_count=len(data.get('field_candidates',[])),
                scope_basis=data.get('scope_basis','UNRESOLVED'),
                observation_scope=data.get('observation_scope','DOCUMENT_LIGHT_DOM'))
            await wait_for_setup_change(self.browser)
            if await self.protect()!=document:
                from modules.whatsapp.contract import V2Stop
                raise V2Stop('OBSERVATION_DOCUMENT_CHANGED',owner='browser',unsafe=True)
            data=await explore_v2(self.browser,self.ui,self.payload,self.stage,
                reviewed_fields=self.reviewed_fields,reviewed_stages=self.reviewed_stages,**extra)
            recognition_attempts+=1
        if await self.protect()!=document:
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('OBSERVATION_DOCUMENT_CHANGED',owner='browser',unsafe=True)
        # Bind only unambiguous actual labelled controls in an observed form scope.
        # This never supplies or approves a record endpoint/tenant mapping.
        from modules.whatsapp.contract import FieldBinding,ReviewedUI,STAGE_FIELDS
        active_fields = ga_fields or STAGE_FIELDS[self.stage]
        updates=dict(self.ui.fields);scopes=dict(self.ui.stage_selectors)
        if self.stage not in self.reviewed_stages:scopes.pop(self.stage,None)
        if self.stage=='preview':self.preview_identity=None
        candidates=data.get('field_candidates',[])
        current_proofs={}

        async def identify(selector, part, field=None, reference=None):
            try:
                return await observed_node(self.browser, selector)
            except SetupStop as error:
                if self.stage == 'content' and getattr(self, 'page1_current_field', None) == 'template':
                    from hashlib import sha256
                    self.browser.trace.agent_event('content_template_binding_failed', role='observer',
                        code=error.code, field=field, part=part, reference=reference,
                        selector_sha256=sha256(selector.encode()).hexdigest(), node_disappeared=lost_observed_node(error))
                raise

        if data.get('scope_selector') and data.get('stage_visible'):
            try:scope_identity=await identify(data['scope_selector'], 'form_scope')
            except SetupStop as error:
                if error.code!='CONTROL_IDENTITY_NOT_UNIQUE':raise
                # An ambiguous recognizer result is evidence, never a write grant.
                data={**data,'stage_visible':False,'discovered_bindings':{}}
                scope_identity=None
            scopes[self.stage]=data['scope_selector']
            if self.stage=='preview' and data.get('preview_identity_selector'):
                q=data['preview_identity_selector']
                self.preview_identity={'selector':q,'identity':await identify(q, 'preview')}
            for field,raw_binding in data.get('discovered_bindings',{}).items():
                if field not in active_fields or field in self.reviewed_fields:continue
                matching=[c for c in candidates if c['field']==field]
                if len(matching)!=1 or matching[0]['selector']!=raw_binding['selector']:continue
                try:
                    binding=FieldBinding.model_validate(raw_binding)
                    proposed=ReviewedUI.model_validate({**self.ui.model_dump(),'fields':{**updates,field:binding},'stage_selectors':scopes})
                except ValueError:continue
                updates=dict(proposed.fields)
                try:identity=await identify(binding.selector, 'field_control', field)
                except SetupStop as error:
                    if error.code!='CONTROL_IDENTITY_NOT_UNIQUE':raise
                    updates.pop(field,None)
                    self.browser.trace.agent_event('observation_control_skipped',role='observer',
                        stage=self.stage,reason=error.code,field=field,field_bound=False)
                    continue
                current_proofs[field]={'identity':identity,
                    'scope_identity':scope_identity,'field_label':matching[0]['field_label'],'stage':self.stage,
                    'strategy':strategy or 'semantic'}
        self.observed_bindings={k:v for k,v in self.observed_bindings.items() if k not in active_fields}
        self.observed_bindings.update(current_proofs)
        patch={'fields':updates,'stage_selectors':scopes}
        if data.get('advance_selector') and data.get('stage_visible'):
            if self.stage=='schedule' and not self.ui.preview_selector:patch['preview_selector']=data['advance_selector']
            elif self.stage in {'setup','audience','content'} and self.stage not in self.ui.next_selectors:
                patch['next_selectors']={**self.ui.next_selectors,self.stage:data['advance_selector']}
        if self.stage == 'content':
            if getattr(self, 'campaign_verified_stage', None) == 'content':
                patch['content_preview_selector'] = data.get('content_preview_selector')
            elif data.get('content_preview_selector') and not self.ui.content_preview_selector:
                patch['content_preview_selector'] = data['content_preview_selector']
        self.ui=self.ui.model_copy(update=patch)
        self.references={};public=[]
        for node in data['nodes']:
            reference=uuid4().hex
            try:identity=await identify(node['selector'], node['part'], node.get('field'), reference)
            except SetupStop as error:
                if error.code!='CONTROL_IDENTITY_NOT_UNIQUE':raise
                current_proofs.pop(node.get('field'),None)
                self.observed_bindings.pop(node.get('field'),None)
                self.browser.trace.agent_event('observation_control_skipped',role='observer',
                    stage=self.stage,reason=error.code,field_bound=False)
                continue
            self.references[reference]={**node,'identity':identity,'stage':self.stage}
            public.append({'reference':reference,**{k:node[k] for k in
                ('field','part','tag','role','type','label','enabled','expanded','exact','create',
                 'selected_exact','checked','value_present','selected_count','invalid',
                 'validation_error_present','option_facts') if k in node}})
        result={'stage':self.stage,'stage_visible':data['stage_visible'],'controls':public,
            'truncated':data.get('truncated',False),'pending_write':self.pending_write,
            'read_only_discovery':self.read_only,'field_evidence':sorted(current_proofs),
            'recognition_attempts':recognition_attempts,'strategy':strategy or 'semantic',
            'scope_basis':data.get('scope_basis','UNRESOLVED'),
            'observation_scope':data.get('observation_scope','DOCUMENT_LIGHT_DOM'),
            'observation_counts':data.get('observation_counts',{}),
            'closed_shadow_scope_certified':data.get('closed_shadow_scope_certified',False),
            'persistence_allowed':self.authorization.persistence,
            'unresolved_fields':[f for f in active_fields if f not in self.reviewed_fields and f not in current_proofs]}
        if ga_fields or mcp_ga_scope:
            result['ga_card_evidence'] = data.get('ga_card_evidence')
            self.ga_advanced_selector = data.get('ga_advanced_selector')
        if 'ga_tracking' in result['unresolved_fields'] and data.get('ga_row_evidence') is not None:
            result['ga_row_evidence'] = data['ga_row_evidence']
        if self.stage == 'audience' and result['unresolved_fields']:
            result['audience_picker_evidence'] = data.get('audience_picker_evidence', [])
            result['audience_section_evidence'] = data.get('audience_section_evidence', {})
        if self.stage == 'content':
            result['content_preview_evidence'] = data.get('content_preview_evidence', {})
        if data.get('scoped_option_evidence') is not None:
            result['scoped_option_evidence'] = data['scoped_option_evidence']
        result['binding_failures'] = {
            field: ('MULTIPLE_VISIBLE_LABELLED_CONTROLS' if sum(c['field'] == field for c in candidates) > 1 else
                    'NO_VISIBLE_CONTROL_WITH_EXACT_FIELD_LABEL' if not any(c['field'] == field for c in candidates) else
                    'CONTROL_OR_FORM_OWNERSHIP_UNPROVEN')
            for field in result['unresolved_fields']
        }
        if self.read_only:
            result['navigation']=await self.readonly_navigation_links(document)
            result['whatsapp_page_observed']=await self._whatsapp_page_observed()
        from app.config import ROOT
        private=ROOT/'credentials'/'whatsapp-v2'/self.browser.trace.run_id/('ui-discovery-'+uuid4().hex+'.json')
        private.parent.mkdir(parents=True,exist_ok=True)
        private.write_text(json.dumps({'reviewed':False,'source_run_id':self.browser.trace.run_id,
            'stage':self.stage,'provenance':'CURRENT_PROTECTED_DOM_NOT_PERSISTENCE_PROOF',
            'field_candidates':candidates,'scope_selector':data.get('scope_selector'),
            'discovered_bindings':data.get('discovered_bindings',{}),
            'advance_selector':data.get('advance_selector'),'content_preview_selector':data.get('content_preview_selector'),
            'preview_identity_selector':data.get('preview_identity_selector')},indent=2),encoding='utf-8')
        evidence=self.browser.trace.agent_event('dom_observation',role='observer' if self.browser.trace.agent_phase=='observer' else 'doer',result=result,
            private_evidence_ref=private.relative_to(ROOT).as_posix(),evidence_policy='STRUCTURAL_ONLY_NO_RAW_DOM')
        result['evidence_ref']=evidence;self.last_observation=result
        if self.stage == 'setup' and getattr(self.authorization, 'manual_form_test', False):
            actionable = [node['field'] for node in public
                          if node.get('field') in current_proofs and node.get('enabled') is True
                          and node.get('part') == 'control']
            self.browser.trace.agent_event('setup_form_readiness', role='observer',
                stage_visible=data['stage_visible'], actionable_fields=sorted(set(actionable)),
                unresolved_fields=result['unresolved_fields'], evidence_ref=evidence)
        return result

    async def observe_diagnostic(self):
        from modules.whatsapp.observation import explore_v2
        from modules.whatsapp.contract import V2Stop
        if not getattr(self,'diagnostic_mode',False):raise V2Stop('READONLY_DIAGNOSTIC_CONTEXT_REQUIRED',owner='authorization')
        document=await self.protect()
        data=await explore_v2(self.browser,None,None,'diagnostic',diagnostic=True)
        if not isinstance(data,dict) or not isinstance(data.get('nodes'),list):
            raise V2Stop('DIAGNOSTIC_OBSERVATION_INVALID',owner='tool')
        if await self.protect()!=document:
            raise V2Stop('OBSERVATION_DOCUMENT_CHANGED',owner='browser',unsafe=True)
        self.references={}
        public=[]
        public_labels={'campaign','campaigns','whatsapp','whatsapp campaigns','create campaign','new campaign',
            'setup','audience','content','schedule','campaign name','waba id','business number','add tags',
            'ga tracking','lists/segments','send later','frequency cap','contact limit','search','close','cancel'}
        public_tags={'INPUT','TEXTAREA','SELECT','BUTTON','A','DIV','SPAN'}
        public_roles={'button','link','combobox','option','searchbox','checkbox','radio','switch','tab','menuitem'}
        public_types={'text','search','checkbox','radio','button','submit','date','time','select-one','select-multiple'}
        for node in data['nodes']:
            selector=node.get('selector')
            if not isinstance(selector,str) or not selector:continue
            identity=await observed_node(self.browser,selector)
            reference=uuid4().hex
            label=node.get('label')
            if label not in public_labels:label=None
            tag=node.get('tag') if node.get('tag') in public_tags else 'OTHER'
            role=node.get('role') if node.get('role') in public_roles else None
            control_type=node.get('type') if node.get('type') in public_types else None
            enabled=node.get('enabled') if type(node.get('enabled')) is bool else None
            expanded=node.get('expanded') if type(node.get('expanded')) is bool else None
            checked=node.get('checked') if type(node.get('checked')) is bool else None
            value_present=node.get('value_present') if type(node.get('value_present')) is bool else None
            selected_count=node.get('selected_count') if type(node.get('selected_count')) is int and node['selected_count']>=0 else None
            entry={'selector':selector,'identity':identity,'stage':self.stage,'part':'control',
                'label':label,'tag':tag,'role':role,'type':control_type,
                'enabled':enabled,'expanded':expanded,'checked':checked,
                'value_present':value_present,'selected_count':selected_count}
            self.references[reference]=entry
            public.append({'reference':reference,'label':label,'tag':tag,'role':role,
                'type':control_type,'visible':True,'enabled':enabled,'expanded':expanded,
                'checked':checked,'value_state':self._redacted_value_state(value_present,tag),
                'selected_count':selected_count})
        navigation=await self.readonly_navigation_links(document)
        if await self.protect()!=document:
            raise V2Stop('OBSERVATION_DOCUMENT_CHANGED',owner='browser',unsafe=True)
        result={'mode':'READ_ONLY_DIAGNOSTIC','authentication':'NOT_CERTIFIED',
            'controls':public,'navigation':navigation,'truncated':data.get('truncated') is True,
            'whatsapp_page_observed':await self._whatsapp_page_observed()}
        evidence=self.browser.trace.agent_event('whatsapp_diagnostic_observation',role='doer',
            result=result,evidence_policy='OPAQUE_REFERENCES_AND_REDACTED_CONTROL_STATE')
        result['evidence_ref']=evidence;self.last_observation=result
        return result

    @staticmethod
    def _redacted_value_state(value_present,tag):
        if tag not in {'INPUT','TEXTAREA','SELECT'}:return 'NOT_APPLICABLE'
        if value_present is True:return 'POPULATED_REDACTED'
        if value_present is False:return 'EMPTY'
        return 'UNKNOWN_REDACTED'

    async def control_state(self,reference,entry):
        from modules.whatsapp.contract import V2Stop
        document=await self.protect()
        if await observed_node(self.browser,entry['selector'])!=entry['identity']:
            raise V2Stop('CONTROL_REFERENCE_STALE',owner='browser',unsafe=True)
        script=r'''(q=>{
          const a=document.querySelectorAll(q);if(a.length!==1)return null;const e=a[0];
          const rect=e.getBoundingClientRect(),visible=rect.width>0&&rect.height>0&&
            getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
          if(!visible||e.matches('input[type=password],input[type=file],input[type=hidden]'))return null;
          const selected=e.tagName==='SELECT'?[...e.selectedOptions]:[];
          const label=e.getAttribute('aria-label')||[...(e.labels||[])].map(n=>n.textContent).join(' ')||
            (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ')||
            e.getAttribute('title')||'';
          const invalid=e.getAttribute('aria-invalid')==='true'||!!e.matches(':invalid');
          const errorId=e.getAttribute('aria-errormessage'),error=errorId?document.getElementById(errorId):null;
          const valuePresent=e.matches('input:not([type=hidden]),textarea,select')?
            (e.type==='checkbox'||e.type==='radio'?e.checked:e.tagName==='SELECT'?selected.length>0:String(e.value||'').length>0):
            ['checkbox','switch','radio'].includes(e.getAttribute('role'))?
              (e.getAttribute('aria-checked')==='true'?true:e.getAttribute('aria-checked')==='false'?false:null):null;
          const checked=typeof e.checked==='boolean'?e.checked:
            e.getAttribute('aria-checked')==='true'?true:e.getAttribute('aria-checked')==='false'?false:null;
          return {tag:e.tagName,role:e.getAttribute('role'),type:e.type||null,label,visible,
            enabled:!e.disabled&&!e.readOnly&&e.getAttribute('aria-disabled')!=='true'&&!e.closest('[inert]'),
            checked:e.type==='checkbox'||e.type==='radio'||['checkbox','switch','radio'].includes(e.getAttribute('role'))?checked:null,
            expanded:e.getAttribute('aria-expanded')==='true',value_present:valuePresent,
            selected_count:e.tagName==='SELECT'?selected.length:null,invalid,
            validation_error_present:invalid||!!error&&error.getClientRects().length>0&&
              getComputedStyle(error).visibility!=='hidden'};
        })'''
        raw=await self.browser.page.evaluate(script+'('+json.dumps(entry['selector'])+')')
        if await self.protect()!=document or await observed_node(self.browser,entry['selector'])!=entry['identity']:
            raise V2Stop('CONTROL_REFERENCE_STALE',owner='browser',unsafe=True)
        if not isinstance(raw,dict) or raw.get('visible') is not True:
            raise V2Stop('CONTROL_UNAVAILABLE',owner='recognition')
        safe_roles={'button','link','combobox','option','searchbox','checkbox','radio','switch','tab','menuitem'}
        safe_types={'text','search','checkbox','radio','button','submit','date','time','select-one','select-multiple'}
        tag=raw.get('tag') if raw.get('tag') in {'INPUT','TEXTAREA','SELECT','BUTTON','A','DIV','SPAN'} else 'OTHER'
        role=raw.get('role') if raw.get('role') in safe_roles else None
        control_type=raw.get('type') if raw.get('type') in safe_types else None
        label=raw.get('label') if isinstance(raw.get('label'),str) else entry.get('label')
        label=' '.join(label.split()).lower() if isinstance(label,str) else None
        if label not in {'campaign','campaigns','whatsapp','whatsapp campaigns','create campaign','new campaign',
            'setup','audience','content','schedule','campaign name','waba id','business number','add tags',
            'ga tracking','lists/segments','send later','frequency cap','contact limit','search','close','cancel'}:
            label=None
        result={'reference':reference,'field':entry.get('field'),'label':label,'tag':tag,'role':role,'type':control_type,
            'visible':True,'enabled':raw.get('enabled') is True,
            'checked':raw.get('checked') if type(raw.get('checked')) is bool else None,
            'expanded':raw.get('expanded') is True,
            'value_state':self._redacted_value_state(raw.get('value_present'),tag),
            'selected_count':raw.get('selected_count') if type(raw.get('selected_count')) is int else None,
            'invalid':raw.get('invalid') is True,
            'validation_error_present':raw.get('validation_error_present') is True}
        result['evidence_ref']=self.browser.trace.agent_event('control_state_readback',
            role='observer' if self.browser.trace.agent_phase=='observer' else 'doer',
            result={k:v for k,v in result.items() if k!='reference'},
            evidence_policy='OPAQUE_REFERENCE_REDACTED_CONTROL_STATE')
        return result

    async def diagnostic_control_state(self,reference,entry):
        from modules.whatsapp.contract import V2Stop
        if not getattr(self,'diagnostic_mode',False):
            raise V2Stop('READONLY_DIAGNOSTIC_CONTEXT_REQUIRED',owner='authorization')
        return await self.control_state(reference,entry)

    def binding(self,field):
        from modules.whatsapp.contract import V2Stop
        if field not in self.ui.fields or (field not in self.reviewed_fields and field not in self.observed_bindings):
            raise V2Stop('CURRENT_FIELD_EVIDENCE_REQUIRED',owner='recognition')
        return self.ui.fields[field]

    async def verify_binding(self,field):
        """Revalidate dynamically observed identity/label, never model assertions."""
        from modules.whatsapp.contract import V2Stop
        binding=self.binding(field);proof=self.observed_bindings.get(field)
        if not proof:return binding
        if (proof['stage']!=self.stage or await observed_node(self.browser,binding.selector)!=proof['identity'] or
            await observed_node(self.browser,self.ui.stage_selectors[self.stage])!=proof['scope_identity']):
            raise V2Stop('DISCOVERED_FIELD_SCOPE_CHANGED',owner='browser',unsafe=True)
        from modules.whatsapp.observation import V2_LABEL_HELPERS, CAMPAIGN_PICKER_HELPERS, GA_TRACKING_HELPERS
        label_script = r'''(s=>{''' + V2_LABEL_HELPERS + CAMPAIGN_PICKER_HELPERS + GA_TRACKING_HELPERS + r'''
          const e=document.querySelector(s.q);if(!e)return false;
          const owned=s.campaign&&campaignPickerField(s.field)&&campaignPickerOwners(s.stage)[s.field]===e;
          const scope=s.ga_scope?document.querySelector(s.ga_scope):null;
          if(s.ga_scope&&(!scope||gaTrackingOwners(scope,s.ga_adjacent_keys).owners[s.field]!==e))return false;
          const text=owned||scope?s.field.replace('_',' '):fieldLabel(e,s.strategy);
          const kind=e.tagName==='SELECT'?'native_select':e.type==='checkbox'||e.getAttribute('role')==='checkbox'||
            (s.field==='ga_tracking'&&e.getAttribute('role')==='switch'&&e.type!=='submit')?'checkbox':
            e.type==='radio'||e.getAttribute('role')==='radio'?'radio':
            e.getAttribute('role')==='combobox'?'combobox':['INPUT','TEXTAREA'].includes(e.tagName)?'text':null;
          let ownedDisplay=true;
          if(kind==='combobox'&&s.selected){
            const a=document.querySelectorAll(s.selected),display=a.length===1?a[0]:null;
            const parent=e.matches('input,textarea')?e.parentElement:e;
            const controls=parent?[...parent.querySelectorAll('input,textarea,select,[role=combobox],[role=checkbox],[role=switch],[role=radio]')].filter(n=>n.getClientRects().length>0&&getComputedStyle(n).visibility!=='hidden'):[];
            const owns=e.contains(display)||parent&&controls.length===1&&controls[0]===e&&parent.contains(display);
            const ids=((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
            ownedDisplay=!!display&&!!owns&&!display.isContentEditable&&!display.matches('input,textarea,[role=option],[role=searchbox]')&&
              !ids.some(id=>document.getElementById(id)?.contains(display));
          }
          return String(text).replace(/\s+/g,' ').trim().toLowerCase()===s.label&&kind===s.kind&&ownedDisplay;
        })(SETTINGS)'''
        label_ok = await self.browser.page.evaluate(label_script.replace('SETTINGS',json.dumps(
            {'q': binding.selector, 'field': field, 'label': proof['field_label'], 'kind': binding.kind,
             'strategy': proof.get('strategy', 'semantic'), 'selected': binding.selected_selector,
             'campaign': getattr(self, 'campaign_verified_stage', None) == self.stage,
             'ga_scope': self.ui.stage_selectors.get('setup') if (
                 (getattr(self, 'ga_setup_fields', None) and field != 'campaign_name')
                 or (getattr(self, 'mcp_ga_scope', False) and field == 'ga_tracking')) else None,
             'ga_adjacent_keys': getattr(self, 'ga_adjacent_keys', False),
             'stage': self.stage})))
        if label_ok is not True:raise V2Stop('DISCOVERED_FIELD_LABEL_CHANGED',owner='recognition')
        return binding

    async def resolve(self,reference):
        from modules.whatsapp.contract import V2Stop
        await self.protect();entry=self.references.get(reference)
        if not entry or entry['stage']!=self.stage:raise V2Stop('FRESH_OBSERVED_REFERENCE_REQUIRED',owner='recognition')
        if await observed_node(self.browser,entry['selector'])!=entry['identity']:
            if (self.stage == 'content' and getattr(self, 'campaign_verified_stage', None) == 'content'
                    and entry.get('field') == 'template' and entry.get('part') == 'option'):
                raise SetupStop('STALE_CONTROL_REFERENCE')
            raise V2Stop('TARGET_IDENTITY_CHANGED',owner='browser',unsafe=True)
        loc=await stagehand_locator(self.browser,entry['selector'],entry['identity'])
        if not await loc.is_visible(timeout=2000):
            if (self.stage != 'audience' or getattr(self, 'campaign_verified_stage', None) != 'audience'
                    or entry.get('field') != 'audience_type' or entry.get('part') != 'control'):
                raise V2Stop('TARGET_NOT_VISIBLE',owner='recognition')
            loc = await self.audience_radio_label(entry)
        if entry.get('field'):
            binding=await self.verify_binding(entry['field'])
            stage_template = (self.stage == 'content' and entry['field'] == 'template'
                              and getattr(self, 'campaign_verified_stage', None) == 'content')
            owned=await self.browser.page.evaluate(r"""(s=>{
              const one=q=>{const a=q?document.querySelectorAll(q):[];return a.length===1?a[0]:null};
              const c=one(s.control),e=one(s.target),scope=one(s.scope);
              const report=(ok,reason,extra={})=>s.template?{ok,reason,...extra}:ok;
              if(!c||!e||!scope||!scope.contains(c))
                return report(false,'CONTROL_OR_SCOPE_UNBOUND',{control:!!c,target:!!e,scope:!!scope});
              if(s.part==='control')return report(e===c,'CONTROL_IDENTITY');
              const links=n=>((n.getAttribute('aria-controls')||'')+' '+(n.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
              const ids=[...new Set(links(c))];
              const panels=ids.map(id=>document.getElementById(id)).filter(panel=>panel?.contains(e));
              const search=s.template?one(s.search):null;
              const owners=[...document.querySelectorAll('[aria-controls],[aria-owns]')].filter(n=>n!==c&&
                links(n).some(id=>ids.includes(id)));
              // The already-owned Search input can expose a relationship to
              // results fed by that input. It is not an independent picker.
              const auxiliary=search&&search.matches('input,textarea,[role=searchbox]')&&
                panels.some(panel=>panel.contains(search))&&owners.includes(search);
              const competing=owners.filter(n=>!auxiliary||n!==search);
              return report(!competing.length&&(s.template?panels.length===1:panels.length>0),
                competing.length?'COMPETING_POPUP_OWNER':panels.length!==1?'TARGET_NOT_IN_UNIQUE_OWNED_PANEL':'OWNED_PANEL',
                {part:s.part,panel_count:panels.length,competing_owner_count:competing.length,owned_search_delegate:!!auxiliary});
            })(SETTINGS)""".replace('SETTINGS',json.dumps({'control':binding.selector,'target':entry['selector'],
                'scope':self.ui.stage_selectors[self.stage],'part':entry['part'],'search':binding.search_selector,
                'template':stage_template})))
            if stage_template:
                self.browser.trace.agent_event('content_template_target_ownership', role='observer', **owned)
                owned = owned['ok']
            if owned is not True:raise V2Stop('CURRENT_FIELD_OWNERSHIP_UNPROVEN',owner='recognition')
        return entry,loc

    async def audience_radio_label(self, entry):
        from modules.whatsapp.contract import V2Stop
        from modules.whatsapp.page1_transition import CAMPAIGN_DOM
        from modules.whatsapp.observation import V2_LABEL_HELPERS

        settings = {'input': entry['selector'], 'scope': self.ui.stage_selectors['audience'],
                    'value': self.payload.audience_type}
        expression = (r'''(s=>{''' + V2_LABEL_HELPERS + CAMPAIGN_DOM + r'''
          const input=one(s.input), scope=one(s.scope);
          if(input?.type!=='radio'||!scope?.contains(input)||input.disabled||input.matches(':disabled'))return null;
          const labels=[...input.labels].filter(e=>scope.contains(e)&&actionable(e)&&e.control===input&&
            e.innerText.replace(/\s+/g,' ').trim()===s.value&&!e.querySelector('a,button,select,textarea')&&
            [...e.querySelectorAll('input')].every(n=>n===input));
          return labels.length===1?cssPath(labels[0]):null;
        })(SETTINGS)''').replace('SETTINGS', json.dumps(settings))
        selector = await self.browser.page.evaluate(expression)
        if not selector:
            self.browser.trace.agent_event('campaign_audience_radio_label', role='observer',
                                           status='EXACT_ASSOCIATED_VISIBLE_LABEL_NOT_UNIQUE')
            raise V2Stop('AUDIENCE_RADIO_VISIBLE_LABEL_NOT_BOUND', owner='recognition')
        identity = await observed_node(self.browser, selector)
        target = await stagehand_locator(self.browser, selector, identity)
        if (not await target.is_visible(timeout=2000)
                or await self.browser.page.evaluate(expression) != selector
                or await observed_node(self.browser, entry['selector']) != entry['identity']):
            raise V2Stop('AUDIENCE_RADIO_LABEL_BINDING_CHANGED', owner='recognition')
        self.browser.trace.agent_event('campaign_audience_radio_label', role='observer', status='BOUND')
        return target

    async def authorize_write(self):
        from modules.whatsapp.contract import V2Stop
        draft = getattr(self, 'draft_approval', None)
        if draft is not None:
            draft.require(self, self.stage)
        if self.read_only or not self.authorization.edits:
            raise V2Stop('READ_ONLY_DISCOVERY_NO_CAMPAIGN_WRITE',owner='authorization')
        if self.authorization.manual_form_test:
            operator_binding = getattr(self, 'operator_setup_binding', None)
            operator_approved = (operator_binding is not None and self.browser.setup_binding is operator_binding
                                 and getattr(self, 'operator_approved_setup_document', None) is not None
                                 and getattr(self, 'operator_approved_payload', None) is self.payload)
            if not operator_approved and getattr(self, 'manual_authentication_verified', False) is not True:
                raise V2Stop('MANUAL_SETUP_REQUIRES_VERIFIED_LOGIN', owner='authorization', unsafe=True)
            later_stage = (self.stage in {'audience', 'content'} and operator_approved and
                           getattr(self, 'campaign_verified_stage', None) == self.stage)
            if self.authorization.persistence or (self.stage != 'setup' and not later_stage):
                raise V2Stop('MANUAL_FORM_TEST_SETUP_ONLY',owner='authorization')
            if later_stage:
                from modules.whatsapp.page1_transition import verify_active_campaign_stage
                await verify_active_campaign_stage(self, self.stage)
        else:
            if not self.start_state_verified:
                raise V2Stop('READ_ONLY_DISCOVERY_NO_CAMPAIGN_WRITE',owner='authorization')
            self.ui.require_record()
        await self.check_write_scope(require_setup_route=self.authorization.manual_form_test and self.stage=='setup')
        await self.install_guard()

    async def check_write_scope(self, *, require_setup_route, preflight=False, expected_document=None):
        from modules.whatsapp.contract import V2Stop

        before = await self.protect()
        if require_setup_route and (getattr(self, '_last_protected_route', None)
                                    or {}).get('fragment_class') != 'SETUP':
            raise V2Stop('SETUP_ROUTE_NOT_READY', owner='recognition')
        guard = getattr(self.browser, 'guard', None)
        document = {}
        if isinstance(before, tuple) and len(before) == 6:
            target, session, generation, document_generation, _, _ = before
            document = {'target_alias': getattr(guard, 'target_aliases', {}).get(target),
                        'session_alias': getattr(guard, 'session_aliases', {}).get(session),
                        'generation': generation, 'document_generation': document_generation}
        if expected_document is not None and before != expected_document:
            self.browser.trace.agent_event('setup_write_security_gate', role='controller', preflight=True,
                status='FAILED', code='WRITE_DOCUMENT_CHANGED', failed_predicates=['current_setup_document'], **document)
            print('SETUP_WRITE_PREFLIGHT FAILED WRITE_DOCUMENT_CHANGED current_setup_document', flush=True)
            raise V2Stop('WRITE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        scoped = getattr(self, 'operator_setup_binding', None) is not None
        # protect() already verifies the current owned document, exact URL and
        # security negatives. resolve() proves control identity and ownership for
        # each operation. The operator-confirmed path needs no closed-root inventory.
        status = None if scoped else safety_diagnostic(await read_safety_snapshot(self.browser, include_inventory=True))
        if status is not None and status['code'] is not None:
            self.browser.trace.agent_event('setup_write_security_gate', role='controller',
                                           preflight=preflight, status='FAILED', **document, **status)
            if preflight:
                print('SETUP_WRITE_PREFLIGHT FAILED ' + json.dumps({
                    **document, 'code': status['code'], 'failed_predicates': status['failed_predicates'],
                    'scope_reason_counts': status['scope_reason_counts'],
                }), flush=True)
            raise V2Stop('WRITE_SECURITY_SCOPE_NOT_CERTIFIED', owner='authorization', unsafe=True)
        if await self.protect() != before:
            if preflight:
                self.browser.trace.agent_event('setup_write_security_gate', role='controller', preflight=True,
                    status='FAILED', code='WRITE_DOCUMENT_CHANGED', failed_predicates=['current_setup_document'], **document)
                print('SETUP_WRITE_PREFLIGHT FAILED WRITE_DOCUMENT_CHANGED current_setup_document', flush=True)
            raise V2Stop('WRITE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        if preflight:
            checked = {**document, 'scope': 'BOUND_SETUP_DOCUMENT' if scoped else 'FULL_SECURITY_INVENTORY'}
            self.browser.trace.agent_event('setup_write_security_gate', role='controller',
                                           preflight=True, status='PASSED', **checked)
            print('SETUP_WRITE_PREFLIGHT PASSED ' + json.dumps(checked), flush=True)
        return before

    async def reveal_ga_advanced(self):
        """Open only the bound Advanced disclosure on the current Setup card."""
        from modules.whatsapp.contract import V2Stop
        from modules.whatsapp.observation import V2_LABEL_HELPERS, GA_TRACKING_HELPERS

        selector = getattr(self, 'ga_advanced_selector', None)
        if not selector:
            return
        await self.authorize_write()
        document = await self.protect()
        scope = self.ui.stage_selectors.get('setup')
        expression = (r'''(s=>{''' + V2_LABEL_HELPERS + GA_TRACKING_HELPERS + r'''
            const roots=document.querySelectorAll(s.scope), controls=document.querySelectorAll(s.control);
            return roots.length===1&&controls.length===1&&gaAdvanced(roots[0]).toggle===controls[0];
        })(SETTINGS)''').replace('SETTINGS', json.dumps({'scope': scope, 'control': selector}))
        identity = await observed_node(self.browser, selector)
        locator = await stagehand_locator(self.browser, selector, identity)
        if (await self.protect() != document or await self.browser.page.evaluate(expression) is not True
                or await observed_node(self.browser, selector) != identity):
            raise V2Stop('GA_ADVANCED_DISCLOSURE_CHANGED', owner='recognition')
        await locator.click(timeout=self.operation_timeout_ms)
        self.browser.trace.agent_event('ga_advanced_dispatched', role='controller', action='OPEN_ADVANCED')
        for _ in range(3):
            await asyncio.sleep(.2)
            await self.observe(strategy='dom')
            if (self.last_observation.get('ga_card_evidence') or {}).get('advanced_regions') == 1:
                return

    async def screenshot(self):
        import struct
        from time import monotonic
        from modules.whatsapp.contract import V2Stop
        if getattr(self,'diagnostic_mode',False):
            raise V2Stop('READONLY_DIAGNOSTIC_SCREENSHOT_NOT_AUTHORIZED',owner='authorization')
        if not self.authorization.vision or not self.ui.visual_scope_selector or not self.ui.stage_selectors.get(self.stage):
            raise V2Stop('REVIEWED_VISUAL_SCOPE_AND_VISION_PERMISSION_REQUIRED',owner='authorization')
        document=await self.protect()
        # The reviewed region excludes tenant/account chrome; no image is written.
        geometry=await self.browser.page.evaluate('(s=>{const a=document.querySelectorAll(s.q),p=document.querySelectorAll(s.stage);if(a.length!==1||p.length!==1)return null;'+
            'const e=a[0],r=e.getBoundingClientRect();if(!p[0].contains(e)||e.matches("input[type=password]")||e.querySelector("input[type=password]"))return null;'+
            'if(r.x<0||r.y<0||r.right>innerWidth||r.bottom>innerHeight)return null;'+
            'return {x:r.x,y:r.y,scrollX,scrollY,width:r.width,height:r.height};})('+json.dumps({'q':self.ui.visual_scope_selector,'stage':self.ui.stage_selectors[self.stage]})+')')
        region=({'x':geometry['x']+geometry['scrollX'],'y':geometry['y']+geometry['scrollY'],
            'width':geometry['width'],'height':geometry['height'],'scale':1} if geometry else None)
        if not region or min(region['width'],region['height'])<=0 or region['x']<0 or region['y']<0:
            raise V2Stop('VISUAL_SCOPE_UNAVAILABLE',owner='recognition')
        result=await self.browser.guard.send('Page.captureScreenshot',{'format':'png','clip':region},document[1])
        if await self.protect()!=document:raise V2Stop('SCREENSHOT_DOCUMENT_CHANGED',owner='browser',unsafe=True)
        raw=base64.b64decode(result['data']);width,height=struct.unpack('>II',raw[16:24]);del raw
        if width*height>8000000:raise V2Stop('SCREENSHOT_SIZE_LIMIT')
        key=uuid4().hex
        self.images={key:{'document':document,'time':monotonic(),'region':region,'geometry':geometry,'width':width,'height':height}}
        metadata={'screenshot_id':key,'width':width,'height':height,'expires_seconds':8,
            'storage':'TRANSIENT_IMAGE_NOT_PERSISTED'}
        metadata['evidence_ref']=self.browser.trace.agent_event('scoped_screenshot',result=metadata)
        return metadata,result['data']

    async def records(self,*,on_login=False):
        """Independent reviewed GET, reduced in-page; never export response bodies."""
        from modules.whatsapp.contract import V2Stop
        if getattr(self,'diagnostic_mode',False):raise V2Stop('READONLY_DIAGNOSTIC_NO_CAMPAIGN_RECORD_ACCESS',owner='authorization')
        self.ui.require_record()
        async def binding():
            if not on_login:return await self.protect()
            from modules.login.verifier import verify
            from app.state import Result
            await self.browser.guard.ensure_observable()
            if self.browser.page is not self.browser.login_page or verify(await self.browser.observe())!=Result.AUTHENTICATED_AND_VERIFIED:
                raise V2Stop('AUTHENTICATED_START_STATE_REQUIRED',owner='authentication',unsafe=True)
            owner=self.browser.guard.session_states[self.browser.guard.primary_session]
            return (owner.target,owner.session,owner.generation,owner.document_generation,owner.frame_id,owner.loader_id)
        document=await binding()
        settings={**self.ui.model_dump(),'expected':self.payload.model_dump()}
        settings['expected']['schedule_datetime']=self.payload.scheduled_local().isoformat()
        script=r'''(async s=>{try{
          const r=await fetch(s.record_path,{method:'GET',credentials:'same-origin',redirect:'error',cache:'no-store'});
          if(!r.ok||Number(r.headers.get('content-length')||0)>1048576)return {available:false};
          const text=await r.text();if(text.length>1048576)return {available:false};const d=JSON.parse(text);
          if(d[s.authenticated_key]!==true||d[s.tenant_key]!==s.tenant)return {available:false};
          const rows=d[s.records_key];if(!Array.isArray(rows)||rows.length>1000)return {available:false};
          const matches=rows.filter(x=>x[s.record_name_key]===s.expected.campaign_name);
          if(matches.length!==1)return {available:true,count:matches.length};
          const x=matches[0],id=x[s.record_id_key];if(!['string','number'].includes(typeof id))return {available:false};
          const hash=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(String(id)));
          return {available:true,count:1,record_hash:[...new Uint8Array(hash)].map(v=>v.toString(16).padStart(2,'0')).join(''),
            draft:x[s.record_status_key]===s.draft_status,
            fields:Object.fromEntries(Object.entries(s.record_fields).map(([k,v])=>[k,k==='schedule_datetime'?
              typeof x[v]==='string'&&/^\d{4}-\d{2}-\d{2}T/.test(x[v])&&Number.isFinite(Date.parse(x[v]))&&Date.parse(x[v])===Date.parse(s.expected[k]):x[v]===s.expected[k]]))};
        }catch{return {available:false}}})(SETTINGS)'''
        result=await self.browser.page.evaluate(script.replace('SETTINGS',json.dumps(settings)))
        if await binding()!=document:raise V2Stop('RECORD_DOCUMENT_CHANGED',owner='browser',unsafe=True)
        result['evidence_ref']=self.browser.trace.agent_event('persisted_record_readback',result=result,
            authentication_context='VERIFIED_LOGIN' if on_login else 'PROTECTED_TASK')
        return result

    def write_journal(self,action,status):
        if getattr(self,'diagnostic_mode',False):
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('READONLY_DIAGNOSTIC_NO_PERSISTENCE',owner='authorization')
        import hashlib
        from pathlib import Path
        from app.config import ROOT
        key=hashlib.sha256((self.ui.tenant+'\0'+self.payload.campaign_name).encode()).hexdigest()
        path=ROOT/'outputs'/'whatsapp-v2'/'persistence'/(key+'.json')
        path.parent.mkdir(parents=True,exist_ok=True)
        if path.exists():
            previous=json.loads(path.read_text())
            if previous.get('status')=='PENDING' and (status!='VERIFIED' or previous.get('run_id')!=self.browser.trace.run_id):
                from modules.whatsapp.contract import V2Stop
                raise V2Stop('PRIOR_PERSISTENT_WRITE_UNCERTAIN',owner='uncertain_write',unsafe=True)
        data={'run_id':self.browser.trace.run_id,'stage':self.stage,'action':action,'status':status}
        temporary=path.with_suffix('.tmp');temporary.write_text(json.dumps(data));temporary.replace(path)

    async def advance(self):
        """Record controller persistence calls, including failures before/after click."""
        if getattr(self,'diagnostic_mode',False):
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('READONLY_DIAGNOSTIC_NO_PERSISTENCE',owner='authorization')
        if getattr(getattr(self,'authorization',None),'manual_form_test',False):
            from modules.whatsapp.contract import V2Stop
            raise V2Stop('MANUAL_FORM_TEST_PERSISTENCE_FORBIDDEN',owner='authorization')
        started=monotonic();result=None;self.controller_attempted=False
        event={'id':str(len(self.events)),'stage':self.stage,'action':'advance','field':None,
            'method':'controller','preparation':'VERIFIED_STAGE','ok':False,'attempted':False}
        self.events.append(event)
        call=self.browser.trace.agent_event('controller_action_started',role='controller',action='advance',event_id=event['id'])
        try:
            result=await self._advance();event['ok']=True
            return result
        except BaseException as error:
            from modules.whatsapp.contract import V2Stop
            event.update(error=error.code if isinstance(error,V2Stop) else type(error).__name__,
                owner=error.owner if isinstance(error,V2Stop) else 'unknown',unsafe=self.pending_write or (isinstance(error,V2Stop) and error.unsafe))
            raise
        finally:
            event.update(attempted=self.controller_attempted,duration_ms=round((monotonic()-started)*1000,3),result=result)
            event['trace_ref']=self.browser.trace.agent_event('controller_action_result',role='controller',
                invocation_id=call,event_id=event['id'],**{k:v for k,v in event.items() if k!='id'})

    async def _advance(self):
        from modules.whatsapp.contract import V2Stop,STAGE_FIELDS
        if not self.next_allowed or not self.authorization.persistence:raise V2Stop('OBSERVER_STAGE_CERTIFICATE_REQUIRED',owner='authorization')
        self.ui.require_persistence(self.stage)
        self.payload.check_schedule()
        await self.authorize_write()
        if not self.next_allowed:raise V2Stop('OBSERVER_STAGE_CERTIFICATE_REQUIRED',owner='authorization')
        selector=self.ui.preview_selector if self.stage=='schedule' else self.ui.next_selectors[self.stage]
        document=await self.protect();identity=await observed_node(self.browser,selector)
        locator=await stagehand_locator(self.browser,selector,identity)
        # Exact reviewed save/advance control; reject dangerous combined actions.
        eligible=await self.browser.page.evaluate(r'''(s=>{
          const a=document.querySelectorAll(s.target),p=document.querySelectorAll(s.stage);
          if(a.length!==1||p.length!==1)return false;
          const e=a[0],scope=p[0];
          return scope.contains(e)&&scope.getClientRects().length>0&&
            e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'&&
            !e.disabled&&!e.readOnly&&e.getAttribute('aria-disabled')!=='true'&&
            !e.closest('[inert]');
        })(SETTINGS)'''.replace('SETTINGS',json.dumps({'target':selector,'stage':self.ui.stage_selectors[self.stage]})))
        if eligible is not True or not await locator.is_visible(timeout=2000):
            raise V2Stop('ADVANCE_CONTROL_NOT_CURRENTLY_ACTIONABLE',owner='recognition')
        text=await self.browser.page.evaluate('(q=>{const e=document.querySelector(q);return e?.textContent||e?.value||""})('+json.dumps(selector)+')')
        if re.search(r'publish|activate|send test|send now',text,re.I):raise V2Stop('PROHIBITED_ACTION',owner='authorization',unsafe=True)
        accepted={'save and preview','save & preview'} if self.stage=='schedule' else {'next step'}
        if ' '.join(text.casefold().split()) not in accepted:raise V2Stop('PERSISTENCE_CONTROL_LABEL_NOT_EXACT',owner='authorization',unsafe=True)
        if document!=await self.protect() or identity!=await observed_node(self.browser,selector):
            raise V2Stop('ADVANCE_TARGET_CHANGED',owner='browser',unsafe=True)
        if not self.next_allowed:raise V2Stop('OBSERVER_STAGE_CERTIFICATE_REQUIRED',owner='authorization')
        self.write_journal('SAVE_AND_PREVIEW' if self.stage=='schedule' else 'NEXT_STEP','PENDING')
        self.pending_write=True;self.next_allowed=False
        await self.browser.page.evaluate('window.__netcoreV2Advance=true')
        try:
            self.controller_attempted=True
            await locator.click(timeout=getattr(self,'operation_timeout_ms',3000))
        finally:await self.browser.page.evaluate('window.__netcoreV2Advance=false')
        # A click/toast/transition is never persistence proof. Only read-only checks follow.
        for attempt in range(3):
            state=await self.records()
            if state.get('available') and state.get('count')==1 and state.get('draft') and all(
                state.get('fields',{}).get(f) is True for f in STAGE_FIELDS[self.stage]):
                self.pending_write=False;self.write_journal('SAVE','VERIFIED')
                return {'ok':True,'persisted':True,'record_hash':state['record_hash'],
                    'persisted_fields':{field:state['fields'][field] for field in STAGE_FIELDS[self.stage]},
                    'draft_verified':True,'evidence_ref':state.get('evidence_ref')}
            if attempt<2:await asyncio.sleep(.3)
        raise V2Stop('PERSISTENT_WRITE_UNCERTAIN_NO_RETRY',owner='uncertain_write',unsafe=True)

    async def readonly_navigation_links(self,document):
        """Use the existing selector generator; expose opaque references only."""
        from modules.login.discovery import DOCUMENT_HELPERS
        origin=self.origin if getattr(self,'diagnostic_mode',False) else self.ui.origin
        settings={'origins':sorted(self.browser.policy.origins),'account':''}
        expression='(settings=>{'+DOCUMENT_HELPERS+r'''
          return [...document.querySelectorAll('nav a[href],[role=navigation] a[href],header a[href]')].slice(0,32)
            .filter(e=>visible(e)&&/whatsapp/i.test(e.textContent||e.getAttribute('aria-label')||'')&&
              !e.onclick&&!e.hasAttribute('onclick')&&!e.hasAttribute('download')&&
              !e.closest('form')&&(!e.target||e.target==='_self')&&e.getAttribute('aria-disabled')!=='true')
            .map(e=>({selector:selector(e,document,settings.account),href:e.href})).filter(e=>e.selector);
        })(SETTINGS)'''
        links=await self.browser.page.evaluate(expression.replace('SETTINGS',json.dumps(settings)))
        if not isinstance(links,list):return []
        current=await self.browser.page.url();public=[]
        for link in links:
            approved=getattr(self,'operator_navigation_selector_sha256',None)
            if approved is not None and hashlib.sha256(link.get('selector','').encode()).hexdigest()!=approved:
                continue
            try:destination=readonly_whatsapp_destination(current,link['href'],origin)
            except (KeyError,TypeError,ValueError,RuntimeError):continue
            if destination==current:continue
            identity=await observed_node(self.browser,link['selector']);reference=uuid4().hex
            self.references[reference]={'part':'navigation','selector':link['selector'],'href':destination,
                'identity':identity,'document':document,'stage':self.stage}
            public.append({'reference':reference,'label':'Observed read-only WhatsApp menu link','action':'navigate'})
        return public

    async def navigate_readonly(self,reference,event):
        from modules.whatsapp.contract import V2Stop
        if not self.read_only:raise V2Stop('READONLY_NAVIGATION_SCOPE_REQUIRED',owner='authorization')
        diagnostic=getattr(self,'diagnostic_mode',False)
        entry=self.references.get(reference)
        if not entry or entry.get('part')!='navigation':raise V2Stop('OBSERVED_NAVIGATION_REFERENCE_REQUIRED',owner='authorization')
        if entry['document']!=await self.protect() or entry['identity']!=await observed_node(self.browser,entry['selector']):
            raise V2Stop('NAVIGATION_REFERENCE_STALE',owner='browser',unsafe=True)
        same=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q);return !!e&&e.tagName==="A"&&e.href===s.href&&!e.onclick&&!e.hasAttribute("onclick")&&!e.hasAttribute("download")&&(!e.target||e.target==="_self")&&e.getClientRects().length>0&&/whatsapp/i.test(e.textContent||e.getAttribute("aria-label")||"")})('+json.dumps({'q':entry['selector'],'href':entry['href']})+')')
        if same is not True:raise V2Stop('NAVIGATION_LINK_CHANGED',owner='browser',unsafe=True)
        origin=self.origin if diagnostic else self.ui.origin
        source=await self.browser.page.url();destination=readonly_whatsapp_destination(source,entry['href'],origin)
        event['attempted']=True
        await self.browser.navigate(destination)
        final=await self.browser.page.url()
        try:readonly_whatsapp_destination(source,final,origin)
        except V2Stop:raise V2Stop('READONLY_NAVIGATION_REDIRECT_UNVERIFIED',owner='browser',unsafe=True) from None
        if not diagnostic:await self.install_guard()
        await self.protect();self.references.clear()
        self.browser.trace.agent_event('readonly_whatsapp_navigation',role='doer',status='ARRIVED_AND_PROTECTED',
            destination_sha256=__import__('hashlib').sha256(final.encode()).hexdigest())
        event['progress']=True
        return await self.observe_diagnostic() if diagnostic else await self.observe()

    async def action(self,arguments,*,template_rebind_available=False):
        from modules.whatsapp.contract import V2Stop, STAGE_FIELDS
        from modules.whatsapp.observer import deterministic_readback
        from time import monotonic
        diagnostic=getattr(self,'diagnostic_mode',False)
        manual=getattr(getattr(self,'authorization',None),'manual_form_test',False)
        active_fields = getattr(self, 'ga_setup_fields', None) or STAGE_FIELDS[self.stage]
        try:
            data = self.action_schema().model_validate(arguments)
        except (ValueError, TypeError):
            return self.reject_schema(arguments), None
        if not diagnostic:
            from modules.whatsapp.contract import V2_CAPABILITIES,validate_v2_capability
            entry=getattr(self,'references',{}).get(data.reference)
            if entry and data.action in V2_CAPABILITIES:
                field=data.field or entry.get('field')
                try:
                    if field not in active_fields or data.field and data.field!=entry.get('field'):
                        raise V2Stop('FIELD_REFERENCE_SCOPE_MISMATCH',owner='authorization')
                    binding=self.binding(field)
                    validate_v2_capability(data.action,binding.kind,entry.get('part'))
                except V2Stop as error:
                    self.browser.trace.agent_event('browser_action_rejected',role='doer',tool='browser_action',
                        error=error.code,field=field,browser_interaction=False)
                    return {'ok':False,'error':error.code,'owner':error.owner,'retry_safe':True},None
        image_data=None;result=None;started=monotonic()
        event={'id':str(len(self.events)),'stage':self.stage,'action':data.action,
            'field':getattr(data,'field',None),'method':'visual' if getattr(data,'screenshot_id',None) or data.action=='screenshot' else 'semantic',
            'preparation':'DIRECT','attempted':False,'valid_approach':False,'ok':False,'progress':False,
            'arguments':self.browser.trace.safe_tool_arguments('browser_action',arguments),'evidence_refs':[]}
        call=self.browser.trace.agent_event('browser_action_started',role='doer',tool='browser_action',
            action=data.action,event_id=event['id'],arguments=event['arguments'])
        self.events.append(event)
        async with self.lock:
            try:
                async with asyncio.timeout(getattr(self,'operation_timeout_ms',10000)/1000):
                    if getattr(self, 'terminal', None):
                        raise V2Stop(self.terminal, owner=getattr(self, 'terminal_owner', 'browser'),
                                     unsafe=getattr(self, 'terminal_unsafe', True))
                    await self.protect()
                    if self.pending_write:raise V2Stop('WRITE_UNCERTAIN_READ_ONLY_REQUIRED',owner='uncertain_write',unsafe=True)
                    if diagnostic:
                        if data.action=='observe':result=await self.observe_diagnostic()
                        elif data.action=='navigate':result=await self.navigate_readonly(data.reference,event)
                        else:
                            entry,loc=await self.resolve(data.reference)
                            if data.action in {'inspect','readback'}:
                                result=await self.diagnostic_control_state(data.reference,entry)
                            elif data.action=='scroll':
                                before=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return [scrollX,scrollY,r.x,r.y]})('+json.dumps(entry['selector'])+')')
                                if data.delta_y is None:
                                    await self.browser.guard.send('DOM.scrollIntoViewIfNeeded',{'backendNodeId':entry['identity'][-1]},entry['identity'][1])
                                else:
                                    point=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})('+json.dumps(entry['selector'])+')')
                                    await self.browser.page.scroll(point['x'],point['y'],0,data.delta_y)
                                after=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return [scrollX,scrollY,r.x,r.y]})('+json.dumps(entry['selector'])+')')
                                event['progress']=before!=after
                                result={'scrolled':event['progress']}
                            else:raise V2Stop('UNSUPPORTED_DIAGNOSTIC_ACTION',owner='authorization')
                    elif data.action in {'observe','dom'}:
                        method='dom' if data.action=='dom' else 'semantic'
                        event.update(method=method,approach=method,valid_approach=True,attempted=True)
                        result=await self.observe(strategy=method)
                        unresolved=result.get('unresolved_fields',[])
                        for missing in unresolved:
                            self.failed_approaches.setdefault((self.stage,missing),set()).add('discovery:'+method)
                        for field in result.get('field_evidence',[]):
                            self.preparations[(self.stage,field)]='DOM' if method=='dom' else 'SEMANTIC'
                            # Finding a control is progress in discovery, not proof
                            # that a field has been filled or an option selected.
                            prior=self.failed_approaches.get((self.stage,field),set())
                            remaining={attempt for attempt in prior if not attempt.startswith('discovery:')}
                            if remaining:self.failed_approaches[(self.stage,field)]=remaining
                            else:self.failed_approaches.pop((self.stage,field),None)
                        event['progress']=bool(result.get('field_evidence'))
                        if unresolved:
                            result={**result,'recognition_status':'PARTIAL' if event['progress'] else 'UNRESOLVED',
                                'permitted_next_operations':['dom' if method=='semantic' else 'observe','wait','inspect'],
                                'remaining_predicates':unresolved}
                    elif not diagnostic and data.action=='wait':
                        from modules.whatsapp.observation import wait_for_setup_change
                        await wait_for_setup_change(self.browser)
                        result=await self.observe()
                    elif not diagnostic and data.action=='screenshot':result,image_data=await self.screenshot()
                    elif not diagnostic and data.action=='navigate':result=await self.navigate_readonly(data.reference,event)
                    elif not diagnostic:
                        entry,loc=await self.resolve(data.reference)
                        field=data.field or entry.get('field')
                        event['field']=field
                        event['preparation']=self.preparations.get((self.stage,field),'DIRECT')
                        if event['method']!='visual' and (event['preparation']=='DOM' or getattr(self,'observation_strategy',None)=='dom'):
                            event['method']='dom'
                        if (data.action == 'click' and entry['part'] == 'control' and not data.screenshot_id
                                and getattr(self, 'operator_setup_binding', None) is not None
                                and self.binding(field).kind in {'combobox', 'native_select'}):
                            # Structural discovery changes how the control is
                            # located, not how the widget receives its first click.
                            prior = self.failed_approaches.get((self.stage, field), set())
                            known_mat_select = (field in {'waba','template'} and
                                                getattr(self, 'page1_current_field', None) == field and
                                                entry.get('tag') == 'MAT-SELECT')
                            retry = 'keyboard' if known_mat_select else 'dom'
                            event['method'] = retry if f'{field}:native' in prior else 'native'
                        if (data.action == 'click' and field == 'template' and entry['part'] == 'search'
                                and getattr(self, 'campaign_verified_stage', None) == 'content'):
                            event['method'] = 'native'
                        event['approach']=':'.join((str(field),event['method']))
                        if field == 'template' and entry['part'] == 'search':
                            event['approach'] += ':search'
                        if data.field and (data.field not in active_fields or entry.get('field')!=data.field):raise V2Stop('FIELD_REFERENCE_SCOPE_MISMATCH',owner='authorization')
                        from modules.whatsapp.contract import validate_v2_capability
                        if data.action in {'click','type','search','select','options','verify'}:
                            if field not in active_fields:raise V2Stop('CURRENT_STAGE_FIELD_REQUIRED',owner='authorization')
                            validate_v2_capability(data.action,self.binding(field).kind,entry['part'])
                        if data.action in {'inspect','options'}:
                            result=await self.inspect_options(entry) if data.action=='options' else await self.control_state(data.reference,entry)
                        elif data.action=='verify':result=await deterministic_readback(self,[field])
                        elif data.action=='scroll':
                            before=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return [scrollX,scrollY,r.x,r.y]})('+json.dumps(entry['selector'])+')')
                            if data.delta_y is None:
                                await self.browser.guard.send('DOM.scrollIntoViewIfNeeded',{'backendNodeId':entry['identity'][-1]},entry['identity'][1])
                            else:
                                import math
                                if not math.isfinite(data.delta_y) or abs(data.delta_y)>1000:raise V2Stop('SCROLL_LIMIT')
                                point=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return {x:r.x+r.width/2,y:r.y+r.height/2}})('+json.dumps(entry['selector'])+')')
                                await self.browser.page.scroll(point['x'],point['y'],0,data.delta_y)
                            after=await self.browser.page.evaluate('(q=>{const r=document.querySelector(q).getBoundingClientRect();return [scrollX,scrollY,r.x,r.y]})('+json.dumps(entry['selector'])+')')
                            event['progress']=before!=after
                            if event['progress']:self.preparations[(self.stage,field)]='SCROLLED'
                            result={'scrolled':event['progress']}
                        else:
                            value_change = data.action in {'type', 'select'}
                            if manual and getattr(self, 'human_assisted_setup', False) and self.stage == 'setup' and field in active_fields:
                                preceding = active_fields[:active_fields.index(field)]
                                if getattr(self, 'mcp_ga_switch_only', False):
                                    if field != 'ga_tracking' or data.action != 'type':
                                        raise V2Stop('MCP_GA_SWITCH_ONLY', owner='authorization')
                                    preceding = ()
                                if getattr(self, 'ga_setup_fields', None) and field.startswith('utm_'):
                                    preceding = ('campaign_name', 'ga_tracking')
                                verified = self.last_independent_readback.get('fields', {})
                                page1_field = getattr(self, 'page1_current_field', None)
                                if page1_field is not None:
                                    from modules.whatsapp.contract import PAGE1_SETUP_FIELDS
                                    if field not in PAGE1_SETUP_FIELDS or field != page1_field:
                                        raise V2Stop('PAGE1_CURRENT_FIELD_SCOPE_REQUIRED', owner='recognition')
                                    preceding = {'business_number': ('campaign_name', 'waba'),
                                                 'campaign_name': ()}.get(field, ('campaign_name',))
                                tag_exhausted = (field == 'ga_tracking' and
                                    getattr(self, 'operator_setup_binding', None) is not None and
                                    len(self.failed_approaches.get(('setup', 'tag'), ())) >= 2)
                                if any(verified.get(name) is not True and not (name == 'tag' and tag_exhausted)
                                       for name in preceding):
                                    raise V2Stop('VERIFY_PRECEDING_SETUP_FIELDS_FIRST', owner='recognition')
                                if preceding and not any(
                                    event.get('action') == 'type' and event.get('field') == 'campaign_name'
                                    and event.get('attempted') is True and event.get('ok') is True
                                    and event.get('verification', {}).get('actual_match') is True
                                    for event in self.events):
                                    raise V2Stop('WRITE_AND_VERIFY_CAMPAIGN_NAME_FIRST', owner='recognition')
                            if self.mutations >= 1:
                                raise V2Stop('TURN_MUTATION_LIMIT', owner='controller')
                            if not field or field not in active_fields or entry.get('create'):raise V2Stop('UNAUTHORIZED_CONTROL',owner='authorization')
                            if not self.authorization.edits or not entry['enabled']:raise V2Stop('CONTROL_NOT_ACTIONABLE',owner='authorization')
                            await self.authorize_write()
                            entry,loc=await self.resolve(data.reference)
                            current=await self.browser.page.evaluate('(q=>{const e=document.querySelector(q);return !!e&&!e.disabled&&!e.readOnly&&!e.matches(":disabled")&&!e.closest("[inert],[aria-disabled=true]")})('+json.dumps(entry['selector'])+')')
                            if current is not True:raise V2Stop('CONTROL_NOT_CURRENTLY_ENABLED',owner='recognition')
                            if data.action=='click' and entry['part'] not in {'control','search'}:raise V2Stop('USE_EXACT_OPTION_SELECTION',owner='authorization')
                            if data.action=='click' and (self.ui.fields[field].kind=='text' or entry['part']=='search') and (
                                entry.get('tag') not in {'INPUT','TEXTAREA'} or entry.get('type') in {'password','hidden','file','submit','button','checkbox','radio'}):
                                raise V2Stop('TEXT_INPUT_KIND_NOT_SUPPORTED',owner='recognition')
                            campaign_picker = (getattr(self, 'campaign_verified_stage', None) == self.stage
                                and field in {'include_audience', 'exclude_audience', 'template'})
                            if (data.action=='select' and self.ui.fields[field].kind=='combobox'
                                    and getattr(self, 'page1_current_field', None) != 'tag' and not campaign_picker):
                                # A popup option that disappears on selection cannot
                                # independently prove the resulting selected state.
                                binding=self.ui.fields[field]
                                persistent=await self.browser.page.evaluate(r'''(s=>{
                                  const one=q=>{if(!q)return null;const a=document.querySelectorAll(q);return a.length===1?a[0]:null};
                                  const c=one(s.control),e=one(s.selected),p=one(s.stage);
                                  if(!c||!e||!p||!p.contains(c)||!p.contains(e)||!e.getClientRects().length||getComputedStyle(e).visibility==='hidden'||
                                    e.isContentEditable||e.matches('input,textarea,[role=searchbox],[role=option]'))return false;
                                  const ids=((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
                                  return !ids.some(id=>document.getElementById(id)?.contains(e));
                                })(SETTINGS)'''.replace('SETTINGS',json.dumps({'control':binding.selector,
                                    'selected':binding.selected_selector,'stage':self.ui.stage_selectors.get(self.stage)})))
                                if persistent is not True:raise V2Stop('PERSISTENT_SELECTED_STATE_EVIDENCE_REQUIRED',owner='recognition')
                            if data.action in {'type','search'}:
                                binding=self.ui.fields[field]
                                if data.action=='search' and (entry['part'] not in {'search','control'} or
                                    entry.get('tag') not in {'INPUT','TEXTAREA'} or entry.get('type') in {'password','hidden','file','submit','button','checkbox','radio'}):
                                    raise V2Stop('OBSERVED_SEARCH_REQUIRED')
                                if data.action=='type' and binding.kind not in {'text','checkbox','datetime_split'}:raise V2Stop('FIELD_REQUIRES_SELECTION')
                                if binding.kind=='text' and (entry['tag'] not in {'INPUT','TEXTAREA'} or entry['type'] in {'password','hidden','file','submit','button','checkbox','radio'}):raise V2Stop('TEXT_INPUT_KIND_NOT_SUPPORTED',owner='recognition')
                                if binding.kind=='checkbox' and not (entry['type']=='checkbox' or entry['role']=='checkbox'
                                    or field=='ga_tracking' and entry['role']=='switch' and entry['type']!='submit'):
                                    raise V2Stop('CHECKBOX_KIND_NOT_SUPPORTED',owner='recognition')
                            failed=self.failed_approaches.setdefault((self.stage,field),set())
                            if manual and len(failed) >= 2:
                                raise V2Stop('STUCK_TWO_APPROACHES_FAILED', owner='recognition')
                            if event['approach'] in failed:raise V2Stop('IDENTICAL_FAILED_APPROACH_USE_DIFFERENT_METHOD',owner='recognition')
                            if data.action=='click' and self.ui.fields[field].kind=='native_select' and (self.stage,field) in self.native_open_attempts:
                                raise V2Stop('NATIVE_OPEN_ALREADY_ATTEMPTED_INSPECT_OR_SELECT',owner='recognition')
                            before_field = None
                            if value_change:
                                before_state = await deterministic_readback(self, [field])
                                before_field = before_state['fields'].get(field)
                                if (getattr(self, 'mcp_campaign_name_write', False)
                                        and before_state.get('observed_states', {}).get('campaign_name', {}).get('state') != 'EMPTY'):
                                    raise V2Stop('MCP_CAMPAIGN_NAME_NO_LONGER_EMPTY', owner='authorization')
                            before_search=await loc.input_value(timeout=2000) if data.action=='search' else None
                            if manual and value_change:
                                # A pre-write match for this field cannot survive
                                # an interrupted or unverified write.
                                readback = getattr(self, 'last_independent_readback', {})
                                fields = {key: value for key, value in readback.get('fields', {}).items()
                                          if key != field}
                                self.last_independent_readback = {**readback, 'fields': fields}
                            if value_change or not manual:
                                self.mutations += 1
                            if data.screenshot_id:
                                shot=self.images.get(data.screenshot_id)
                                if not shot or monotonic()-shot['time']>8 or shot['document']!=await self.protect():raise V2Stop('STALE_SCREENSHOT',owner='recognition')
                                import math
                                if not all(math.isfinite(v) for v in (data.x,data.y)) or not (0<=data.x<shot['width'] and 0<=data.y<shot['height']):raise V2Stop('COORDINATES_OUT_OF_BOUNDS')
                                geometry=shot['geometry']
                                scroll=await self.browser.page.evaluate('({x:scrollX,y:scrollY})')
                                if (scroll['x'],scroll['y'])!=(geometry['scrollX'],geometry['scrollY']):raise V2Stop('SCREENSHOT_SCROLL_CHANGED',owner='recognition')
                                x=geometry['x']+data.x*geometry['width']/shot['width']
                                y=geometry['y']+data.y*geometry['height']/shot['height']
                                hit=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q),h=document.elementFromPoint(s.x,s.y);return !!e&&(e===h||e.contains(h))})('+json.dumps({'q':entry['selector'],'x':x,'y':y})+')')
                                if hit is not True:raise V2Stop('VISUAL_TARGET_NOT_CONFIRMED',owner='recognition')
                                await self.resolve(data.reference)
                                if monotonic()-shot['time']>8:raise V2Stop('STALE_SCREENSHOT',owner='recognition')
                                event['valid_approach']=True;event['attempted']=True;await self.browser.page.click(x,y)
                            elif data.action=='click':
                                event['valid_approach']=True;event['attempted']=True
                                if event['method']=='keyboard':
                                    view = await self.browser.page.evaluate(r'''(q=>{
                                      const nodes=document.querySelectorAll(q), e=nodes.length===1?nodes[0]:null;
                                      if(e?.tagName!=='MAT-SELECT'||e.getAttribute('role')!=='combobox'||
                                         e.tabIndex<0||e.closest('[aria-disabled=true],[inert]')||
                                         !e.getClientRects().length||getComputedStyle(e).visibility!=='visible')return null;
                                      if(e.getAttribute('aria-expanded')==='true')return 'ALREADY_OPEN';
                                      e.focus({preventScroll:true});
                                      return document.activeElement===e?'FOCUSED':null;
                                    })('''+json.dumps(entry['selector'])+')')
                                    if view not in {'FOCUSED','ALREADY_OPEN'}:
                                        event['attempted']=False
                                        raise V2Stop(field.upper()+'_KEYBOARD_FOCUS_NOT_VERIFIED',owner='recognition')
                                    if view == 'FOCUSED':
                                        await self.protect()
                                        if (await observed_node(self.browser,entry['selector']) != entry['identity']
                                                or await self.browser.page.evaluate(
                                                    '(q=>document.activeElement===document.querySelector(q)&&'
                                                    'document.activeElement.getAttribute("aria-expanded")!=="true")('
                                                    +json.dumps(entry['selector'])+')') is not True):
                                            event['attempted']=False
                                            raise V2Stop(field.upper()+'_KEYBOARD_TARGET_CHANGED',owner='recognition')
                                        await self.browser.page.key_press('Enter')
                                    else:
                                        event['attempted']=False
                                elif event['method']=='dom':
                                    await loc.send_click_event(bubbles=True,cancelable=True,composed=True,
                                        timeout=getattr(self,'operation_timeout_ms',3000))
                                else:await loc.click(timeout=getattr(self,'operation_timeout_ms',3000))
                            if data.action=='click' and self.ui.fields[field].kind=='native_select':self.native_open_attempts.add((self.stage,field))
                            if data.action in {'type','search'}:
                                value=self.expected_value(field)
                                if self.ui.fields[field].kind=='checkbox':
                                    checked=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q);if(!e)return null;' +
                                        'if(e.type==="checkbox")return e.checked;const role=e.getAttribute("role");' +
                                        'if(role==="checkbox"||(s.ga&&role==="switch"&&e.type!=="submit"))' +
                                        'return e.getAttribute("aria-checked")==="true"?true:e.getAttribute("aria-checked")==="false"?false:null;return null;})('+
                                        json.dumps({'q': entry['selector'], 'ga': field == 'ga_tracking'})+')')
                                    if type(checked) is not bool:raise V2Stop('CHECKBOX_STATE_UNVERIFIED',owner='recognition')
                                    if checked!=value:
                                        event['valid_approach']=True;event['attempted']=True;await loc.click(timeout=getattr(self,'operation_timeout_ms',3000))
                                elif self.ui.fields[field].kind=='datetime_split':
                                    b=self.ui.fields[field];scheduled=self.payload.scheduled_local()
                                    if not b.date_selector or not b.time_selector:raise V2Stop('REVIEWED_DATE_TIME_CONTROLS_REQUIRED',owner='recognition')
                                    prepared=[]
                                    for selector,text in ((b.date_selector,scheduled.strftime(b.date_format)),(b.time_selector,scheduled.strftime(b.time_format))):
                                        safe=await self.browser.page.evaluate('(s=>{const a=document.querySelectorAll(s.q),p=document.querySelectorAll(s.stage);return a.length===1&&p.length===1&&p[0].contains(a[0])&&a[0].tagName==="INPUT"&&["date","time","text"].includes(a[0].type)&&!a[0].disabled&&!a[0].readOnly&&a[0].getClientRects().length>0})('+json.dumps({'q':selector,'stage':self.ui.stage_selectors[self.stage]})+')')
                                        if safe is not True:raise V2Stop('DATE_TIME_PART_NOT_ACTIONABLE',owner='recognition')
                                        identity=await observed_node(self.browser,selector)
                                        prepared.append((selector,text,identity))
                                    for selector,text,identity in prepared:
                                        target=await stagehand_locator(self.browser,selector,identity)
                                        event['valid_approach']=True;event['attempted']=True;await target.fill(text,timeout=getattr(self,'operation_timeout_ms',3000))
                                elif field=='campaign_name' and data.action=='type' and not self.authorization.manual_form_test:
                                    from modules.whatsapp.browser_baseline import field_reference
                                    discovery=await self.baseline.discover()
                                    reference=field_reference(discovery,'campaign_name')
                                    try:await self.baseline.fill_campaign(reference,authorized=True)
                                    finally:
                                        event['attempted']=self.baseline.write_attempted
                                        event['valid_approach']=self.baseline.write_attempted
                                elif (data.action == 'search' and field == 'tag' and
                                      getattr(self, 'page1_current_field', None) == 'tag' and before_search == value):
                                    pass  # The verified Page 1 picker search already matches.
                                else:
                                    if field=='schedule_datetime':value=self.payload.scheduled_local().strftime(self.ui.fields[field].datetime_format)
                                    event['valid_approach']=True;event['attempted']=True;await loc.fill(value,timeout=getattr(self,'operation_timeout_ms',3000))
                            elif data.action=='select':
                                await self.select(entry,loc,field,event)
                                if field=='waba':self.waba_selected=True
                            elif data.action!='click':raise V2Stop('UNSUPPORTED_ACTION',owner='tool')
                            self.images.clear()
                            if data.action in {'type','select'}:
                                current_observation = await self.observe()
                                result=await deterministic_readback(self,[field])
                                if result['fields'].get(field) is not True:raise V2Stop('FIELD_WRITE_UNCERTAIN_NO_RETRY',owner='uncertain_write',unsafe=True)
                                event['progress']=before_field is not True
                                if manual:
                                    result['observation'] = current_observation
                            elif data.action=='search':
                                if await loc.input_value(timeout=2000)!=self.expected_value(field):raise V2Stop('SEARCH_READBACK_FAILED',owner='recognition')
                                event['progress']=before_search!=self.expected_value(field)
                                self.preparations[(self.stage,field)]='SEARCHED_EXISTING'
                                result={'search_text_verified':True,'selection_verified':False}
                            elif data.action=='click' and (self.ui.fields[field].kind=='text' or entry['part']=='search'):
                                focused=await self.browser.page.evaluate('(q=>document.activeElement===document.querySelector(q))('+json.dumps(entry['selector'])+')')
                                result={'focused':focused is True,'selection_verified':False}
                                if not focused:raise V2Stop('INPUT_FOCUS_NOT_VERIFIED',owner='recognition')
                            else:
                                attempts = 3 if field in {'waba','template'} and getattr(self, 'page1_current_field', None) == field else 1
                                for poll in range(attempts):
                                    if poll:
                                        await asyncio.sleep(.25)
                                    fresh=await self.observe()
                                    opened=any(c['field']==field and c['part']=='control' and c['expanded'] for c in fresh['controls'])
                                    options=any(c['field']==field and c['part']=='option' for c in fresh['controls'])
                                    if opened or options:
                                        break
                                event['progress']=(opened and not entry['expanded']) or (options and not entry['expanded'])
                                if opened or options:
                                    self.preparations[(self.stage,field)]='DROPDOWN_OPENED'
                                if field=='waba' and (opened or options or self.ui.fields[field].kind=='native_select'):
                                    self.waba_opened=True
                                result={'interaction_returned':True,'dropdown_open_verified':opened or options,
                                    'selection_verified':False,'observation':fresh}
                                if not (opened or options):
                                    if self.ui.fields[field].kind=='combobox':raise V2Stop('DROPDOWN_OPEN_NOT_VERIFIED',owner='recognition')
                                    result['native_popup_visibility']='UNAVAILABLE_USE_OPTIONS_AND_SELECT'
                    if (event.get('field') and data.action in {'type', 'select', 'verify'}
                            and result.get('fields', {}).get(event['field']) is True):
                        self.failed_approaches.pop((self.stage,event['field']),None)
                    event['ok']=True
                    return {'ok':True,**result},image_data
            except asyncio.CancelledError:
                self.terminal='INTERRUPTED_WRITE_NO_RETRY' if event['attempted'] else 'CANCELLED'
                self.terminal_owner = 'uncertain_write' if event['attempted'] else 'controller'
                self.terminal_unsafe = event['attempted']
                event.update(error=self.terminal,unsafe=self.terminal_unsafe,owner=self.terminal_owner);raise
            except Exception as error:
                option = self.references.get(data.reference) if data.action == 'select' and data.field == 'template' else None
                if (self.stage == 'content' and getattr(self, 'campaign_verified_stage', None) == 'content'
                        and option and option.get('part') == 'option' and not event['attempted']
                        and (lost_observed_node(error) or isinstance(error, SetupStop)
                             and error.code in {'STALE_CONTROL_REFERENCE', 'CONTROL_IDENTITY_NOT_UNIQUE'})):
                    self.browser.trace.agent_event('content_template_binding_failed', role='observer',
                        code=error.code, field='template', part='option_action', reference=data.reference,
                        node_disappeared=lost_observed_node(error))
                    if template_rebind_available and not self.terminal and not self.pending_write:
                        event.update(error=error.code, owner='recognition', unsafe=False)
                        result={'ok':False,'error':error.code,'template_rebind_required':True}
                        return result,None
                defect=isinstance(error,(TypeError,AttributeError,KeyError,NameError,NotImplementedError))
                import re
                raw_code=getattr(error,'code',None)
                code='CODE_FIX_REQUIRED' if defect else raw_code if isinstance(raw_code,str) and re.fullmatch(r'[A-Z][A-Z0-9_]{0,79}',raw_code) else 'BROWSER_OPERATION_TIMEOUT' if isinstance(error,TimeoutError) else 'BROWSER_OPERATION_FAILED'
                unsafe=bool(getattr(error,'unsafe',False)) or (event['attempted'] and data.action in {'type','select','advance'})
                owner='tool' if defect else getattr(error,'owner','browser')
                if unsafe or defect or owner in {'authorization','browser','authentication'}:
                    self.terminal=code;self.terminal_owner=owner;self.terminal_unsafe=unsafe
                event.update(error=code,owner=owner,unsafe=unsafe)
                if getattr(error,'budget_kind',None):
                    event.update(budget_kind=error.budget_kind,actions_used=error.actions_used,actions_limit=error.actions_limit)
                if event['valid_approach'] and not unsafe and not self.terminal and code!='IDENTICAL_FAILED_APPROACH_USE_DIFFERENT_METHOD':
                    self.failed_approaches.setdefault((self.stage,event['field']),set()).add(event['approach'])
                result={'ok':False,'error':code,'owner':owner,'terminal':bool(self.terminal),'retry_safe':not self.terminal}
                return result,None
            finally:
                event['duration_ms']=round((monotonic()-started)*1000,3)
                event['remaining_browser_actions']=max(0,self.browser.config.max_actions-self.browser.trace.actions) if hasattr(self.browser,'config') and hasattr(self.browser.trace,'actions') else None
                inference=getattr(self,'inference_budget',None);permissions=getattr(self,'permissions',None)
                event['remaining_execution_budget']={
                    'browser_actions':event['remaining_browser_actions'],
                    'session_seconds':max(0,round(self.session_deadline-monotonic(),3)) if hasattr(self,'session_deadline') else None,
                    'model_requests':max(0,inference.max_requests-inference.requests) if inference else None,
                    'doer_tool_calls':max(0,permissions.max_tool_calls-permissions.tool_calls) if permissions else None,
                    'doer_turns':getattr(self,'turns_remaining',None),
                    'browser_operation_timeout_ms':getattr(self,'operation_timeout_ms',10000),
                }
                field=event.get('field')
                if field and result and isinstance(result.get('fields'),dict):
                    event['verification']={'expected_match':True,'actual_match':result['fields'].get(field)}
                from datetime import datetime,timezone
                # Full structural observations have their own evidence event. Keep
                # model/Teacher action history bounded and link to that evidence.
                event['result']={k:v for k,v in (result or {}).items() if k not in {'controls','options','observation'}}
                for key in ('controls','options'):
                    if result and key in result:
                        event['result'][key+'_count']=len(result[key])
                        event['result'][key+'_sample']=result[key][:8]
                        event['result'][key+'_omitted']=max(0,len(result[key])-8)
                event['timestamp_end']=datetime.now(timezone.utc).isoformat()
                if self.last_observation and self.last_observation.get('evidence_ref'):
                    event['evidence_refs'].append(self.last_observation['evidence_ref'])
                if result and result.get('evidence_ref'):event['evidence_refs'].append(result['evidence_ref'])
                event['trace_ref']=self.browser.trace.agent_event('browser_action',role='doer',tool='browser_action',
                    invocation_id=call,event_id=event['id'],**{k:v for k,v in event.items() if k!='id'})

    def expected_value(self, field):
        current = getattr(self, 'audience_option', None)
        if current and self.stage == 'audience' and current[0] == field:
            if current[1] not in self.payload.audience_values(field):
                from modules.whatsapp.contract import V2Stop
                raise V2Stop('AUDIENCE_OPTION_NOT_IN_APPROVED_PAYLOAD', owner='authorization')
            return current[1]
        return getattr(self.payload, field)

    async def inspect_options(self,entry):
        from modules.whatsapp.contract import V2Stop
        field=entry.get('field')
        if not field:raise V2Stop('DROPDOWN_FIELD_REQUIRED')
        await self.observe()
        binding=self.ui.fields[field]
        if binding.kind=='native_select':
            options=await self.browser.page.evaluate('(s=>{const a=document.querySelectorAll(s.q);if(a.length!==1||a[0].tagName!=="SELECT")return null;'+
                'return [...a[0].options].map((o,i)=>({index:i,exact:o.value===s.v||o.textContent.trim()===s.v,enabled:!o.disabled,existing:true}));})('+json.dumps({'q':binding.selector,'v':self.expected_value(field)})+')')
            if options is None:raise V2Stop('NATIVE_SELECT_NOT_CURRENT',owner='recognition')
            return {'options':options,'selection_verified':False,'control_reference':next((r for r,d in self.references.items() if d['field']==field and d['part']=='control'),None)}
        return {'options':[{'reference':r,'exact':d['exact'],'existing':not d['create']} for r,d in self.references.items()
            if d['field']==field and d['part']=='option']}

    async def select(self,entry,loc,field,event):
        from modules.whatsapp.contract import V2Stop
        value=self.expected_value(field);binding=self.ui.fields[field]
        if field=='waba' and not self.waba_opened:raise V2Stop('EXPLICIT_WABA_OPEN_REQUIRED',owner='recognition')
        if binding.kind=='radio':
            exact=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q);if(!e)return false;'+
                'const label=e.getAttribute("aria-label")||[...(e.labels||[])].map(n=>n.textContent).join(" ")||'+
                '(e.getAttribute("aria-labelledby")||"").split(/\\s+/).map(id=>document.getElementById(id)?.textContent||"").join(" ")||e.textContent||"";'+
                'return (e.type==="radio"||e.getAttribute("role")==="radio")&&(e.value===s.v||label.replace(/\\s+/g," ").trim()===s.v);})('+
                json.dumps({'q':entry['selector'],'v':value})+')')
            if exact is not True:raise V2Stop('RADIO_OPTION_NOT_EXACT',owner='recognition')
            event['valid_approach']=True
            checked=await self.browser.page.evaluate('(q=>{const e=document.querySelector(q);if(!e)return null;' +
                'if(e.type==="radio")return e.checked;if(e.getAttribute("role")==="radio")' +
                'return e.getAttribute("aria-checked")==="true"?true:e.getAttribute("aria-checked")==="false"?false:null;return null;})('+
                json.dumps(entry['selector'])+')')
            if type(checked) is not bool:raise V2Stop('RADIO_STATE_UNVERIFIED',owner='recognition')
            if not checked:event['attempted']=True;await loc.click(timeout=getattr(self,'operation_timeout_ms',3000))
        elif binding.kind=='native_select':
            # Resolve the option's actual value from this current observed select.
            options=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q);return e?.tagName==="SELECT"?'+
                '[...e.options].filter(o=>!o.disabled&&(o.textContent.trim()===s.v||o.value===s.v)).map(o=>o.value):[]})('+json.dumps({'q':binding.selector,'v':value})+')')
            event['valid_approach']=True
            if len(options)!=1:raise V2Stop('EXACT_EXISTING_OPTION_UNAVAILABLE',owner='recognition')
            event['attempted']=True;await loc.select_option([options[0]],timeout=getattr(self,'operation_timeout_ms',3000))
        elif binding.kind=='combobox' and entry['part']=='option' and entry['exact'] and not entry['create']:
            if not binding.option_selector:raise V2Stop('REVIEWED_EXISTING_OPTION_EVIDENCE_REQUIRED',owner='recognition')
            if field == 'tag' and getattr(self, 'page1_current_field', None) == 'tag':
                from modules.whatsapp.observation import TAG_CHIP_HELPERS
                expression = r'''(s=>{''' + TAG_CHIP_HELPERS + r'''
                  const options=document.querySelectorAll(s.q);
                  const e=options.length===1?options[0]:null;
                  if(!e||!e.matches(s.options)||e.closest('[data-create],[data-select2-tag=true],[aria-disabled=true]'))
                      return null;
                  const controls=document.querySelectorAll(s.control);
                  if(controls.length!==1)return null;
                  const control=controls[0];
                  const ids=((control.getAttribute('aria-controls')||'')+' '+(control.getAttribute('aria-owns')||''))
                    .split(/\s+/).filter(Boolean);
                  const rows=new Set(ids.flatMap(id=>[...document.getElementById(id)?.querySelectorAll(s.options)||[]]));
                  const matches=[...rows].map(row=>({row,chip:chipParts(row,'+')}))
                    .filter(item=>item.chip.label===s.value&&item.chip.action);
                  if(matches.length!==1||matches[0].row!==e)return null;
                  const {chip}=matches[0];
                  if(chip.action===e)return s.q;
                  const path=[];
                  for(let n=chip.action;n&&n!==e;n=n.parentElement){
                    const order=[...n.parentElement.children].indexOf(n)+1;
                    path.unshift(':nth-child('+order+')');
                  }
                  const selector=s.q+' > '+path.join(' > ');
                  return path.length&&document.querySelectorAll(selector).length===1&&
                      document.querySelector(selector)===chip.action?selector:null;
                })(SETTINGS)'''
                settings = {'q': entry['selector'], 'options': binding.option_selector, 'value': value,
                            'control': binding.selector}
                selector = await self.browser.page.evaluate(expression.replace('SETTINGS', json.dumps(settings)))
                if not isinstance(selector, str) or not selector:
                    raise V2Stop('EXACT_EXISTING_TAG_ADD_CONTROL_NOT_BOUND', owner='recognition')
                identity = await observed_node(self.browser, selector)
                if await observed_node(self.browser, entry['selector']) != entry['identity']:
                    raise V2Stop('TAG_OPTION_IDENTITY_CHANGED', owner='browser', unsafe=True)
                target = await stagehand_locator(self.browser, selector, identity)
                if await self.browser.page.evaluate(expression.replace('SETTINGS', json.dumps(settings))) != selector:
                    raise V2Stop('EXACT_EXISTING_TAG_ADD_CONTROL_NOT_BOUND', owner='recognition')
                event['valid_approach'] = event['attempted'] = True
                await target.click(timeout=getattr(self, 'operation_timeout_ms', 3000))
                return
            if (getattr(self, 'campaign_verified_stage', None) == self.stage
                    and field in {'include_audience', 'exclude_audience', 'template'}):
                from modules.whatsapp.observation import CAMPAIGN_PICKER_HELPERS
                expression = r'''(s=>{''' + CAMPAIGN_PICKER_HELPERS + r'''
                  const c=document.querySelector(s.control), e=document.querySelector(s.option);
                  if(!c||!e||campaignPickerOwners(s.stage)[s.field]!==c)return false;
                  const ids=((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
                  const rows=new Set(ids.flatMap(id=>[...document.getElementById(id)?.querySelectorAll('[role=option]')||[]]));
                  const matches=[...rows].filter(row=>campaignOptionExact(row,s.value,s.field));
                  return matches.length===1&&matches[0]===e&&e.getAttribute('aria-selected')!=='true';
                })(SETTINGS)'''
                settings = {'control': binding.selector, 'option': entry['selector'], 'field': field,
                            'stage': self.stage, 'value': value}
                permitted = await self.browser.page.evaluate(expression.replace('SETTINGS', json.dumps(settings)))
            else:
                permitted=await self.browser.page.evaluate('(s=>{const e=document.querySelector(s.q);return !!e&&e.matches(s.options)&&!e.hasAttribute("data-create")&&e.getAttribute("data-select2-tag")!=="true"&&e.textContent.trim()===s.value})('+json.dumps({'q':entry['selector'],'options':binding.option_selector,'value':value})+')')
            event['valid_approach']=True
            if permitted is not True:raise V2Stop('EXACT_EXISTING_OPTION_UNAVAILABLE',owner='recognition')
            if field == 'template' and getattr(self, 'campaign_verified_stage', None) == 'content':
                if await observed_node(self.browser, entry['selector']) != entry['identity']:
                    raise SetupStop('STALE_CONTROL_REFERENCE')
            event['attempted']=True;await loc.click(timeout=getattr(self,'operation_timeout_ms',3000))
        else:raise V2Stop('EXACT_EXISTING_OPTION_REQUIRED',owner='recognition')

    def action_schema(self):
        from modules.whatsapp.contract import (V2Action, V2DiagnosticAction, V2DiscoveryAction,
            V2DiscoveryNoVisionAction, V2ManualAction, V2ManualNoVisionAction, V2SetupReadOnlyAction,
            V2CampaignAudienceAction, V2CampaignContentAction, V2GATrackingAction, V2GADisabledAction)

        if getattr(self, 'diagnostic_mode', False):
            return V2DiagnosticAction
        authorization = getattr(self, 'authorization', None)
        vision = (getattr(authorization, 'vision', False) and
                  bool(getattr(getattr(self, 'ui', None), 'visual_scope_selector', None)))
        if self.read_only:
            if getattr(self, 'human_assisted_setup', False):
                return V2SetupReadOnlyAction
            return V2DiscoveryAction if vision else V2DiscoveryNoVisionAction
        if getattr(authorization, 'manual_form_test', False):
            if getattr(self, 'ga_setup_fields', None):
                return V2GATrackingAction if self.payload.ga_tracking else V2GADisabledAction
            stage = getattr(self, 'stage', None)
            if (getattr(self, 'operator_setup_binding', None) is not None and
                    getattr(self, 'campaign_verified_stage', None) == stage):
                if stage == 'audience':
                    return V2CampaignAudienceAction
                if stage == 'content':
                    return V2CampaignContentAction
            return V2ManualAction if vision else V2ManualNoVisionAction
        return V2Action

    def reject_schema(self, arguments):
        from modules.whatsapp.contract import v2_schema_rejection

        detail = v2_schema_rejection(self.action_schema(), arguments)
        self.browser.trace.agent_event('browser_action_rejected', role='doer', tool='browser_action',
            error='INVALID_BROWSER_ACTION_ARGUMENTS', browser_interaction=False, **detail)
        return {'ok': False, 'error': 'INVALID_BROWSER_ACTION_ARGUMENTS', 'owner': 'tool',
                'retry_safe': True, **detail}

    @staticmethod
    def compact_tool_observation(result):
        # Full redacted observation remains in its own trace event. Keep all
        # supported action references and drop repeated scan diagnostics.
        if 'observation' in result:
            result = {**result, 'observation': V2BrowserTools.compact_tool_observation(result['observation'])}
        if 'controls' not in result:
            return result
        control_keys = ('field', 'part', 'reference', 'enabled', 'expanded', 'exact',
                        'selected_exact', 'checked', 'create', 'role', 'tag', 'type')
        controls = [{key: node[key] for key in control_keys if key in node}
                    for node in result['controls'] if node.get('field') and not node.get('create')]
        keys = ('ok', 'stage', 'stage_visible', 'field_evidence', 'unresolved_fields', 'recognition_status',
                'permitted_next_operations', 'evidence_ref', 'truncated', 'pending_write', 'scoped_option_evidence')
        return {**{key: result[key] for key in keys if key in result}, 'controls': controls}

    def registered_tools(self):
        from langchain_core.tools import StructuredTool
        from modules.whatsapp.contract import v2_action_arguments

        def validation_error(error):
            item = next((item for item in error.errors() if isinstance(item.get('input'), dict)), {})
            return json.dumps(self.reject_schema(item.get('input', {})))

        async def invoke(**arguments):
            # StructuredTool has already validated supplied fields, then expands
            # omitted nullable defaults. Do not turn those into supplied fields
            # during the deterministic action boundary's second validation.
            arguments={key:value for key,value in arguments.items() if value is not None}
            result,raw_image=await self.action(arguments)
            if getattr(self, 'human_assisted_setup', False):
                result = self.compact_tool_observation(result)
                field = getattr(self, 'page1_current_field', None)
                if field is not None:
                    for observation in (result, result.get('observation') or {}):
                        if 'controls' in observation:
                            observation['controls'] = [item for item in observation['controls'] if item.get('field') == field]
            content=[{'type':'text','text':json.dumps(result)}]
            if raw_image:content.append({'type':'image_url','image_url':{'url':'data:image/png;base64,'+raw_image}})
            return content,{'image_supplied':raw_image is not None}
        diagnostic=getattr(self,'diagnostic_mode',False)
        description=('Payload-free supervised diagnostic: observe, inspect/read back redacted current state, scroll, or navigate only with a fresh observed WhatsApp link reference. No screenshot, DOM dump, field, value, URL, write, campaign-record or persistence action is available.'
            if diagnostic else
            'Observe uses a deterministic Stagehand accessibility snapshot and semantic labels; dom freshly inspects structural DOM labels. Wait is bounded rendering observation. Inspect, scroll, options and verify use current references. Click focuses text/search controls or opens an owned dropdown; type applies fixed text/checkbox values; search uses the fixed approved value; select chooses an exact existing dropdown/radio option. Screenshots require separate reviewed vision scope. In read-only mode navigate uses an observed link reference. Next Step may be inspected; only a separately authorized controller can advance/save. Never supply values/selectors.')
        schema = self.action_schema()
        if getattr(self, 'human_assisted_setup', False):
            description = ('Protected WhatsApp Setup. Observe semantic labels or use dom for structural labels. '
                'Use fresh observed references only. Inspect takes reference only. '
                'Use options for candidate options and verify for actual field readback. '
                'The contract fixes all input values and excludes persistence.')
        signature = '; '.join(action + '(' + ', '.join(
            field if field in required else field + ' optional' for field in allowed) + ')'
            for action, (required, allowed) in v2_action_arguments(schema).items())
        description += ' Allowed action signatures: ' + signature + '. Omit unrelated arguments.'
        return [StructuredTool.from_function(coroutine=invoke,name='browser_action',
            description=description,args_schema=schema,response_format='content_and_artifact',
            handle_validation_error=validation_error)]
