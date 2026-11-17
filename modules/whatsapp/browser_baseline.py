"""Isolated deterministic baseline. No agent, Teacher, page classifier or inventory."""
import asyncio
import json
import re
from urllib.parse import urlsplit
from browser.observed_control import protected_document
from modules.login.negative_scan import NEGATIVE_SNAPSHOT
from modules.whatsapp.contract import SETUP_URL,VALUES,FIELD_FACTS
from modules.whatsapp.browser_baseline_dom import DISCOVER,COMMIT_BLOCK,lookup


class BaselineStop(Exception):
    def __init__(self,code,**diagnostic):
        super().__init__(code);self.code=code;self.diagnostic=diagnostic


def route_allowed(url,destination=SETUP_URL):
    try:
        actual,target=urlsplit(url),urlsplit(destination)
        return bool(not actual.username and not actual.password and '\\' not in url and not any(ord(c)<33 for c in url)
            and actual.scheme==target.scheme and actual.hostname==target.hostname
            and (actual.port or (443 if actual.scheme=='https' else 80))==(target.port or (443 if target.scheme=='https' else 80))
            and actual.port!=0 and actual.path==target.path and actual.query==target.query and actual.fragment in {'','setup'})
    except (ValueError,TypeError):return False


def sanitized_action_error(error,operation):
    """RPC messages/data may embed selectors/values; never retain them."""
    if isinstance(error,BaselineStop):return {'code':error.code,'operation':operation,**error.diagnostic}
    raw=getattr(error,'code',None)
    code=raw if isinstance(raw,int) or isinstance(raw,str) and re.fullmatch(r'[A-Z_0-9-]{1,64}',raw) else None
    message=str(error).lower()  # transient classification only
    category='TIMEOUT' if 'timeout' in message or isinstance(error,TimeoutError) else 'LOCATOR_NOT_UNIQUE' if 'strict mode' in message else 'BROWSER_OPERATION_FAILED'
    return {'code':category,'operation':operation,'exception_category':type(error).__name__,'rpc_code':code,
        'raw_exception_omitted':True}


async def reject_security_negatives(browser):
    """Existing accessible negative predicates, flags only; never inventory."""
    safety=await browser.page.evaluate(NEGATIVE_SNAPSHOT.replace('SETTINGS',json.dumps({'origins':sorted(browser.policy.origins)})))
    flags=safety.get('flags',{}) if isinstance(safety,dict) else {}
    negatives=('credential_form','challenge','rejected','expired','account_restricted')
    detected=[k for k in negatives if flags.get(k) is True]
    if detected:raise BaselineStop('AUTHENTICATION_OR_SECURITY_STOP',failed_predicates=detected)
    if not all(type(flags.get(k)) is bool for k in negatives):raise BaselineStop('SECURITY_OBSERVATION_INVALID')


def field_reference(discovery,field):
    """Choose only a current associated field, including proven readonly values."""
    candidates=[c for c in discovery['controls'] if c.get('field_hint')==field and c.get('visible')]
    eligible=[c for c in candidates if c.get('enabled') or c.get('matches_required_value') and c.get('owned')]
    if any(c.get('failure_code')=='BACKING_SELECTION_CONFLICT' for c in candidates):
        raise BaselineStop('BACKING_SELECTION_CONFLICT',field=field,field_diagnostic=discovery.get('field_diagnostics',{}).get(field,{}))
    if len(eligible)==1:return eligible[0]['reference']
    diagnostic=discovery.get('field_diagnostics',{}).get(field,{})
    code='FIELD_CONTROL_AMBIGUOUS' if len(eligible)>1 else 'READ_ONLY_SELECTION_NOT_VERIFIED' if candidates else 'FIELD_CONTROL_UNRESOLVED'
    raise BaselineStop(code,field=field,matches=len(eligible),field_diagnostic=diagnostic)


