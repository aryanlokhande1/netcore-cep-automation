"""Read-only Setup form observation. Private selectors never belong in model output.

This identifies visible light-DOM controls; it does not certify authentication or
shadow safety, authorize actions, install listeners or change the page.
"""
import json

from modules.whatsapp.contract import LABELS,VALUES,FIELD_ALIASES


OBSERVE_SETUP = r'''(settings => {
 const norm=s=>(s||'').replace(/\s+/g,' ').trim();
 const labelText=s=>norm(s).replace(/[\s*:]+$/g,'').toLowerCase();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const disabled=e=>!!e.disabled||!!e.readOnly||e.matches(':disabled')||
   e.getAttribute('aria-disabled')==='true'||!!e.closest('[inert],[aria-disabled=true]');
 const geometry=e=>{
   if(!e||typeof e.getBoundingClientRect!=='function'||typeof document.elementFromPoint!=='function')
     return {unobscured:null,in_viewport:null,geometry_status:'NOT_CHECKED'};
   try{
     const rect=e.getBoundingClientRect(),x=rect.left+rect.width/2,y=rect.top+rect.height/2;
     if(!(rect.width>0&&rect.height>0))return {unobscured:false,in_viewport:false,geometry_status:'ZERO_SIZE'};
     if(!(x>=0&&y>=0&&x<window.innerWidth&&y<window.innerHeight))
       return {unobscured:false,in_viewport:false,geometry_status:'OFFSCREEN'};
     const hit=document.elementFromPoint(x,y),clear=hit===e||!!hit&&e.contains(hit);
     return {unobscured:clear,in_viewport:true,geometry_status:clear?'UNOBSCURED':'COVERED'};
   }catch{return {unobscured:null,in_viewport:null,geometry_status:'NOT_CHECKED'};}
 };
 const all=[];let traversal_limit_reached=false,open_shadow_hosts_observed=0;
 // A cursor walk, rather than an unbounded query array, limits observation cost.
 const stack=[{next:document.documentElement||document.body,depth:0}];
 while(stack.length){
   const cursor=stack[stack.length-1],e=cursor.next;
   if(!e){stack.pop();continue;}
   cursor.next=e.nextElementSibling;
   if(all.length>=12000){traversal_limit_reached=true;break;}
   all.push(e);if(e.shadowRoot)open_shadow_hosts_observed++;
   if(e.firstElementChild){
     if(cursor.depth>=128)traversal_limit_reached=true;
     else stack.push({next:e.firstElementChild,depth:cursor.depth+1});
   }
 }
 if(traversal_limit_reached)return {fields:Object.fromEntries(Object.keys(settings.labels).map(key=>[key,{discovered:false}])),
   form_selector:null,form_recognized:false,same_form:false,setup_active:false,page_identity:false,
   whatsapp_text_observed:false,observation_complete:false,observation_scope:'DOCUMENT_LIGHT_DOM',closed_shadow_scope_certified:false,
   observation_counts:{nodes_inspected:all.length,visible_controls:0,open_shadow_hosts_observed,traversal_limit_reached:true}};
 const unique=s=>{
   if(!s||s.length>1024)return null;
   try{const found=document.querySelectorAll(s);return found.length===1?found[0]:null;}catch{return null;}
 };
 const selector=e=>{
   if(!e)return null;
   if(e.id){const s='#'+CSS.escape(e.id);if(unique(s)===e)return s;}
   let n=e,parts=[];
   for(let depth=0;n&&depth<=128;depth++,n=n.parentElement){
     const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
     parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
     const s=parts.join(' > ');if(s.length>1024)return null;
     if(unique(s)===e)return s;
   }
   return null;
 };
 const byId=id=>id?unique('#'+CSS.escape(id)):null;
 const label=e=>{
   const aria=e.getAttribute('aria-label');if(aria)return norm(aria);
   const labels=[...(e.labels||[])].filter(l=>visible(l));
   if(labels.length)return norm(labels.map(l=>l.textContent).join(' '));
   const ids=(e.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
   const referenced=ids.map(byId);
   return referenced.length&&referenced.every(l=>l&&visible(l))?norm(referenced.map(l=>l.textContent).join(' ')):'';
 };
 const safeControl=e=>!!e&&e.tagName!=='TEXTAREA'&&
   !['submit','password','hidden','checkbox','radio','file'].includes(e.type)&&
   (e.type!=='button'||e.tagName==='BUTTON'&&e.getAttribute('role')==='combobox')&&
   (e.tagName==='SELECT'||e.tagName==='INPUT'||e.getAttribute('role')==='combobox');
 const actionSafe=e=>{
   if(!e||e.getAttribute('href')!==null||e.getAttribute('formaction')!==null||e.isContentEditable)return false;
   const editable=e.closest('[contenteditable]');
   if(editable&&editable.getAttribute('contenteditable')!=='false')return false;
   const basic=e.tagName==='SELECT'||e.tagName==='INPUT'&&['text','search',''].includes(e.type)||
     e.getAttribute('role')==='combobox'&&(['DIV','SPAN'].includes(e.tagName)||e.tagName==='BUTTON'&&e.type==='button');
   if(!basic)return false;
   const operative=e.tagName==='BUTTON'||e.getAttribute('role')==='combobox';
   if(operative){
     const text=norm(e.innerText||e.textContent||e.value||'');
     if(/\b(next(?:\s+step)?|finish\s+later|save|schedule|publish|activate|send)\b/i.test(text)||/^(audience|content)$/i.test(text))return false;
   }
   return true;
 };
 const controls=all.filter(e=>e.matches('input,select,[role=combobox]')&&safeControl(e)&&visible(e));
 const labels=all.filter(e=>e.tagName==='LABEL'&&visible(e));
 const owner=e=>e.form||e.closest('form,[role=form]');
 const fields={},found=new Map();
 for(const [key,wanted] of Object.entries(settings.labels)){
   let candidates=controls.filter(e=>(FIELD_ALIASES_JSON)[labelText(label(e))]===key);
   if(!candidates.length){
     for(const l of labels){
       if((FIELD_ALIASES_JSON)[labelText(l.textContent)]!==key)continue;
       if(safeControl(l.control)&&visible(l.control))candidates.push(l.control);
       else{
         let parent=l.parentElement;
         for(let depth=0;parent&&depth<2;depth++,parent=parent.parentElement){
           const matches=controls.filter(e=>parent.contains(e));
           if(matches.length===1){candidates.push(matches[0]);break;}
         }
       }
     }
   }
   candidates=[...new Set(candidates)];
   if(candidates.length!==1){fields[key]={discovered:false,ambiguous:candidates.length>1};continue;}
   const e=candidates[0],controlSelector=selector(e),form=owner(e),formSelector=selector(form);
   const desired=settings.values[key];
   const action_safe=actionSafe(e);
   const kind=!action_safe?'UNSUPPORTED':e.tagName==='SELECT'?'NATIVE_SELECT':key==='campaign_name'&&e.tagName==='INPUT'&&
     e.getAttribute('role')!=='combobox'&&['text','search',''].includes(e.type)?'TEXT_INPUT':
     key!=='campaign_name'&&e.getAttribute('role')==='combobox'?'OWNED_COMBOBOX':'UNSUPPORTED';
   let options=[],selected=false,value=null,open_selector=null,search_selector=null,option_selector=null;
   let expanded=false,widgetOwned=kind!=='OWNED_COMBOBOX'&&kind!=='UNSUPPORTED',has_other_tags=false,optionElement=null,searchElement=null;
   let existing_exact_option_count=0;
   const optionMatches=o=>{
     if(o.getAttribute('href')!==null||o.getAttribute('formaction')!==null||o.isContentEditable||
       o.closest('[contenteditable]')&&o.closest('[contenteditable]').getAttribute('contenteditable')!=='false')return false;
     if(e.tagName==='SELECT'&&o.tagName!=='OPTION')return false;
     if(e.tagName!=='SELECT'&&!(['DIV','SPAN','LI'].includes(o.tagName)||o.tagName==='BUTTON'&&o.type==='button'))return false;
     if(o.disabled||o.closest('optgroup')?.disabled||o.getAttribute('aria-disabled')==='true'||
       o.getAttribute('data-select2-tag')==='true'||o.getAttribute('data-create')==='true')return false;
     if(e.tagName==='SELECT'&&!o.value)return false;
     if(key==='tags'&&e.tagName!=='SELECT'&&o.getAttribute('data-existing')!=='true')return false;
     if(key==='waba')return o.value===desired||o.getAttribute('data-waba-id')===desired;
     return norm(o.textContent)===desired;
   };
   if(kind==='NATIVE_SELECT'){
     if(e.options.length>2000)traversal_limit_reached=true;
     options=[...e.options].slice(0,2000).filter(optionMatches);
     existing_exact_option_count=options.length;
     const chosen=[...e.selectedOptions];
     selected=chosen.some(optionMatches);
     if(key!=='tags'&&chosen.length!==1)selected=false;
     has_other_tags=key==='tags'&&chosen.some(o=>o.value&&!optionMatches(o));
     if(has_other_tags)selected=false;
     if(options.length===1){optionElement=options[0];value=optionElement.value;option_selector=selector(optionElement);}
   }else if(kind==='TEXT_INPUT')selected=e.value===desired;
   else if(kind==='OWNED_COMBOBOX'){
     const ids=[...new Set(((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean))];
     const roots=ids.map(byId);
     const competing=controls.filter(other=>other!==e&&other.getAttribute('role')==='combobox'&&
       ((other.getAttribute('aria-controls')||'')+' '+(other.getAttribute('aria-owns')||'')).split(/\s+/).some(id=>ids.includes(id)));
     widgetOwned=ids.length>0&&roots.every(Boolean)&&competing.length===0&&
       roots.every(root=>root!==e&&!root.contains(e)&&root.matches('[role=listbox]'));
     if(widgetOwned){
       expanded=e.getAttribute('aria-expanded')==='true'||roots.some(visible);
       const choices=[...new Set(all.filter(o=>o.matches('[role=option]')&&roots.some(root=>root.contains(o))))];
       if(choices.length>2000)traversal_limit_reached=true;
       const bounded=choices.slice(0,2000),existing=bounded.filter(optionMatches);
       existing_exact_option_count=existing.length;
       options=existing.filter(visible);
       const chosen=options.length===1?options[0]:null;
       if(chosen){
         optionElement=chosen;
         option_selector=selector(chosen);value=option_selector;
       }
       const selectedChoices=bounded.filter(o=>o.getAttribute('aria-selected')==='true');
       const controlMatches=key==='waba'?(e.value===desired||e.getAttribute('data-waba-id')===desired):norm(e.value||e.innerText||e.textContent)===desired;
       selected=existing.length===1&&selectedChoices.some(o=>o===existing[0])&&controlMatches;
       if(key!=='tags'&&selectedChoices.length!==1)selected=false;
       has_other_tags=key==='tags'&&selectedChoices.some(o=>!optionMatches(o));
       if(has_other_tags)selected=false;
       open_selector=controlSelector;
       const searches=all.filter(s=>s.tagName==='INPUT'&&['text','search'].includes(s.type)&&actionSafe(s)&&
         s.matches('input[type=search],[role=searchbox]')&&visible(s)&&!disabled(s)&&roots.some(root=>root.contains(s)));
       if(searches.length===1){searchElement=searches[0];search_selector=selector(searchElement);}
     }
   }
   const selector_unique=!!controlSelector&&unique(controlSelector)===e;
   const ownership_verified=!!form&&!!formSelector&&unique(formSelector)===form&&widgetOwned;
   const controlGeometry=geometry(e),optionGeometry=geometry(optionElement),searchGeometry=geometry(searchElement);
   fields[key]={discovered:selector_unique,selector:controlSelector,selector_unique,form_selector:formSelector,
     kind,action_safe,selected,exact_option_count:options.length,existing_exact_option_count,value,option_selector,open_selector,search_selector,expanded,
     visible:visible(e),enabled:!disabled(e),disabled:disabled(e),ownership_verified,
     ...controlGeometry,option_visible:visible(optionElement),option_enabled:!!optionElement&&!disabled(optionElement),
     option_selector_unique:!!option_selector&&unique(option_selector)===optionElement,
     option_unobscured:optionGeometry.unobscured,option_geometry_status:optionGeometry.geometry_status,
     search_visible:visible(searchElement),search_enabled:!!searchElement&&!disabled(searchElement),
     search_selector_unique:!!search_selector&&unique(search_selector)===searchElement,
     search_unobscured:searchGeometry.unobscured,search_geometry_status:searchGeometry.geometry_status,
     multiple:!!e.multiple,has_other_tags,observation_scope:'DOCUMENT_LIGHT_DOM'};
   if(selector_unique)found.set(key,{element:e,form});
 }
 const campaign=found.get('campaign_name'),form=campaign?.form||null,form_selector=selector(form);
 const form_recognized=!!campaign&&!!form&&!!form_selector&&[...found.values()].every(item=>item.form===form);
 const same_form=found.size===4&&form_recognized;
 for(const [key,field] of Object.entries(fields))field.same_form=!!form&&found.get(key)?.form===form;
 const stepNodes=all.filter(e=>visible(e)&&e.matches('h1,h2,h3,h4,[role=heading],[aria-current],[aria-selected],li,button,a'));
 const active=e=>[e,e.parentElement].filter(Boolean).some(n=>['step','page','true'].includes(n.getAttribute('aria-current'))||
   n.getAttribute('aria-selected')==='true'||[...n.classList].some(c=>/(^|[-_])(active|current|selected)([-_]|$)/i.test(c)));
 const setup_active=stepNodes.some(e=>labelText(e.textContent)==='setup'&&(active(e)||e.matches('h1,h2,h3,h4,[role=heading]')))&&
   !stepNodes.some(e=>['audience','content','schedule'].includes(labelText(e.textContent))&&active(e));
 const whatsapp_text_observed=stepNodes.some(e=>e.textContent.length<=1024&&/\bwhatsapp\b/i.test(e.textContent));
 const active_step=setup_active?'SETUP':(['audience','content','schedule'].find(
   name=>stepNodes.some(e=>labelText(e.textContent)===name&&active(e)))||'UNKNOWN').toUpperCase();
 // A scoped form signature is independent of heading wording. The Python
 // destination gate still requires the exact configured route before binding or
 // writes; each field's separate ownership/action checks remain mandatory.
 const distinct_field_controls=new Set([...found.values()].map(item=>item.element)).size;
 const form_signature=form_recognized&&same_form&&distinct_field_controls===4&&fields.campaign_name?.kind==='TEXT_INPUT'&&
   fields.campaign_name?.ownership_verified===true&&fields.campaign_name?.action_safe===true;
 const page_identity=setup_active&&(whatsapp_text_observed||form_signature);
 const identity_basis=!page_identity?'UNESTABLISHED':whatsapp_text_observed?'WHATSAPP_TEXT_AND_SETUP':'SCOPED_PAGE1_FORM_SIGNATURE';
 return {fields,form_selector,form_recognized,same_form,setup_active,page_identity,identity_basis,
   identity_diagnostics:{whatsapp_text_observed,setup_active,form_signature,
     distinct_field_controls,same_form_field_count:[...found.values()].filter(item=>!!form&&item.form===form).length},
   active_step,whatsapp_text_observed,observation_complete:!traversal_limit_reached,
   observation_scope:'DOCUMENT_LIGHT_DOM',closed_shadow_scope_certified:false,
   observation_counts:{nodes_inspected:all.length,visible_controls:controls.length,open_shadow_hosts_observed,traversal_limit_reached}};
})(SETTINGS)'''


