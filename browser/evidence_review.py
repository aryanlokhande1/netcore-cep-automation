"""Operator-owned evidence preview/approval within the already authorized browser."""
import asyncio
import hashlib
import json
import sys
from time import monotonic
from pathlib import Path
from app.safety import require_live_browser
from modules.login.evidence import EvidenceConfig
from modules.login.verifier import verify
from browser.evidence_diagnostics import EvidenceFailure,ApprovalFailure,evidence_diagnostic,review_event,gate_summary,operator_event

class EvidenceReview:
    def __init__(self,browser):
        require_live_browser(browser)
        if not browser.config.evidence_file:raise EvidenceFailure('MISSING_EVIDENCE_CONFIGURATION')
        self.browser=browser;self.path=Path(browser.config.evidence_file)
        self.preview_digest=None
        self.preview_context=None
    async def resource_options(self):
        """Observed, neutral options only; no selectors/endpoints or auth grant."""
        require_live_browser(self.browser)
        discovery=getattr(self.browser,'discovery',None)
        if discovery is None:raise ApprovalFailure('PASSIVE_DISCOVERY_REQUIRED')
        await discovery.protected(consume=False)
        return {'status':'RESOURCE_CONTRACT_REVIEW_REQUIRED','trusted':False,
            'digest':discovery.contract_choices_digest,
            'options':[{'option':i+1,'resource':c['resource'],
                'key':c['binding']['authenticated_json_key'],'observed_value':c['observed_value'],
                'value_sha256':c['binding']['authenticated_value_sha256']}
                for i,c in enumerate(discovery.contract_choices)],
            'meaning':'Observed value is not authentication proof. Select only if its protected-session meaning is independently understood; A still requires all gates.'}

    async def select_resource_contract(self,option,digest,*,confirmed=False):
        require_live_browser(self.browser)
        discovery=getattr(self.browser,'discovery',None)
        if not confirmed or discovery is None or digest is None or digest!=discovery.contract_choices_digest:
            raise ApprovalFailure('RESOURCE_CONTRACT_SELECTION_NOT_REVIEWED')
        if type(option) is not int or not 1<=option<=len(discovery.contract_choices):
            raise ApprovalFailure('RESOURCE_CONTRACT_OPTION_UNOBSERVED')
        await discovery.protected(consume=False)
        choice=discovery.contract_choices[option-1]
        if choice['context']!=await self.browser.authentication_context():raise ApprovalFailure('RESOURCE_CONTRACT_CONTEXT_CHANGED')
        resource=next((r for r in discovery.resources if r['alias']==choice['resource']),None)
        if resource is None or discovery.current_resource(resource)!='CURRENT_DOCUMENT':raise ApprovalFailure('RESOURCE_CONTRACT_RESOURCE_STALE')
        binding=choice['binding'];facts=resource.get('facts',{})
        if facts.get('negative_authentication_keys'):raise ApprovalFailure('NEGATIVE_AUTHENTICATION_RESOURCE')
        if facts.get('scalar_hashes',{}).get(binding['authenticated_json_key'])!=binding['authenticated_value_sha256'] or binding['tenant_json_key'] not in facts.get('tenant_keys',[]):
            raise ApprovalFailure('RESOURCE_CONTRACT_FACTS_CHANGED')
        if self.path.exists() and json.loads(self.path.read_bytes()).get('reviewed') is True:
            raise ApprovalFailure('REVIEWED_CONFIGURATION_REPLACEMENT_REQUIRES_REVIEW')
        candidate=EvidenceConfig(reviewed=False,**binding,
            authentication_contract_reference='OPERATOR_OBSERVED_CONTRACT:'+self.browser.trace.run_id+':'+str(choice['resource'])+':'+digest)
        raw=candidate.model_dump_json(indent=2).encode()
        self.path.write_bytes(raw);self.preview_digest=None;self.preview_context=None
        return {'status':'SELECTED_UNREVIEWED','trusted':False,'digest':hashlib.sha256(raw).hexdigest(),
            'operator_approval_required':True,'verification_basis':'OBSERVED_RESOURCE_EXPLICIT_SELECTION_ONLY'}
    async def context(self,candidate):
        current=await self.browser.authentication_context()
        if current is None:return None
        discovery=getattr(self.browser,'discovery',None)
        matching=[r for r in getattr(discovery,'resources',[]) if r['path']==candidate.readonly_path]
        resource=max(matching,key=lambda r:r['alias'])['alias'] if matching else None
        return current,resource
    async def operator_candidates(self,kind):
        require_live_browser(self.browser)
        discovery=getattr(self.browser,'discovery',None)
        if discovery is None:raise ApprovalFailure('PASSIVE_DISCOVERY_REQUIRED')
        return await discovery.operator_candidates(kind)
    async def bind_candidate(self,reference):
        require_live_browser(self.browser)
        discovery=getattr(self.browser,'discovery',None)
        if discovery is None:raise ApprovalFailure('PASSIVE_DISCOVERY_REQUIRED')
        result=await discovery.bind_candidate(reference)
        operator_event(self.browser,'CANDIDATE_BINDING',result)
        return result
    async def preview(self):
        require_live_browser(self.browser)
        try:raw=self.path.read_bytes()
        except FileNotFoundError:
            # A missing draft must not hide the host/negative-state observation.
            self.preview_digest=None
            observation=await self.browser.observe()
            details=self.browser.verification_details or {}
            return {'status':'PENDING_OPERATOR_OBSERVATION','trusted':False,
                'verification':str(verify(observation)),'gate_status':details.get('gate_status',gate_summary(observation)),
                'negative_states':negative_summary(observation,details),'issues':['MISSING_EVIDENCE_CONFIGURATION'],
                'missing':details.get('missing',[]),'next_steps':['DISCOVER_THEN_REVIEW_PRIVATE_INVENTORY']}
        candidate=EvidenceConfig.parse_document(raw,allow_unreviewed=True)
        url=await self.browser.page.url()
        from urllib.parse import urlsplit
        if urlsplit(url).hostname!=self.browser.config.expected_host or not self.browser.policy.permits(url):
            raise PermissionError('Evidence preview requires the approved destination')
        original=self.browser.evidence
        original_details=self.browser.verification_details
        digest=hashlib.sha256(raw).hexdigest()
        context=await self.context(candidate)
        try:
            self.browser.evidence=candidate.model_copy(update={'reviewed':True})
            # Use the same origin guard and browser-action budget as ordinary verification.
            from modules.login.evidence import stable_authentication
            observation=await stable_authentication(self.browser,prospective=True)
            details=dict(self.browser.verification_details or {})
        finally:
            self.browser.evidence=original
            self.browser.verification_details=original_details
        self.preview_digest=digest
        self.preview_context=context
        statuses=details.get('gate_status',gate_summary(observation)).copy()
        statuses['evidence_configured']='PASS' if candidate.reviewed else 'FAIL'
        return {'status':'READY_FOR_EXPLICIT_APPROVAL' if verify(observation)=='AUTHENTICATED_AND_VERIFIED' else 'PENDING_OPERATOR_OBSERVATION',
            'trusted':False,'digest':digest,'verification':str(verify(observation)),
            'verification_basis':'PROSPECTIVE_APPROVAL_CHECK',
            'page_context':details.get('page_context'),'scope_certificate':details.get('scope_certificate'),
            'provenance':'CURRENT_PROTECTED_DOM_AND_OBSERVED_RESOURCE',
            'gates':{k:getattr(observation,k) for k in gate_summary(observation)},
            'gate_status':statuses,'negative_states':negative_summary(observation,details),
            'issues':details.get('issues',[])+([] if candidate.reviewed else ['EVIDENCE_CONFIGURATION_UNREVIEWED']),
            'missing':list(dict.fromkeys(details.get('missing',[])+([] if candidate.reviewed else ['evidence_configured'])))}
    async def approve(self,digest,approved=False):
        if not approved or digest!=self.preview_digest or hashlib.sha256(self.path.read_bytes()).hexdigest()!=digest:
            raise ApprovalFailure('APPROVAL_DIGEST_MISMATCH')
        require_live_browser(self.browser)
        from urllib.parse import urlsplit
        url=await self.browser.page.url()
        if urlsplit(url).hostname!=self.browser.config.expected_host or not self.browser.policy.permits(url):
            raise PermissionError('Destination changed after preview')
        raw=self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=digest:
            raise ApprovalFailure('APPROVAL_DIGEST_MISMATCH')
        candidate=EvidenceConfig.parse_document(raw,allow_unreviewed=True)
        if self.preview_context is None or self.preview_context!=await self.context(candidate):
            raise ApprovalFailure('APPROVAL_CONTEXT_CHANGED')
        # Recheck all gates with the exact operator-reviewed document before trusting it.
        current=await self.preview()
        if current['digest']!=digest or not all(current['gates'].values()) or current['verification']!='AUTHENTICATED_AND_VERIFIED':
            raise ApprovalFailure('INDEPENDENT_EVIDENCE_INCOMPLETE')
        if hashlib.sha256(self.path.read_bytes()).hexdigest()!=digest:
            raise ApprovalFailure('APPROVAL_DIGEST_MISMATCH')
        # Only this explicit operator action changes the private reviewed flag; never .env.
        candidate=candidate.model_copy(update={'reviewed':True})
        original=self.browser.evidence
        self.browser.evidence=candidate
        try:
            from modules.login.evidence import stable_authentication
            observation=await stable_authentication(self.browser,prospective=True)
            result=verify(observation)
            if result!='AUTHENTICATED_AND_VERIFIED':raise ApprovalFailure('INDEPENDENT_EVIDENCE_INCOMPLETE')
            if hashlib.sha256(self.path.read_bytes()).hexdigest()!=digest:raise ApprovalFailure('APPROVAL_DIGEST_MISMATCH')
            if self.browser.guard and self.browser.guard.failed:raise ApprovalFailure('NAVIGATION_GUARD_UNAVAILABLE')
            if self.preview_context!=await self.context(candidate):raise ApprovalFailure('APPROVAL_CONTEXT_CHANGED')
            self.path.write_text(candidate.model_dump_json(indent=2),encoding='utf-8')
        except BaseException:
            self.browser.evidence=original
            raise
        self.browser.trace.add('verify',observation,result)
        self.browser.trace.lifecycle('LOGIN_READINESS',{'state':'AUTHENTICATED_AND_VERIFIED',
            'verification_basis':'EXPLICIT_DIGEST_APPROVAL_AND_CURRENT_EVIDENCE','trusted':True,'observations':2})
        if str(result)=='AUTHENTICATED_AND_VERIFIED':self.browser.trace.fact('DESTINATION_VERIFIED')
        return observation


