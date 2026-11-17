"""Scoped browser-first widget inspection; private DOM data never enters reports.

No model, arbitrary selector, script or navigation interface is exposed. When a
trigger cannot be proved, only an operator demonstration can open the widget.
Demonstration is evidence, not permission to adopt an unrelated portal.
"""
import asyncio
import json

from modules.whatsapp.browser_baseline import BaselineStop
from modules.whatsapp.browser_baseline_dom import lookup
from modules.whatsapp.contract import VALUES,CAPABILITIES,validate_capability,CapabilityError


INSPECT = r'''(settings=>{
 const norm=s=>(s||'').replace(/\s+/g,' ').trim(), visible=e=>!!e?.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 const parent=e=>e?.parentElement||e?.getRootNode()?.host;
 const within=(e,g)=>{for(let n=e,i=0;n&&i<128;n=parent(n),i++)if(n===g)return true;return false};
 const path=e=>{const root=e.getRootNode();let parts=[],n=e;
   if(e.id){const s='#'+CSS.escape(e.id);if(root.querySelectorAll(s).length===1){const outer=root.host?path(root.host):[];return outer?[...outer,s]:null;}}
   for(let i=0;n&&i<128;i++,n=n.parentElement){
     const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
     parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
     const s=parts.join(' > ');if(s.length>2048)return null;
     if(!n.parentElement&&root.querySelectorAll(s).length===1){const outer=root.host?path(root.host):[];return outer?[...outer,s]:null;}
   }return null;};
 const all=[],roots=[document];let complete=true;
 for(let r=0;r<roots.length;r++){const walk=document.createTreeWalker(roots[r],NodeFilter.SHOW_ELEMENT);let e;
   while(e=walk.nextNode()){if(all.length===12000){complete=false;break;}all.push(e);
     if(e.shadowRoot){if(roots.length===65)complete=false;else roots.push(e.shadowRoot);}}
   if(!complete)break;}
 const field=e=>{const copy=e.cloneNode(true);copy.querySelectorAll('input,select,textarea,[role=listbox]').forEach(n=>n.remove());
   const s=norm(copy.textContent).replace(/[\s*:]+$/g,'').toLowerCase();
   return FIELD_ALIASES_JSON[s];};
 const raw=all.filter(e=>visible(e)&&!e.matches('input,select,textarea,option,[role=option]')&&field(e));
 const labels=raw.filter(e=>!raw.some(n=>n!==e&&field(n)===field(e)&&e.contains(n)));
 const groups=[];
 for(const l of labels.filter(e=>field(e)===settings.field)){
   for(let g=parent(l),i=0;g&&i<5;g=parent(g),i++){
     if(g.matches('body,html,form,[role=form]')||labels.some(n=>within(n,g)&&field(n)!==settings.field)||
       all.some(n=>visible(n)&&n.matches('label,legend')&&within(n,g)&&field(n)!==settings.field))break;
     if(all.some(n=>n!==l&&within(n,g)&&!within(n,l)&&
       (n.tagName.includes('-')||n.matches('input,select,button,[role=combobox],[aria-haspopup]')))){
       if(!groups.includes(g))groups.push(g);break;}
   }
 }
 // Repeated labels within one actual component do not create separate owners.
 const unique=groups.filter(g=>!groups.some(n=>n!==g&&within(g,n)));
 const group=unique.length===1?unique[0]:null, nodes=group?all.filter(n=>within(n,group)):[];
 const safe=e=>!!e&&!e.closest('a,[href],[formaction],[inert],[role=tab]')&&!e.matches('input[type=submit],input[type=image]')&&
   !(e.tagName==='BUTTON'&&e.type!=='button')&&!e.isContentEditable&&!e.closest('[contenteditable]:not([contenteditable=false])')&&
   !/\b(save|next(?:\s+step)?|finish\s+later|schedule|publish|activate|send|create)\b/i.test(norm(e.textContent)+' '+(e.getAttribute('aria-label')||''));
 const enabled=e=>!e.disabled&&!e.matches(':disabled')&&e.getAttribute('aria-disabled')!=='true'&&!e.closest('[inert],[aria-disabled=true]');
 const triggers=nodes.filter(e=>visible(e)&&safe(e)&&enabled(e)&&!e.closest('[role=listbox]')&&
   (e.tagName==='SELECT'||e.getAttribute('role')==='combobox'||e.getAttribute('aria-haspopup')==='listbox'));
 const trigger=triggers.length===1?triggers[0]:null;
 const ids=trigger?((trigger.getAttribute('aria-controls')||'')+' '+(trigger.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean):[];
 const linked=ids.map(id=>all.filter(n=>n.id===id)).filter(found=>found.length===1).map(found=>found[0]);
 const contested=ids.some(id=>all.some(n=>n!==trigger&&((n.getAttribute('aria-controls')||'')+' '+(n.getAttribute('aria-owns')||'')).split(/\s+/).includes(id)));
 const lists=contested?[]:all.filter(e=>e.matches('[role=listbox]')&&(linked.includes(e)||group&&within(e,group)));
 const visibleLists=lists.filter(visible), desired=settings.value;
 const opts=trigger?.tagName==='SELECT'?[...trigger.options]:visibleLists.flatMap(l=>all.filter(n=>within(n,l)&&n.matches('[role=option]')));
 const idMatch=s=>{const t=norm(s),tokens=t.match(/[0-9]+/g)||[];
   return new RegExp('(^|[^A-Za-z0-9_])'+desired+'($|[^A-Za-z0-9_])').test(t)&&
     tokens.filter(n=>n===desired).length===1&&!tokens.some(n=>n.length>=desired.length&&n!==desired);};
 const exact=o=>settings.field==='waba'?(!(norm(o.value)+' '+norm(o.getAttribute('data-value'))+' '+norm(o.textContent)).match(/[0-9]+/g)?.some(n=>n.length>=desired.length&&n!==desired)&&(idMatch(o.value)||idMatch(o.getAttribute('data-value'))||idMatch(o.textContent))):norm(o.textContent)===desired;
 const existing=o=>o.tagName==='OPTION'||o.getAttribute('data-existing')==='true'||
   o.getAttribute('role')==='option'&&!o.hasAttribute('data-create')&&!o.hasAttribute('data-select2-tag')&&(settings.field==='waba'?exact(o):norm(o.textContent)===desired);
 const approved=opts.filter(o=>exact(o)&&existing(o)&&safe(o)&&enabled(o)&&
   !o.closest('optgroup[disabled]')&&(o.tagName==='OPTION'||visible(o)));
 const selected=trigger?.tagName==='SELECT'?[...trigger.selectedOptions]:lists.flatMap(l=>all.filter(n=>within(n,l)&&n.matches('[role=option][aria-selected=true]')));
 const readonly=nodes.filter(n=>n.tagName==='INPUT'&&(n.readOnly||n.disabled)&&visible(n));
 const current=trigger?(trigger.tagName==='INPUT'||trigger.tagName==='SELECT'?trigger.value:
   trigger.getAttribute('aria-valuetext')||trigger.getAttribute('data-selected-value')||norm(trigger.textContent)):null;
 // Tags require actual selected-option evidence, never search text. A native or
 // uniquely associated readonly WABA/number can independently prove its value.
 const readonlyProof=settings.field!=='tags'&&readonly.length===1&&readonly[0].value===desired&&
   [...(readonly[0].labels||[])].some(l=>labels.includes(l));
 const matchingScope=trigger?.tagName==='SELECT'?[...trigger.options]:lists.flatMap(l=>all.filter(n=>within(n,l)&&n.matches('[role=option]')));
 const selectedProof=matchingScope.filter(exact).length===1&&selected.length===1&&exact(selected[0])&&existing(selected[0])&&
   (trigger?.tagName==='SELECT'||current===desired||current===norm(selected[0].textContent));
 const selected_ok=!!group&&(readonlyProof||selectedProof);
 const reason=!complete?'SCOPED_TRAVERSAL_LIMIT':!group?'FIELD_GROUP_NOT_UNIQUE':
   !trigger&&!selected_ok?'CUSTOM_TRIGGER_NOT_ESTABLISHED':contested?'POPUP_OWNER_AMBIGUOUS':
   !selected_ok&&!opts.length?'OPTIONS_NOT_RENDERED_OR_UNOWNED':!selected_ok&&approved.length!==1?'EXACT_EXISTING_OPTION_NOT_UNIQUE':null;
 const role=e=>['combobox','button','listbox','option','textbox'].includes(e.getAttribute('role'))?e.getAttribute('role'):'OTHER_OR_NONE';
 return {complete,group:pathIf(group),trigger:pathIf(trigger),options:approved.map(o=>({path:path(o),value:o.tagName==='OPTION'?o.value:null})),
   native:trigger?.tagName==='SELECT',selected:selected_ok,readonly:readonlyProof,expanded:visibleLists.length>0,
   public:{field:settings.field,label_count:labels.filter(e=>field(e)===settings.field).length,group_count:unique.length,
     accessible_name_class:trigger?fieldName(trigger):'UNESTABLISHED',trigger_count:triggers.length,disabled_target_count:nodes.filter(n=>visible(n)&&!enabled(n)&&n.matches('select,[role=combobox],[aria-haspopup=listbox]')).length,
     trigger_kind:trigger?.tagName==='SELECT'?'NATIVE_SELECT':trigger?'SEMANTIC_DROPDOWN':'UNESTABLISHED',
     ownership:contested?'CONTESTED':linked.length?'EXPLICIT_ARIA':lists.length?'SCOPED_DESCENDANT':'UNESTABLISHED',
     custom_hosts:nodes.filter(n=>n.tagName.includes('-')).length,open_roots:nodes.filter(n=>n.shadowRoot).length,
     roles:[...new Set(nodes.filter(visible).map(role))],option_count:opts.length,approved_option_count:approved.length,
     accessible_relationships:{label_for:nodes.filter(n=>n.matches('label[for]')).length,
       labelledby:nodes.filter(n=>n.hasAttribute('aria-labelledby')).length,controls:ids.length},
     unowned_visible_listboxes:all.filter(n=>n.matches('[role=listbox]')&&visible(n)&&!lists.includes(n)).length,
     option_classifications:{approved_existing:approved.length,create_or_new:opts.filter(n=>n.hasAttribute('data-create')||n.hasAttribute('data-select2-tag')||/\bcreate\b/i.test(norm(n.textContent))).length,
       other:opts.filter(n=>!exact(n)).length},
     selected_option_count:selected.length,exact_selected:selected_ok,read_only_value_verified:readonlyProof,
     negative_reason:reason,traversal_complete:complete}}
 function pathIf(n){return n?path(n):null}
 function fieldName(n){const name=norm(n.getAttribute('aria-label')||'').replace(/[\s*:]+$/g,'').toLowerCase();
   return FIELD_ALIASES_JSON[name]===settings.field?settings.field:'REDACTED_OR_UNESTABLISHED'}
})(SETTINGS)'''