async def wait_for_setup_change(browser):
    """Bounded read-only DOM/hash readiness; timeout always triggers a fresh read.

    No DOM markers, field listeners or action permissions are installed. All
    observer/listener resources are removed on mutation, transition or deadline.
    """
    await browser.page.evaluate(r'''new Promise(resolve=>{
      let done=false,observer,timer;
      const finish=()=>{if(done)return;done=true;observer.disconnect();
        clearTimeout(timer);removeEventListener('hashchange',finish);resolve(true)};
      const scope='form,input,select,[role=combobox],h1,h2,h3,h4,[role=heading],[aria-current],[aria-selected]';
      const relevant=(node,descendants=false)=>{const e=node.nodeType===1?node:node.parentElement;
        return !!e&&(!!e.closest(scope)||descendants&&!!e.querySelector(scope))};
      observer=new MutationObserver(records=>{
        if(records.some(r=>relevant(r.target)||[...r.addedNodes].some(n=>relevant(n,true))))finish();
      });
      observer.observe(document,{subtree:true,childList:true,attributes:true,characterData:true});
      addEventListener('hashchange',finish,{once:true});timer=setTimeout(finish,500);
    })''')


async def observe_setup(browser):
    """Private descriptor snapshot; callers enforce protection and sanitize output."""
    return await browser.page.evaluate(OBSERVE_SETUP.replace('SETTINGS',json.dumps({'labels':LABELS,'values':VALUES})))