def negative_summary(observation,details=None):
    unavailable='PERMITTED_DOCUMENT_UNOBSERVABLE' in (details or {}).get('issues',[])
    return {key:'FAIL' if getattr(observation,key) else 'NOT_CHECKED' if unavailable else 'PASS' for key in
        ('credential_form','challenge','rejected','expired','account_restricted','login_entry')}

def discovery_terminal_summary(discovery, readiness):
    dom = discovery.get('dom_diagnostics') or {}
    scope = discovery.get('scope_certificate') or {}
    context = discovery.get('page_context') or {}
    summary = {key: discovery[key] for key in (
        'status', 'code', 'stage', 'transport_status', 'dashboard_candidates', 'tenant_candidates',
        'observed_json_resources', 'supported_json_resources', 'resource_contract_options',
        'unreviewed_draft_written', 'gaps', 'browser_actions_remaining', 'documents_inspected',
        'permitted_frames_unavailable', 'external_frames_skipped', 'json_support_counts',
        'resource_document_status_counts', 'budget_kind', 'actions_used', 'actions_limit',
        'render_comparison_status', 'render_comparison_screenshot', 'render_comparison_ax_ignored') if key in discovery}
    resource_keys = ('resource', 'state', 'support', 'document_status', 'query_bearing',
                     'authenticated_indicator_count', 'exact_tenant_indicator_count', 'json_traversal_limit_reached')
    resources = discovery.get('resource_diagnostics') or []
    summary.update(
        approval='REQUIRES_SUCCESSFUL_PREVIEW',
        readiness={key: readiness[key] for key in ('state', 'elapsed_ms', 'polls', 'shell_landmarks') if key in readiness},
        document={key: context[key] for key in ('primary_document_coherent', 'sdk_page_active', 'expected_host',
                                                'primary_target_alias', 'primary_session_alias', 'document_generation')
                  if key in context},
        elements_inspected=dom.get('inspected_elements'),
        dom_rejections=dom.get('rejections', {}),
        dashboard_visibility_rejections=dom.get('dashboard_visibility_rejections', {}),
        exact_tenant_rejections=dom.get('exact_tenant_rejections', {}),
        protected_scope={key: scope[key] for key in ('state', 'certified', 'document_current',
            'inventory_documents_inspected', 'inaccessible_shadow_roots', 'inventory_changed',
            'inventory_complete', 'negative_scope_complete') if key in scope},
        resources=[{key: item[key] for key in resource_keys if key in item} for item in resources[:12]],
        omitted_resources=max(0, len(resources) - 12))
    return summary