class InteractiveWidgets:
    def __init__(self,baseline,prompt=None):
        self.baseline=baseline;self.browser=baseline.browser;self.prompt=prompt
        self.evidence=[];self.identities={};self.attempted=set();self.actions=[]

    async def inspect(self,field):
        try:validate_capability('inspect_field',field=field)
        except CapabilityError as error:raise BaselineStop(error.code) from None
        if field not in CAPABILITIES['select_existing_option'].fields:raise BaselineStop('INTERACTIVE_FIELD_OUT_OF_SCOPE')
        data=await self.baseline._snapshot()
        before=await self.baseline.protect()
        result=await self.browser.page.evaluate(INSPECT.replace('SETTINGS',json.dumps({'field':field,'value':VALUES[field]})))
        if await self.baseline.protect()!=before:raise BaselineStop('CURRENT_DOCUMENT_CHANGED')
        if not result.get('complete'):raise BaselineStop('SCOPED_TRAVERSAL_LIMIT',field=field)
        self.evidence.append(result['public']);print('WIDGET_OBSERVED '+json.dumps(result['public']),flush=True)
        return result

    async def scroll(self,field):
        try:validate_capability('scroll_to_field',field=field)
        except CapabilityError as error:raise BaselineStop(error.code) from None
        observed=await self.inspect(field)
        if not observed['group']:raise BaselineStop('FIELD_GROUP_NOT_UNIQUE',field=field)
        identity=await self.baseline._identity({'path':observed['group']})
        await self.baseline.protect()
        # Fixed read-only operation on a privately observed, pinned field group.
        if await self.baseline._identity({'path':observed['group']})!=identity:raise BaselineStop('CURRENT_CONTROL_CHANGED')
        await self.browser.page.evaluate('(()=>{const e='+lookup(observed['group'])+';e?.scrollIntoView({block:"center"});return !!e})()')
        return await self.inspect(field)

    async def _locator(self,path):
        identity=await self.baseline._identity({'path':path})
        locator=self.browser.page.locator(path[-1])
        if await locator.count(timeout=2000)!=1 or not await locator.is_visible(timeout=2000):
            raise BaselineStop('INTERACTIVE_TARGET_NOT_UNIQUE_VISIBLE')
        if await self.baseline._identity({'path':path})!=identity:raise BaselineStop('CURRENT_CONTROL_CHANGED')
        return locator,identity

    async def open(self,field,*,authorized=False,_open_selected=False):
        try:validate_capability('open_dropdown',field=field)
        except CapabilityError as error:raise BaselineStop(error.code) from None
        if authorized is not True:raise BaselineStop('OPERATOR_REMAINING_FIELDS_AUTHORIZATION_REQUIRED')
        if not (await self.baseline._snapshot()).get('setup_active'):raise BaselineStop('DIRECT_SETUP_EVIDENCE_INCOMPLETE')
        state=await self.scroll(field)
        if state['selected'] and not _open_selected or state['native'] or state['expanded']:return state
        if not state['trigger']:
            return await self.demonstrate(field)
        if any(a['field']==field and a['action']=='DROPDOWN_OPEN_ATTEMPTED' for a in self.actions):raise BaselineStop('DROPDOWN_STATE_UNCERTAIN',field=field)
        await self.baseline._commit_block()
        locator,identity=await self._locator(state['trigger'])
        fresh=await self.inspect(field)
        if fresh['trigger']!=state['trigger'] or await self.baseline._identity({'path':fresh['trigger']})!=identity:
            raise BaselineStop('CURRENT_CONTROL_CHANGED')
        self.actions.append({'field':field,'action':'DROPDOWN_OPEN_ATTEMPTED'})
        await locator.click(timeout=3000)  # one non-submit dropdown interaction
        self.actions.append({'field':field,'action':'DROPDOWN_OPENED'})
        for attempt in range(4):
            state=await self.inspect(field)
            if state['options'] or state['expanded']:break
            if attempt<3:await asyncio.sleep(.2)
        if not state.get('expanded') and not state.get('selected') and not state.get('native'):
            raise BaselineStop('DROPDOWN_OPEN_NOT_VERIFIED',field=field)
        return state

    async def demonstrate(self,field):
        before=await self.scroll(field)
        if not self.prompt:raise BaselineStop('SUPERVISED_WIDGET_DEMONSTRATION_REQUIRED',field=field)
        await self.baseline._commit_block()
        document=await self.baseline.protect()
        group_identity=await self.baseline._identity({'path':before['group']})
        if not await self.prompt(field):raise BaselineStop('WIDGET_DEMONSTRATION_DECLINED',field=field)
        after=await self.inspect(field)
        if await self.baseline.protect()!=document or after['group']!=before['group'] or await self.baseline._identity({'path':after['group']})!=group_identity:
            raise BaselineStop('CURRENT_CONTROL_CHANGED',field=field)
        delta={'field':field,'operation':'OPERATOR_DROPDOWN_DEMONSTRATION',
            'visible_option_count_before':before['public']['option_count'],
            'visible_option_count_after':after['public']['option_count'],
            'ownership_after':after['public']['ownership'],'selection_changed':before['selected']!=after['selected']}
        self.evidence.append(delta);print('WIDGET_DEMONSTRATION '+json.dumps(delta),flush=True)
        if delta['selection_changed']:raise BaselineStop('DEMONSTRATION_SELECTION_CHANGED_STOP',field=field)
        return after

    async def complete(self,field,*,authorized=False,initialize_dependency=False):
        try:validate_capability('select_existing_option',field=field,initialize_dependency=initialize_dependency)
        except CapabilityError as error:raise BaselineStop(error.code) from None
        if authorized is not True:raise BaselineStop('OPERATOR_REMAINING_FIELDS_AUTHORIZATION_REQUIRED')
        if field in self.attempted:raise BaselineStop('INTERACTIVE_WRITE_RETRY_FORBIDDEN',field=field)
        state=await self.inspect(field) if initialize_dependency else None
        if initialize_dependency:
            dependent=await self.inspect('business_number')
            if field!='waba' or not state['selected'] or dependent['selected'] or dependent['public']['group_count']!=1 or dependent['public']['disabled_target_count']!=1:
                raise BaselineStop('DEPENDENCY_INITIALIZATION_NOT_JUSTIFIED')
        # Only the validated dependency path may open an already selected widget.
        state=await self.open(field,authorized=True,_open_selected=True) if initialize_dependency else await self.open(field,authorized=True)
        if state['selected'] and not initialize_dependency:
            self.identities[field]=await self.baseline._identity({'path':state['group']});return
        if len(state['options'])!=1:raise BaselineStop('INTERACTIVE_EXISTING_OPTION_UNESTABLISHED',field=field)
        if not state['trigger']:raise BaselineStop('CUSTOM_TRIGGER_NOT_ESTABLISHED',field=field)
        await self.baseline._commit_block()
        option=state['options'][0];choice,identity=await self._locator(option['path']) if not state['native'] else (None,None)
        trigger,trigger_identity=await self._locator(state['trigger'])
        current=await self.inspect(field)
        if current['trigger']!=state['trigger'] or current['options']!=state['options'] or not current['group']:
            raise BaselineStop('INTERACTIVE_OPTION_CHANGED',field=field)
        if await self.baseline._identity({'path':current['trigger']})!=trigger_identity:
            raise BaselineStop('CURRENT_CONTROL_CHANGED')
        self.attempted.add(field)
        self.actions.append({'field':field,'action':'SELECTION_ATTEMPTED'})
        if state['native']:await trigger.select_option([option['value']],timeout=3000)
        else:
            if await self.baseline._identity({'path':option['path']})!=identity:raise BaselineStop('CURRENT_OPTION_CHANGED')
            await choice.click(timeout=3000)
        self.actions.append({'field':field,'action':'SELECTION_COMPLETED'})
        # No action retry, even when readback is inconclusive.
        await self.readback(field)
        self.identities[field]=await self.baseline._identity({'path':current['group']})

    async def readback(self,field):
        state=await self.inspect(field)
        if not state['selected']:raise BaselineStop('INTERACTIVE_EXACT_SELECTION_NOT_VERIFIED',field=field)
        if field in self.identities and await self.baseline._identity({'path':state['group']})!=self.identities[field]:
            raise BaselineStop('CURRENT_CONTROL_CHANGED',field=field)
        return True

from modules.whatsapp.contract import FIELD_ALIASES
INSPECT=INSPECT.replace("FIELD_ALIASES_JSON",json.dumps(dict(FIELD_ALIASES)))