OBSERVE_SETUP=OBSERVE_SETUP.replace("FIELD_ALIASES_JSON",json.dumps(dict(FIELD_ALIASES)))


# One V2 task-scoped exploration surface. Private selectors never leave tools.
# Unlike final readback this describes actionable nodes, not completed goals.
V2_EXPLORE=r'''(s=>{
 const norm=x=>String(x??'').replace(/\s+/g,' ').trim();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const unique=q=>{if(!q)return null;const a=document.querySelectorAll(q);return a.length===1?a[0]:null};
 const forms=[...document.querySelectorAll('form,[role=form]')].filter(visible);
 const declared=unique(s.scope),roots=forms.length?forms:[...document.querySelectorAll('main,[role=main]')].filter(visible);
 const scope=declared||(roots.length===1?roots[0]:document.body);
 if(!scope||!visible(scope))return {stage_visible:false,nodes:[],field_candidates:[]};
 const selector=e=>{if(e.id){const q='#'+CSS.escape(e.id);if(unique(q)===e)return q;}
   const parts=[];for(let n=e;n&&parts.length<32;n=n.parentElement){
    const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
    parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
    const q=parts.join(' > ');if(unique(q)===e)return q;}return null;};
 const label=e=>norm(e.getAttribute('aria-label')||[...(e.labels||[])].map(n=>n.textContent).join(' ')||
   (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' '));
 const bindings=Object.fromEntries(Object.entries(s.fields).filter(([field])=>s.reviewed_fields.includes(field))),fieldCandidates=[];
 const scopeProven=!!declared||roots.length===1;
 for(const e of [...scope.querySelectorAll('input,textarea,select,[role=combobox]')].slice(0,1000)){
   if(!visible(e)||e.type==='password'||e.type==='file'||e.type==='hidden')continue;
   const key=s.aliases[label(e).toLowerCase()];if(!key||!s.goal_fields.includes(key))continue;
   let kind=e.tagName==='SELECT'?'native_select':e.type==='checkbox'?'checkbox':e.type==='radio'?'radio':
     e.getAttribute('role')==='combobox'?'combobox':['INPUT','TEXTAREA'].includes(e.tagName)?'text':null;
   const q=selector(e);if(!kind||!q)continue;
   fieldCandidates.push({field:key,selector:q,kind,field_label:label(e).toLowerCase(),scope_selector:scopeProven?selector(scope):null});
 }
 for(const c of fieldCandidates){
   if(bindings[c.field]||fieldCandidates.filter(x=>x.field===c.field).length!==1||!scopeProven)continue;
   const e=unique(c.selector),ids=((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
   const popups=ids.map(id=>document.getElementById(id)).filter(Boolean);
   const searches=popups.flatMap(p=>[...p.querySelectorAll('input[type=search],[role=searchbox]')]).filter(visible);
   const selected=popups.flatMap(p=>[...p.querySelectorAll('[role=option][aria-selected=true]')]).filter(visible);
   bindings[c.field]={selector:c.selector,kind:c.kind,selected_selector:selected.length===1?selector(selected[0]):null,
     search_selector:searches.length===1?selector(searches[0]):null,option_selector:c.kind==='combobox'?'[role=option]':null};
 }
 const publicLabels=new Set(['next step','save and preview','save & preview','setup','audience','content','schedule',
   'campaign name','waba id','business number','add tags','ga tracking','lists/segments','send later',
   'frequency cap','contact limit','search','close','cancel']);
 const forbidden=e=>/\b(publish|activate|send test|send now|save & publish|save and publish|finish later)\b/i.test(
   norm(e.textContent)+' '+norm(e.getAttribute('aria-label')))||e.type==='password'||e.type==='file';
 const candidates=[...scope.querySelectorAll('input,textarea,select,button,a[href],[role=combobox],[role=option],[role=button],[role=searchbox]')];
 for(const [field,b] of Object.entries(bindings)){
   const c=unique(b.selector);if(!c||!scope.contains(c))continue;
   candidates.push(c);
   for(const id of ((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean)){
    const popup=document.getElementById(id);
    if(popup)candidates.push(...popup.querySelectorAll('[role=option],input[type=search],[role=searchbox]'));
   }
 }
 const nodes=[];
 for(const e of [...new Set(candidates)].slice(0,1000)){
   if(!visible(e)||forbidden(e))continue;
   const q=selector(e);if(!q)continue;
   let field=null,part='control';
   for(const [k,b] of Object.entries(bindings)){
    if(unique(b.selector)===e){field=k;break;}
    if(b.search_selector&&unique(b.search_selector)===e){field=k;part='search';break;}
    const c=unique(b.selector),ids=c?((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/):[];
    if(e.matches('[role=option]')&&ids.some(id=>id&&document.getElementById(id)?.contains(e))){field=k;part='option';break;}
   }
   const text=norm(e.getAttribute('aria-label')||e.labels?.[0]?.textContent||e.textContent);
   const expected=field?s.values[field]:null;
   const exact=typeof expected==='string'&&(norm(e.value)===expected||norm(e.textContent)===expected);
   const selected=e.tagName==='SELECT'?[...e.selectedOptions]:[];
   const optionFacts=e.tagName==='SELECT'?[...e.options].slice(0,200).map((o,i)=>({index:i,enabled:!o.disabled,selected:o.selected,exact:typeof expected==='string'&&(norm(o.value)===expected||norm(o.textContent)===expected)})):[];
   nodes.push({selector:q,field,part,tag:e.tagName,role:e.getAttribute('role'),type:e.type||null,
    label:publicLabels.has(text.toLowerCase())?text.toLowerCase():null,
    field_label:field?label(e).toLowerCase():null,selected_exact:selected.length===1&&(norm(selected[0].value)===expected||norm(selected[0].textContent)===expected),
    checked:e.type==='checkbox'||e.type==='radio'?e.checked:null,option_facts:optionFacts,
    enabled:!e.disabled&&!e.readOnly&&e.getAttribute('aria-disabled')!=='true',
    expanded:e.getAttribute('aria-expanded')==='true',exact,create:e.hasAttribute('data-create')||e.getAttribute('data-select2-tag')==='true'});
 }
 const advance=[...scope.querySelectorAll('button,[role=button],input[type=submit]')].filter(e=>visible(e)&&!forbidden(e)&&
   (s.stage==='schedule'?['save and preview','save & preview'].includes(norm(e.textContent||e.value).toLowerCase()):norm(e.textContent||e.value).toLowerCase()==='next step'));
 const previews=[...scope.querySelectorAll('[role=region]')].filter(e=>visible(e)&&norm(e.getAttribute('aria-label')).toLowerCase()==='template preview');
 const previewIdentity=s.stage==='preview'?[...scope.querySelectorAll('h1,h2,h3,[role=heading]')].filter(e=>visible(e)&&
   ['preview','campaign preview','whatsapp preview','whatsapp campaign preview'].includes(norm(e.textContent).toLowerCase())):[];
 return {stage_visible:!!declared||(scopeProven&&(fieldCandidates.length>0||previewIdentity.length===1)),nodes,truncated:candidates.length>1000,
   scope_selector:scopeProven?selector(scope):null,field_candidates:fieldCandidates,
   preview_identity_selector:previewIdentity.length===1?selector(previewIdentity[0]):null,
   advance_selector:scopeProven&&advance.length===1?selector(advance[0]):null,
   content_preview_selector:scopeProven&&previews.length===1?selector(previews[0]):null,discovered_bindings:bindings};
})(SETTINGS)'''


