"""Bounded Deep Agents loop over the existing browser-first executor."""
import base64
import hashlib
import json
import struct
import zlib
from pathlib import Path
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field
from langchain_core.tools import StructuredTool
from modules.whatsapp.browser_baseline import BaselineStop,field_reference
from modules.whatsapp.interactive_widgets import InteractiveWidgets
from modules.whatsapp.contract import VALUES,CAPABILITIES,capability_schema,validate_capability,CapabilityError

FieldName=Literal[tuple(VALUES)]
_UNSET=object()
FieldArgs=capability_schema('inspect_field')
SelectionArgs=capability_schema('select_existing_option')
EmptyArgs=capability_schema('observe_page')
class LoopReport(BaseModel):
    status:Literal['COMPLETE','BLOCKED']
class Correction(BaseModel):
    model_config=ConfigDict(extra='forbid')
    owner:Literal['model','tool','controller','authorization','browser','external_site','unknown']
    observations:list[str]=Field(max_length=8)
    hypothesis:str=Field(max_length=500)
    evidence_indices:list[int]=Field(min_length=1,max_length=8)
    next_action:Literal['INSPECT_BEFORE_SELECT','OPEN_BEFORE_INSPECT','VERIFY_BEFORE_ACTION','CODE_FIX_REQUIRED','STOP']
    rationale:str=Field(max_length=500)
    risk:Literal['read_only','scoped_edit','unsafe']
    confidence:float=Field(ge=0,le=1)
    code_fix_required:bool
    safe_next_iteration:bool
    expected_evidence:Literal['OWNED_OPTIONS','EXACT_SELECTED_STATE','FOUR_FIELD_READBACK']

GUIDANCE={
 'BASELINE':'Observe first. Inspect pending fields before acting. Fill Campaign first, then WABA, Business number, tags. Verify each.',
 'INSPECT_BEFORE_SELECT':'Inspect the pending field and rendered options before selection. Never act on an unowned option.',
 'OPEN_BEFORE_INSPECT':'Inspect the pending field, open its verified dropdown once, inspect rendered options, then select only a unique existing approved option.',
 'VERIFY_BEFORE_ACTION':'Verify the current selection first. Preserve matching fields. Inspect and open only pending controls.'}
RECOVERABLE={'INTERACTIVE_EXISTING_OPTION_UNESTABLISHED','FIELD_CONTROL_UNRESOLVED','OPTIONS_NOT_RENDERED_OR_UNOWNED','PREREQUISITE_NOT_VERIFIED','FIELD_NOT_VERIFIED'}

async def redacted_capture(baseline,directory,index,*,protect=None):
    """Capture transiently; fully redact every pixel before writing any PNG."""
    try:
        document=await (protect or baseline.protect)()
        response=await baseline.browser.guard.send('Page.captureScreenshot',{'format':'png'},document[1])
        raw=base64.b64decode(response['data']);width,height=struct.unpack('>II',raw[16:24]);del raw,response
        if await (protect or baseline.protect)()!=document:raise ValueError()
        if not 0<width*height<=8000000:raise ValueError()
        def chunk(name,data):return struct.pack('>I',len(data))+name+data+struct.pack('>I',zlib.crc32(name+data)&0xffffffff)
        image=b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('>IIBBBBB',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress((b'\0'+b'\xe0\xe0\xe0'*width)*height))+chunk(b'IEND',b'')
        path=directory/f'iteration-{index}-redacted.png';path.write_bytes(image)
        return {'status':'FULL_FRAME_REDACTED','file':path.name,'remote_image_supplied':False}
    except Exception:return {'status':'SCREENSHOT_SKIPPED_PRIVACY_OR_TRANSPORT','remote_image_supplied':False}