async def evidence_review_hold(browser,seconds):
    if not sys.stdin.isatty() or not 1<=seconds<=300:raise ValueError('Interactive bounded evidence review required')
    review=EvidenceReview(browser)
    print('Evidence review: D discovers; R lists observed resource contracts, then 1-9 selects an understood contract as unreviewed; P previews; A explicitly approves the unchanged digest after all gates pass; Enter closes. No credential resubmission.',flush=True)
    deadline=monotonic()+seconds
    async def operation(stage,callback):
        try:data=await asyncio.wait_for(callback(),max(.01,deadline-monotonic()))
        except Exception as error:data=evidence_diagnostic(error,stage,browser)
        recorded=review_event(browser,stage,data)
        if stage == 'DISCOVERY':
            shown = discovery_terminal_summary(recorded, getattr(browser, 'login_readiness', None) or {})
            print('REVIEW DISCOVERY ' + json.dumps(shown), flush=True)
        elif getattr(browser,'rendering_diagnostics_enabled',False):
            shown={k:v for k,v in recorded.items() if k in {'status','code','verification','gate_status','gates',
                'digest','options','meaning','gaps','issues','resource_contract_options','render_comparison_status','render_comparison_screenshot'}}
            print('REVIEW '+stage+' '+json.dumps(shown),flush=True)
        else:print(json.dumps(recorded),flush=True)
        return data
    if getattr(browser,'discovery',None):
        await operation('DISCOVERY',browser.discovery.prepare)
    pending_selection=None
    approval_digest=None
    while monotonic()<deadline:
        key=None
        if sys.platform=='win32':
            import msvcrt
            if msvcrt.kbhit():key=msvcrt.getwch().lower()
        else:
            import select
            if select.select([sys.stdin],[],[],0)[0]:key=sys.stdin.readline().strip().lower() or '\n'
        if key in ('\r','\n'):return None
        if pending_selection and key in tuple('123456789'):
            option=int(key);selected_digest=pending_selection['digest'];pending_selection=None
            approval_digest=None
            await operation('SELECTION',lambda:review.select_resource_contract(option,selected_digest,confirmed=True))
        if key=='r':
            options=await operation('RESOURCE_OPTIONS',review.resource_options)
            pending_selection=options if options.get('digest') and options.get('options') else None
            print('Choose 1-9 only if you understand the observed protected-resource contract. Selection is not approval; press P then A to verify and approve.',flush=True)
        if key=='d' and getattr(browser,'discovery',None):
            pending_selection=None
            approval_digest=None
            await operation('DISCOVERY',browser.discovery.prepare)
        if key=='p':
            preview=await operation('PREVIEW',review.preview)
            approval_digest=preview.get('digest') if preview['status']=='READY_FOR_EXPLICIT_APPROVAL' else None
        if key=='a':
            if approval_digest is None:
                diagnostic=evidence_diagnostic(ApprovalFailure('PREVIEW_REQUIRED_BEFORE_APPROVAL'),'APPROVAL',browser)
                print(json.dumps(review_event(browser,'APPROVAL',diagnostic)),flush=True)
                continue
            try:
                observed=await asyncio.wait_for(review.approve(approval_digest,approved=True),max(.01,deadline-monotonic()))
                print(json.dumps(review_event(browser,'APPROVAL',{'status':'APPROVED','gates':gate_summary(observed),
                    'negative_states':negative_summary(observed),'trusted':True})),flush=True)
                print('Reviewed Login evidence saved to ' + str(review.path.resolve()), flush=True)
                return observed
            except Exception as error:print(json.dumps(review_event(browser,'APPROVAL',evidence_diagnostic(error,'APPROVAL',browser))),flush=True)
        await asyncio.sleep(.05)
    raise TimeoutError('Evidence review deadline')