async def explore_v2(browser,ui,payload,stage,*,reviewed_fields=None,reviewed_stages=None):
    from modules.whatsapp.contract import STAGE_FIELDS,FIELD_ALIASES
    aliases={alias:('tag' if field=='tags' else field) for alias,field in FIELD_ALIASES.items()}
    aliases.update({field.replace('_',' '):field for field in STAGE_FIELDS[stage]})
    aliases.update({'waba id':'waba','add tags':'tag','lists/segments':'audience_type',
        'include audience':'include_audience','exclude audience':'exclude_audience','template':'template',
        'send later':'send_type','frequency cap':'frequency_cap_enabled','contact limit':'contact_limit_enabled'})
    trusted_fields=set(ui.fields) if reviewed_fields is None and ui.reviewed else set(reviewed_fields or ())
    trusted_stages=set(ui.stage_selectors) if reviewed_stages is None and ui.reviewed else set(reviewed_stages or ())
    settings={'stage':stage,'scope':ui.stage_selectors.get(stage) if stage in trusted_stages else None,
        'reviewed_fields':sorted(trusted_fields),'goal_fields':STAGE_FIELDS[stage],'aliases':aliases,
        'fields':{k:v.model_dump() for k,v in ui.fields.items()},'values':payload.model_dump()}
    return await browser.page.evaluate(V2_EXPLORE.replace('SETTINGS',json.dumps(settings)))