class BrowserTools:
    def __init__(self,baseline):
        self.baseline=baseline;self.widgets=InteractiveWidgets(baseline);self.events=[];self.terminal=None;self.current=None;self.blocked=False;self.directory=None;self.session=(id(baseline.browser.page),id(baseline.browser.process),baseline.browser.login_submissions)
    async def readiness(self):
        from time import monotonic
        start=monotonic();before=await self.baseline.protect()
        state=await self.baseline.browser.page.evaluate("""new Promise(resolve=>{
          const done=()=>document.readyState!=='loading'&&!!document.querySelector('input,select,[role=combobox]');
          if(done()){resolve({state:document.readyState,controls_present:true});return;}
          const observer=new MutationObserver(()=>{if(done()){observer.disconnect();clearTimeout(timer);resolve({state:document.readyState,controls_present:true});}});
          observer.observe(document,{subtree:true,childList:true});
          const timer=setTimeout(()=>{observer.disconnect();resolve({state:document.readyState,controls_present:done()});},1500);
        })""")
        after=await self.baseline.protect()
        if after!=before:raise BaselineStop('CURRENT_DOCUMENT_CHANGED')
        return {'route':'AUTHORIZED_SETUP','same_protected_document':True,'document_state':state['state'],
            'controls_present':state['controls_present'],'elapsed_ms':round((monotonic()-start)*1000)}

    async def verify(self,field):
        validate_capability('verify_field',field=field)
        if field=='campaign_name':
            data=await self.baseline.discover()
            if not any(c.get('field_hint')==field for c in data['controls']):
                self.baseline.verified.discard(field);return False
            ref=field_reference(data,field)
            d,loc=await self.baseline._target(ref,field)
            ok=d['selected'] and await loc.input_value(timeout=2000)==VALUES[field]
            if ok:self.baseline.campaign_reference=ref
        else:ok=(await self.widgets.inspect(field))['selected']
        if ok:self.baseline.verified.add(field)
        else:self.baseline.verified.discard(field)
        return bool(ok)
    async def invoke(self,name,field=_UNSET,initialize_dependency=_UNSET,**extra):
        if self.terminal:return {'ok':False,'error':self.terminal}
        if self.blocked:return {'ok':False,'error':'ATTEMPT_ENDED'}
        arguments=dict(extra)
        if field is not _UNSET:arguments['field']=field
        if initialize_dependency is not _UNSET:arguments['initialize_dependency']=initialize_dependency
        try:
            if not isinstance(name,str) or name not in CAPABILITIES or not CAPABILITIES[name].exposed:raise CapabilityError('TOOL_FORBIDDEN')
            validate_capability(name,**arguments)
        except CapabilityError as error:
            self.events.append({'index':len(self.events),'tool':name if isinstance(name,str) and name in CAPABILITIES else 'UNSUPPORTED_TOOL','arguments_class':'INVALID','ok':False,'error':error.code})
            self.blocked=True
            return {'ok':False,'error':error.code,'retry_safe':True}
        field=arguments.get('field');initialize_dependency=arguments.get('initialize_dependency',False)
        self.current=field
        event={'index':len(self.events),'tool':name,'arguments_class':field or 'NO_ARGUMENTS','field':field}
        self.events.append(event)
        try:
            await self.baseline.protect()
            if self.session!=(id(self.baseline.browser.page),id(self.baseline.browser.process),self.baseline.browser.login_submissions):raise BaselineStop('SESSION_CHANGED')
            if name=='observe_page':
                data=await self.baseline.discover();result={'setup_active':data['setup_active'],'verified':sorted(self.baseline.verified)}
            elif name=='capture_failure':result=await redacted_capture(self.baseline,self.directory,'tool-'+str(len(self.events)))
            elif name=='verify_field':
                result={'exact_verified':await self.verify(field)}
                if not result['exact_verified']:raise BaselineStop('FIELD_NOT_VERIFIED')
            elif name in {'inspect_field','inspect_options'}:
                if field=='campaign_name':result={'controls':[c for c in (await self.baseline.discover())['controls'] if c.get('field_hint')=='campaign_name']}
                else:result=(await self.widgets.inspect(field))['public']
            elif name=='scroll_to_field':
                if field=='campaign_name':
                    from modules.whatsapp.browser_baseline_dom import lookup
                    discovery=await self.baseline.discover();ref=field_reference(discovery,field)
                    descriptor,_=await self.baseline._target(ref,field)
                    scrolled=await self.baseline.browser.page.evaluate('(()=>{const e='+lookup(descriptor['path'])+';e?.scrollIntoView({block:"center"});return !!e})()')
                    await self.baseline._target(ref,field)
                    if scrolled is not True:raise BaselineStop('OBSERVED_CONTROL_UNAVAILABLE')
                    result={'field':field,'observed_control_scrolled':True}
                else:result=(await self.widgets.scroll(field))['public']
            elif name=='open_dropdown':result=(await self.widgets.open(field,authorized=True))['public']
            elif name=='fill_approved_field':
                if field!='campaign_name':raise BaselineStop('FIELD_ACTION_FORBIDDEN')
                if not await self.verify(field):
                    data=await self.baseline.discover();await self.baseline.fill_campaign(field_reference(data,field),authorized=True)
                result={'exact_verified':await self.verify(field)}
                if not result['exact_verified']:raise BaselineStop('FIELD_EXACT_READBACK_FAILED')
            elif name=='select_existing_option':
                if field=='campaign_name':raise BaselineStop('FIELD_ACTION_FORBIDDEN')
                for prior in list(VALUES)[:list(VALUES).index(field)]:
                    if not await self.verify(prior):raise BaselineStop('PREREQUISITE_NOT_VERIFIED')
                await self.widgets.complete(field,authorized=True,initialize_dependency=initialize_dependency);result={'exact_verified':await self.verify(field)}
                if not result['exact_verified']:raise BaselineStop('FIELD_EXACT_READBACK_FAILED')
            else:raise BaselineStop('CAPABILITY_DISPATCH_MISSING')
            event.update(ok=True,result=result);return {'ok':True,**result}
        except Exception as error:
            code=error.code if isinstance(error,BaselineStop) else 'BROWSER_OR_BUDGET_FAILURE'
            event.update(ok=False,error=code)
            self.blocked=code!='FIELD_NOT_VERIFIED'
            if code not in RECOVERABLE or field in self.widgets.attempted:self.terminal=code
            return {'ok':False,'error':code,'retry_safe':self.terminal is None,**({'exact_verified':False,'terminal':False} if code=='FIELD_NOT_VERIFIED' else {})}
    def registered(self):
        raise RuntimeError('RETIRED_WHATSAPP_TOOLS: use V2BrowserTools')
        tools=[]
        for name,capability in CAPABILITIES.items():
            if not capability.exposed:continue
            def bind(action):
                async def call(**arguments):return await self.invoke(action,**arguments)
                return call
            tools.append(StructuredTool.from_function(coroutine=bind(name),name=name,
                description=name+': '+capability.verification+'. Fixed approved values only.',args_schema=capability_schema(name)))
        return tools

async def run_loop(baseline,doer_model,teacher_model,budget,directory,**options):
    raise RuntimeError('RETIRED_WHATSAPP_SCHEDULER: use modules.whatsapp.workflow.run_v2')
    from learning.browser_runtime import run_task
    from learning.skill_storage.registry import module_baseline
    return await run_task(baseline,doer_model,teacher_model,budget,directory,
        task=(BrowserTools,VALUES,GUIDANCE,LoopReport,Correction,redacted_capture,BaselineStop),
        baseline_skill=module_baseline('whatsapp'),**options)