class BrowserBaseline:
    def __init__(self,browser,destination=SETUP_URL,*,values=None,commit_guard=None):
        self.browser=browser;self.destination=destination;self.controls={};self.references={}
        self.values=dict(VALUES if values is None else values)
        self.commit_guard=commit_guard
        self.verified=set();self.stage='DISCOVERY';self.write_attempted=False;self.stopped=False;self.campaign_reference=None
        self.last_discovery=None;self.latest_field_diagnostics={}
        self.interactive=None;self.widget_prompt=None

    async def protect(self):
        if self.stopped:raise BaselineStop('BASELINE_STOPPED_NO_RETRY')
        await self.browser.guard.ensure_observable()
        document=protected_document(self.browser)
        if not route_allowed(await self.browser.page.url(),self.destination):raise BaselineStop('WRONG_SETUP_ROUTE')
        self.browser.budget()
        return document

    async def _snapshot(self):
        before=await self.protect()
        await reject_security_negatives(self.browser)
        data=await self.browser.page.evaluate(DISCOVER.replace('SETTINGS',json.dumps({'values':self.values})))
        if not isinstance(data,dict) or not isinstance(data.get('controls'),list):raise BaselineStop('DISCOVERY_INVALID')
        if data.get('access_denied'):raise BaselineStop('ACCESS_DENIED')
        if protected_document(self.browser)!=before:raise BaselineStop('CURRENT_DOCUMENT_CHANGED')
        await self.protect()
        self.latest_field_diagnostics=data.get('field_diagnostics',{})
        return data

    async def inspect_widgets(self):
        from modules.whatsapp.interactive_widgets import InteractiveWidgets
        if self.interactive is None:self.interactive=InteractiveWidgets(self,self.widget_prompt)
        return {field:(await self.interactive.inspect(field))['public'] for field in ('waba','business_number','tags')}

    async def _identity(self,descriptor):
        before=await self.protect();object_id=None
        try:
            response=await self.browser.guard.send('Runtime.evaluate',{'expression':lookup(descriptor['path']),'returnByValue':False},before[1])
            object_id=response.get('result',{}).get('objectId')
            if not object_id:raise BaselineStop('OBSERVED_CONTROL_UNAVAILABLE')
            node=await self.browser.guard.send('DOM.describeNode',{'objectId':object_id,'depth':0},before[1])
            backend=node['node']['backendNodeId']
            if type(backend) is not int or backend<=0:raise BaselineStop('CONTROL_IDENTITY_UNAVAILABLE')
            if protected_document(self.browser)!=before:raise BaselineStop('CURRENT_DOCUMENT_CHANGED')
            return (*before,backend)
        finally:
            if object_id:await self.browser.guard.send('Runtime.releaseObject',{'objectId':object_id},before[1])

    async def discover(self):
        data=await self._snapshot();visible=[]
        for descriptor in data['controls']:
            if not descriptor.get('path') or not descriptor.get('visible'):continue
            identity=await self._identity(descriptor)
            ref=self.references.get(identity)
            if ref is None:ref='control_'+str(len(self.references)+1);self.references[identity]=ref
            evidence=[]
            for p in descriptor.get('evidence_paths',[]):
                if p!=descriptor['path']:evidence.append((p,await self._identity({'path':p})))
            self.controls[ref]={'descriptor':descriptor,'identity':identity,'evidence':evidence}
            visible.append({'reference':ref,**{k:descriptor.get(k) for k in
                ('field_hint','visible_label','accessible_label','label_source','label_class','label_present','kind','visible','enabled',
                 'read_only','owned','ownership_confidence','selection_evidence','failure_code')},
                'matches_required_value':descriptor.get('selected') is True,
                'matching_existing_options':len(descriptor.get('options',[]))})
        self.last_discovery={'controls':visible,**{k:data[k] for k in ('headings','heading_labels','setup_active','active_step','legacy_identity',
            'traversal_complete','open_roots_inspected','html_form_present')},
            'field_diagnostics':data.get('field_diagnostics',{}),'global_shadow_certified':False,'model_requests':0}
        return self.last_discovery

    async def _target(self,reference,field,*,allow_readonly=False):
        entry=self.controls.get(reference)
        if not entry:raise BaselineStop('UNOBSERVED_CONTROL')
        data=await self._snapshot()
        if not data.get('setup_active'):raise BaselineStop('DIRECT_SETUP_EVIDENCE_INCOMPLETE')
        current=[d for d in data['controls'] if d.get('path')==entry['descriptor']['path']]
        if len(current)!=1:raise BaselineStop('OBSERVED_CONTROL_UNAVAILABLE')
        descriptor=current[0]
        if descriptor.get('failure_code')=='BACKING_SELECTION_CONFLICT':raise BaselineStop('BACKING_SELECTION_CONFLICT',field=field)
        readonly_verified=allow_readonly and field!='campaign_name' and descriptor.get('selected') and descriptor.get('owned')
        if not descriptor.get('visible') or not descriptor.get('safe') or not descriptor.get('enabled') and not readonly_verified:
            raise BaselineStop('TARGET_NOT_VISIBLE_ENABLED_SAFE',field=field,failure_code=descriptor.get('failure_code'))
        if descriptor.get('field_hint')!=field:raise BaselineStop('OBSERVED_LABEL_DOES_NOT_ESTABLISH_FIELD')
        if field=='campaign_name' and not descriptor.get('visible_label'):raise BaselineStop('VISIBLE_CAMPAIGN_LABEL_NOT_ESTABLISHED')
        if descriptor.get('has_other_tags'):raise BaselineStop('OTHER_EXISTING_TAGS_PRESENT')
        if await self._identity(descriptor)!=entry['identity']:raise BaselineStop('CURRENT_CONTROL_CHANGED')
        for p,identity in entry.get('evidence',[]):
            if p not in descriptor.get('evidence_paths',[]) or await self._identity({'path':p})!=identity:
                raise BaselineStop('FIELD_OWNERSHIP_EVIDENCE_CHANGED',field=field)
        # Installed Stagehand searches each shadow root independently. A CSS
        # ancestor chain cannot cross a host boundary. The actual root-local
        # selector must match exactly once across the SDK's complete search;
        # the private root chain independently pins that same node.
        locator=self.browser.page.locator(descriptor['path'][-1])
        count=await locator.count(timeout=2000)
        if count!=1:raise BaselineStop('LOCATOR_NOT_FOUND' if count==0 else 'LOCATOR_NOT_UNIQUE',matches=count)
        if not await locator.is_visible(timeout=2000):raise BaselineStop('TARGET_NOT_VISIBLE')
        if await self._identity(descriptor)!=entry['identity']:raise BaselineStop('CURRENT_CONTROL_CHANGED')
        return descriptor,locator

    async def _commit_block(self):
        if self.commit_guard is not None:
            await self.commit_guard();return
        if not await self.browser.page.evaluate(COMMIT_BLOCK):raise BaselineStop('COMMIT_BLOCK_UNAVAILABLE')

    async def fill_campaign(self,reference,authorized=False):
        self.stage='CAMPAIGN_LOCATE'
        if authorized is not True:raise BaselineStop('OPERATOR_CAMPAIGN_AUTHORIZATION_REQUIRED')
        if 'campaign_name' in self.verified or self.write_attempted:raise BaselineStop('CAMPAIGN_WRITE_RETRY_FORBIDDEN')
        try:
            descriptor,locator=await self._target(reference,'campaign_name')
            if descriptor['kind']!='TEXT_INPUT':raise BaselineStop('CAMPAIGN_TEXT_INPUT_NOT_ESTABLISHED')
            await self._commit_block();await self._target(reference,'campaign_name')
            self.stage='CAMPAIGN_FILL';self.write_attempted=True
            await locator.fill(self.values['campaign_name'],timeout=3000)
            self.stage='CAMPAIGN_READBACK';await self._target(reference,'campaign_name')
            if await locator.input_value(timeout=2000)!=self.values['campaign_name']:raise BaselineStop('CAMPAIGN_EXACT_READBACK_FAILED')
            current,_=await self._target(reference,'campaign_name')
            if not current['selected']:raise BaselineStop('CAMPAIGN_EXACT_READBACK_FAILED')
            self.verified.add('campaign_name');self.campaign_reference=reference;print('CAMPAIGN_NAME_VERIFIED',flush=True)
            return {'ok':True,'status':'CAMPAIGN_NAME_VERIFIED','exact_readback':True,'unsaved':True}
        except Exception:self.stopped=True;raise

    async def complete_remaining(self,mapping=None,authorized=False):
        if 'campaign_name' not in self.verified:raise BaselineStop('CAMPAIGN_MUST_SUCCEED_FIRST')
        if authorized is not True:raise BaselineStop('OPERATOR_REMAINING_FIELDS_AUTHORIZATION_REQUIRED')
        if mapping is not None and set(mapping)!={'waba','business_number','tags'}:raise BaselineStop('EXACT_REMAINING_FIELD_MAP_REQUIRED')
        if self.verified-{'campaign_name'}:raise BaselineStop('REMAINING_WRITE_RETRY_FORBIDDEN')
        try:
            resolved={}
            for field in ('waba','business_number','tags'):
                for prior,ref in {'campaign_name':self.campaign_reference,**resolved}.items():
                    if ref=='INTERACTIVE':
                        await self.interactive.readback(prior);continue
                    current,_=await self._target(ref,prior,allow_readonly=prior!='campaign_name')
                    if not current['selected']:raise BaselineStop('VERIFIED_PREREQUISITE_CHANGED',field=prior)
                self.stage=field.upper()+'_LOCATE'
                # Dependent controls are mapped only after the preceding field
                # actually verifies. No all-fields-present prerequisite at F.
                for attempt in range(4):
                    discovery=await self.discover()
                    try:
                        reference=mapping[field] if mapping is not None else field_reference(discovery,field)
                        break
                    except BaselineStop as error:
                        if error.code!='FIELD_CONTROL_UNRESOLVED':raise
                        if attempt==3:
                            reference=None;break
                        print('FIELD_RENDER_PENDING field='+field+' read_attempt='+str(attempt+1),flush=True)
                        await asyncio.sleep(.25)  # read-only readiness; never retry an action
                if reference is None or not self.controls[reference]['descriptor'].get('owned'):
                    from modules.whatsapp.interactive_widgets import InteractiveWidgets
                    if self.interactive is None:self.interactive=InteractiveWidgets(self,self.widget_prompt)
                    await self.interactive.complete(field,authorized=True)
                    resolved[field]='INTERACTIVE';self.verified.add(field);print(FIELD_FACTS[field],flush=True)
                    continue
                resolved[field]=reference
                descriptor,locator=await self._target(reference,field,allow_readonly=True)
                if not descriptor['selected']:
                    if not descriptor.get('enabled') or descriptor['kind'] in {'READ_ONLY_SELECTED_VALUE','STATIC_SELECTED_VALUE'}:
                        raise BaselineStop('READ_ONLY_SELECTION_NOT_VERIFIED',field=field)
                    if descriptor['kind'] in {'OWNED_COMBOBOX','CUSTOM_DROPDOWN'}:
                        if not descriptor['owned']:raise BaselineStop('DROPDOWN_OWNERSHIP_UNESTABLISHED',field=field)
                        self.stage=field.upper()+'_OPEN'
                        if not descriptor.get('expanded'):await locator.click(timeout=3000)
                        descriptor,locator=await self._target(reference,field)
                        for attempt in range(3):
                            if descriptor['options'] or attempt==2:break
                            await asyncio.sleep(.2)
                            descriptor,locator=await self._target(reference,field)
                    options=descriptor['options']
                    if len(options)!=1:raise BaselineStop('EXACT_EXISTING_OPTION_UNESTABLISHED',field=field,matching_options=len(options))
                    self.stage=field.upper()+'_SELECT';await self._target(reference,field)
                    expected_backing=options[0].get('backing_value')
                    if descriptor['kind']=='NATIVE_SELECT':await locator.select_option([options[0]['value']],timeout=3000)
                    elif descriptor['kind'] in {'OWNED_COMBOBOX','CUSTOM_DROPDOWN'}:
                        option=options[0]
                        if not option['visible'] or not option['path'] or option.get('enabled') is False or option.get('existing') is False:
                            raise BaselineStop('EXISTING_OPTION_NOT_VISIBLE',field=field)
                        identity=await self._identity(option)
                        choice=self.browser.page.locator(option['path'][-1])
                        if await choice.count(timeout=2000)!=1:raise BaselineStop('OPTION_LOCATOR_NOT_UNIQUE',field=field)
                        await self._target(reference,field)
                        if await self._identity(option)!=identity:raise BaselineStop('CURRENT_OPTION_CHANGED',field=field)
                        await choice.click(timeout=3000)
                    else:raise BaselineStop('DROPDOWN_TYPE_UNSUPPORTED',field=field)
                self.stage=field.upper()+'_READBACK'
                after,_=await self._target(reference,field,allow_readonly=True)
                if not after['selected']:raise BaselineStop('FIELD_EXACT_READBACK_FAILED',field=field)
                if not descriptor['selected'] and expected_backing is not None and after.get('selected_value')!=expected_backing:
                    raise BaselineStop('EXISTING_OPTION_IDENTITY_NOT_VERIFIED',field=field)
                self.verified.add(field);print(FIELD_FACTS[field],flush=True)
            return await self.verify_all(resolved)
        except Exception:self.stopped=True;raise

    async def verify_all(self,mapping):
        if self.verified!=set(VALUES):raise BaselineStop('FOUR_FIELDS_NOT_YET_VERIFIED')
        for field,ref in {'campaign_name':self.campaign_reference,**mapping}.items():
            if ref=='INTERACTIVE':
                await self.interactive.readback(field);continue
            descriptor,locator=await self._target(ref,field,allow_readonly=field!='campaign_name')
            if field=='campaign_name':
                if await locator.input_value(timeout=2000)!=VALUES[field]:raise BaselineStop('CAMPAIGN_EXACT_READBACK_FAILED')
                current,_=await self._target(ref,field)
                if not current['selected']:raise BaselineStop('CAMPAIGN_EXACT_READBACK_FAILED')
            elif not descriptor['selected']:raise BaselineStop('FIELD_EXACT_READBACK_FAILED',field=field)
        return {'ok':True,'status':'FOUR_FIELDS_VERIFIED_UNSAVED','verified_fields':sorted(self.verified),'explicit_commits':0}
