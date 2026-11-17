"""Private, read-only accessible control discovery for the isolated baseline."""

DISCOVER = r'''(settings=>{
 const norm=s=>(s||'').replace(/\s+/g,' ').trim();
 const visible=e=>!!e&&!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 const text=e=>{if(!e)return '';const copy=e.cloneNode(true);
   copy.querySelectorAll('input,select,textarea,[role=listbox]').forEach(n=>n.remove());return norm(copy.textContent)};
 const simple=s=>norm(s).replace(/[\s*:]+$/g,'').toLowerCase();
 const hints=FIELD_ALIASES_JSON;
 const path=e=>{
   const root=e.getRootNode();let n=e,parts=[],local=null;
   if(e.id){const s='#'+CSS.escape(e.id);if(root.querySelectorAll(s).length===1)local=s;}
   for(let depth=0;!local&&n&&depth<128;depth++,n=n.parentElement){
     const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
     parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
     const s=parts.join(' > ');if(s.length>2048)break;
     if(root.querySelectorAll(s).length===1)local=s;
   }
   if(!local)return null;const parent=root.host?path(root.host):[];
   return parent?[...parent,local]:null;
 };
 const all=[],roots=[document];let limit=false;
 for(let r=0;r<roots.length;r++){
   const walk=document.createTreeWalker(roots[r],NodeFilter.SHOW_ELEMENT);let e;
   while(e=walk.nextNode()){
     if(all.length>=12000){limit=true;break;}all.push(e);
     if(e.shadowRoot){if(roots.length>=65)limit=true;else roots.push(e.shadowRoot);}
   }
   if(limit)break;
 }
 const label=e=>{
   const labels=[...(e.labels||[])].filter(visible);
   if(labels.length)return {value:norm(labels.map(text).join(' ')),source:'VISIBLE_LABEL'};
   const root=e.getRootNode(),ids=(e.getAttribute('aria-labelledby')||'').split(/\s+/).filter(Boolean);
   const named=ids.map(id=>root.querySelector('#'+CSS.escape(id)));
   if(named.length&&named.every(n=>n&&visible(n)))return {value:norm(named.map(text).join(' ')),source:'VISIBLE_LABELLEDBY'};
   if(e.getAttribute('aria-label'))return {value:norm(e.getAttribute('aria-label')),source:'ARIA_LABEL'};
   for(let p=e.parentElement,depth=0;p&&depth<3;depth++,p=p.parentElement){
     const nearby=[...p.querySelectorAll('label')].filter(visible);
     if(nearby.length===1)return {value:text(nearby[0]),source:'NEARBY_VISIBLE_LABEL'};
   }
   return {value:'',source:'UNLABELED'};
 };
 const safe=e=>!e.isContentEditable&&!e.closest('[contenteditable]:not([contenteditable=false])')&&
   !e.hasAttribute('href')&&!e.hasAttribute('formaction')&&
   (e.tagName==='SELECT'||e.tagName==='INPUT'&&['text','search',''].includes(e.type)||
    e.getAttribute('role')==='combobox'&&(['DIV','SPAN'].includes(e.tagName)||e.tagName==='BUTTON'&&e.type==='button'))&&
   !(e.getAttribute('role')==='combobox'&&/\b(save|next(?:\s+step)?|finish\s+later|schedule|publish|activate|send)\b/i.test(e.textContent||''));
 const active=e=>[e,e.parentElement].filter(Boolean).some(n=>
   ['step','page','true'].includes(n.getAttribute('aria-current'))||n.getAttribute('aria-selected')==='true'||
   [...n.classList].some(c=>/(^|[-_])(active|current|selected)([-_]|$)/i.test(c)));
 const steps=all.filter(e=>visible(e)&&e.matches('h1,h2,h3,h4,[role=heading],[aria-current],[aria-selected],li,button,a'));
 const other=steps.find(e=>['audience','content','schedule'].includes(simple(e.textContent))&&active(e));
 const setup=steps.some(e=>simple(e.textContent)==='setup'&&(active(e)||e.matches('h1,h2,h3,h4,[role=heading]')))&&!other;
 const legacySteps=steps.filter(e=>e.getRootNode()===document);
 const legacy=legacySteps.some(e=>e.textContent.length<=1024&&/\bwhatsapp\b/i.test(e.textContent));
 const legacySetup=legacySteps.some(e=>simple(e.textContent)==='setup'&&(active(e)||e.matches('h1,h2,h3,h4,[role=heading]')))&&
   !legacySteps.some(e=>['audience','content','schedule'].includes(simple(e.textContent))&&active(e));
 const controls=[];
 for(const e of all.filter(e=>e.matches('input,select,[role=combobox]')&&safe(e))){
   if(controls.length>=100){limit=true;break;}
   const l=label(e),field=hints[simple(l.value)]||null,p=path(e);
   const disabled=!!e.disabled||!!e.readOnly||e.matches(':disabled')||e.getAttribute('aria-disabled')==='true'||!!e.closest('[inert]');
   const kind=e.tagName==='SELECT'?'NATIVE_SELECT':e.tagName==='INPUT'&&e.getAttribute('role')!=='combobox'?'TEXT_INPUT':'OWNED_COMBOBOX';
   const root=e.getRootNode();const ids=[...new Set(((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean))];
   const lists=ids.map(id=>root.querySelector('#'+CSS.escape(id)));
   const owned=kind==='NATIVE_SELECT'||ids.length>0&&lists.every(n=>n&&n.matches('[role=listbox]'))&&
     !all.some(n=>n!==e&&n.getAttribute('role')==='combobox'&&
       ((n.getAttribute('aria-controls')||'')+' '+(n.getAttribute('aria-owns')||'')).split(/\s+/).some(id=>ids.includes(id)));
   const options=kind==='NATIVE_SELECT'?[...e.options]:owned?lists.flatMap(n=>[...n.querySelectorAll('[role=option]')]):[];
   const desired=settings.values[field];
   const match=o=>!o.disabled&&!o.closest('optgroup')?.disabled&&!o.closest('[inert],[aria-disabled=true]')&&
     !o.isContentEditable&&!o.closest('[contenteditable]:not([contenteditable=false])')&&
     o.getAttribute('aria-disabled')!=='true'&&!o.hasAttribute('href')&&!o.hasAttribute('formaction')&&
     o.getAttribute('data-create')!=='true'&&o.getAttribute('data-select2-tag')!=='true'&&
     (field==='waba'?(o.value===desired||o.getAttribute('data-waba-id')===desired||norm(o.textContent)===desired):norm(o.textContent)===desired)&&
     (kind==='NATIVE_SELECT'?!!o.value:field!=='tags'||o.getAttribute('data-existing')==='true')&&
     (kind==='NATIVE_SELECT'||['DIV','SPAN','LI'].includes(o.tagName)||o.tagName==='BUTTON'&&o.type==='button');
   const choices=field?options.filter(match):[];
   const selected=kind==='TEXT_INPUT'?!!field&&e.value===desired:kind==='NATIVE_SELECT'?
     [...e.selectedOptions].length===1&&[...e.selectedOptions].some(match):!!field&&
       (field==='waba'?(e.value===desired||e.getAttribute('data-waba-id')===desired):norm(e.value||e.innerText||e.textContent)===desired)&&
       choices.length===1&&choices[0].getAttribute('aria-selected')==='true';
   controls.push({path:p,field_hint:field,visible_label:field&&l.source!=='ARIA_LABEL'?l.value:null,
     accessible_label:field?l.value:null,label_source:l.source,label_class:field||'OTHER_OR_UNESTABLISHED',
     label_present:!!l.value,kind,visible:visible(e),enabled:!disabled,safe:true,selected,owned,
     has_other_tags:field==='tags'&&(kind==='NATIVE_SELECT'?[...e.selectedOptions].some(o=>o.value&&!match(o)):
       options.some(o=>o.getAttribute('aria-selected')==='true'&&!match(o))),
     options:choices.map(o=>({path:path(o),value:kind==='NATIVE_SELECT'?o.value:null,visible:visible(o)}))});
 }
 // Extend the same bounded observation for non-Campaign widgets. The working
 // Campaign collection and its text-entry semantics above remain unchanged.
 const remaining=['waba','business_number','tags'];
 const fieldName=e=>{
   const s=simple(text(e));
   return hints[s]||null;
 };
 const parent=e=>e?.parentElement||e?.getRootNode()?.host||null;
 const within=(e,ancestor)=>{for(let n=e,depth=0;n&&depth<128;depth++,n=parent(n))if(n===ancestor)return true;return false};
 const notDangerous=e=>!!e&&!e.isContentEditable&&!e.closest('[contenteditable]:not([contenteditable=false])')&&
   !e.hasAttribute('href')&&!e.hasAttribute('formaction')&&
   !/\b(save|next(?:\s+step)?|finish\s+later|schedule|publish|activate|send)\b/i.test(norm(e.textContent));
 const trigger=e=>notDangerous(e)&&!e.closest('[role=listbox]')&&(
   e.getAttribute('role')==='combobox'&&['INPUT','DIV','SPAN','BUTTON'].includes(e.tagName)&&
     (e.tagName!=='BUTTON'||e.type==='button')||
   e.tagName==='BUTTON'&&e.type==='button'||e.getAttribute('role')==='button'&&
     ['listbox','true'].includes(e.getAttribute('aria-haspopup')));
 const inputDisplay=e=>e.tagName==='INPUT'&&['text','search',''].includes(e.type)&&
   (e.readOnly||e.disabled||e.getAttribute('aria-readonly')==='true');
 const staticDisplay=e=>notDangerous(e)&&(e.tagName==='OUTPUT'||
   e.hasAttribute('aria-valuetext')||e.hasAttribute('data-selected-value')||
   e.matches('span,div')&&e.hasAttribute('aria-labelledby')&&!e.querySelector('input,select,button,[role=combobox],[role=option]'));
 const principal=e=>visible(e)&&notDangerous(e)&&!e.closest('[role=listbox]')&&
   (e.tagName==='SELECT'||e.tagName==='INPUT'&&['text','search',''].includes(e.type)||trigger(e)||staticDisplay(e));
 const nativeDisabled=e=>!!e.disabled||e.matches(':disabled')||e.getAttribute('aria-disabled')==='true'||!!e.closest('[inert],[aria-disabled=true]');
 const byId=(root,id)=>{if(!id)return null;const found=root.querySelectorAll('#'+CSS.escape(id));return found.length===1?found[0]:null};
 const labelled=(e,l)=>l.tagName==='LABEL'&&(l.control===e||l.contains(e))||
   !!l.id&&(e.getAttribute('aria-labelledby')||'').split(/\s+/).includes(l.id);
 const rawLabels=all.filter(e=>visible(e)&&!e.matches('input,select,textarea,option,[role=option]')&&remaining.includes(fieldName(e)));
 const fieldLabels=rawLabels.filter(e=>!rawLabels.some(other=>other!==e&&fieldName(other)===fieldName(e)&&
   (e.tagName!=='LABEL'&&e.tagName!=='LEGEND'&&e.contains(other)||
    other.matches('label,legend')&&other.contains(e))));
 const fieldGroup=l=>{
   for(let g=parent(l),depth=0;g&&depth<5;depth++,g=parent(g)){
     if(g.matches('body,html,form,[role=form]'))break;
     const labels=fieldLabels.filter(n=>within(n,g));
     const visibleLabels=all.filter(n=>visible(n)&&n.matches('label,legend')&&within(n,g));
     if(labels.length!==1||visibleLabels.some(n=>!within(l,n)&&!within(n,l)&&fieldName(n)!==fieldName(l)))break;
     if(all.some(n=>principal(n)&&within(n,g)))return g;
   }
   return null;
 };
 const samePaths=(a,b)=>JSON.stringify(a)===JSON.stringify(b);
 const diagnostics={};
 for(const field of remaining){
   const labels=fieldLabels.filter(l=>fieldName(l)===field),descriptors=[],seen=new Set(),failures=new Set();
   if(!labels.length)failures.add('FIELD_LABEL_NOT_RECOGNIZED');
   for(const l of labels){
     const group=fieldGroup(l),direct=all.filter(e=>principal(e)&&labelled(e,l));
     const grouped=group?all.filter(e=>principal(e)&&within(e,group)):[];
     const buttons=grouped.filter(trigger),displays=grouped.filter(n=>!trigger(n)&&(inputDisplay(n)||staticDisplay(n)));
     let candidates=direct;
     if(buttons.length===1&&displays.length===1&&direct.includes(displays[0]))candidates=buttons;
     if(!candidates.length){
       if(buttons.length===1&&grouped.every(e=>e===buttons[0]||inputDisplay(e)||staticDisplay(e)))candidates=buttons;
       else if(grouped.length===1)candidates=grouped;
     }
     if(candidates.length!==1){failures.add(candidates.length>1||grouped.length>1?'AMBIGUOUS_FIELD_OWNER':'NO_ASSOCIATED_CONTROL');continue;}
     const e=candidates[0],p=path(e),key=JSON.stringify(p);
     if(!p||seen.has(key))continue;seen.add(key);
     const explicit=labelled(e,l),association=explicit?(l.tagName==='LABEL'?'EXPLICIT_LABEL':'EXPLICIT_ARIA'):'UNIQUE_FIELD_GROUP';
     const source=explicit?(l.tagName==='LABEL'?'VISIBLE_LABEL':'VISIBLE_LABELLEDBY'):'VISIBLE_FIELD_GROUP_LABEL';
     const root=e.getRootNode(),ids=[...new Set(((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean))];
     const named=ids.map(id=>byId(root,id));
     const competing=all.some(n=>n!==e&&trigger(n)&&
       ((n.getAttribute('aria-controls')||'')+' '+(n.getAttribute('aria-owns')||'')).split(/\s+/).some(id=>ids.includes(id)));
     let lists=ids.length&&named.every(n=>n&&n.matches('[role=listbox]'))&&!competing?named:[];
     if(!ids.length&&group&&buttons.length===1){
       const local=all.filter(n=>n.matches('[role=listbox]')&&within(n,group));
       if(local.length===1)lists=local;
     }
     const isSelect=e.tagName==='SELECT',isRead=inputDisplay(e),isStatic=staticDisplay(e)&&!trigger(e);
     const isCombo=e.getAttribute('role')==='combobox';
     const kind=isSelect?'NATIVE_SELECT':isRead&&!isCombo?'READ_ONLY_SELECTED_VALUE':isStatic?'STATIC_SELECTED_VALUE':
       isCombo?'OWNED_COMBOBOX':trigger(e)?'CUSTOM_DROPDOWN':'UNSUPPORTED_WIDGET';
     const outputIds=e.tagName==='OUTPUT'?(e.getAttribute('for')||'').split(/\s+/).filter(Boolean):[];
     let backing=outputIds.length===1?byId(root,outputIds[0]):null;
     if(backing&&!backing.matches('select,input[type=hidden]'))backing=null;
     if(!backing&&group){
       const backed=all.filter(n=>within(n,group)&&n.matches('select,input[type=hidden]')&&
         l.tagName==='LABEL'&&l.control===n);
       if(backed.length===1)backing=backed[0];
     }
     const display=buttons.length===1&&e===buttons[0]&&displays.length===1&&labelled(displays[0],l)?displays[0]:e;
     const desired=settings.values[field];
     // A directly labelled readonly current-value input is independently useful
     // even when its sibling trigger's popup is unmounted. It authorizes only
     // unchanged-value verification, never opening an unknown popup. Tag search
     // text always needs the existing selected-option/backing proof below.
     const readonlyDisplayProven=field!=='tags'&&display!==e&&inputDisplay(display)&&
       labelled(display,l)&&!!group&&buttons.length===1&&displays.length===1&&display.value===desired;
     const selectionOnly=readonlyDisplayProven&&!lists.length&&!backing;
     const owned=isSelect||isRead&&explicit||lists.length>0||!!backing||readonlyDisplayProven;
     const scope=lists.length?lists.flatMap(n=>all.filter(o=>within(o,n)&&o.matches('[role=option]'))):[];
     const options=isSelect?[...e.options]:scope;
     const optionValue=o=>o.tagName==='OPTION'?o.value:o.getAttribute('data-value')??o.getAttribute('data-waba-id')??o.getAttribute('value');
     const valueMatch=o=>field==='waba'?(optionValue(o)===desired||norm(o.textContent)===desired):norm(o.textContent)===desired;
     const allowed=o=>!o.disabled&&!o.closest('optgroup')?.disabled&&!o.closest('[inert],[aria-disabled=true]')&&
       !o.isContentEditable&&!o.closest('[contenteditable]:not([contenteditable=false])')&&!o.hasAttribute('href')&&!o.hasAttribute('formaction')&&
       o.getAttribute('data-create')!=='true'&&o.getAttribute('data-select2-tag')!=='true'&&
       (o.tagName==='OPTION'&&!!o.value||['DIV','SPAN','LI'].includes(o.tagName)||o.tagName==='BUTTON'&&o.type==='button');
     const backingOption=o=>backing?.tagName==='SELECT'?[...backing.options].filter(n=>valueMatch(n)&&
       optionValue(o)!==null&&optionValue(o)!==undefined&&n.value===optionValue(o)&&norm(n.textContent)===norm(o.textContent)):[];
     const existing=o=>field!=='tags'||o.tagName==='OPTION'||o.getAttribute('data-existing')==='true'||backingOption(o).length===1;
     const exact=options.filter(o=>allowed(o)&&valueMatch(o)&&(field!=='tags'||existing(o)));
     const selectedNative=(select)=>select?.tagName==='SELECT'&&[...select.selectedOptions].length===1&&
       [...select.selectedOptions].some(o=>allowed(o)&&valueMatch(o));
     // BUTTON.value defaults to an empty string even when the actual selected
     // display and explicit data-value changed. Only native value controls use
     // the value property; custom triggers use their observed attributes/text.
     const nativeValue=n=>n.tagName==='INPUT'||n.tagName==='SELECT';
     const currentText=norm(nativeValue(display)?display.value:
       display.getAttribute('aria-valuetext')||display.getAttribute('data-selected-value')||display.innerText||display.textContent);
     const currentValue=nativeValue(e)?e.value:e.getAttribute('data-value')??e.getAttribute('data-waba-id')??
       e.getAttribute('data-selected-value')??e.getAttribute('value');
     const selectedOptions=options.filter(o=>o.getAttribute('aria-selected')==='true');
     const exactSelected=exact.length===1&&selectedOptions.length===1&&selectedOptions[0]===exact[0]&&
       (field==='waba'?(currentValue===desired||currentText===desired||currentText===norm(exact[0].textContent)):currentText===desired);
     let selected=false,evidence='NOT_ESTABLISHED';
     if(isSelect&&selectedNative(e)){selected=true;evidence='NATIVE_SELECTED_OPTION';}
     else if(field!=='tags'&&isRead&&explicit&&e.value===desired){selected=true;evidence='READ_ONLY_CURRENT_VALUE';}
     else if(readonlyDisplayProven){selected=true;evidence='READ_ONLY_CURRENT_VALUE';}
     else if(backing){
       const backingApproved=backing.tagName==='SELECT'?selectedNative(backing):backing.value===desired;
       const concordant=backing.tagName==='SELECT'?currentText===norm(backing.selectedOptions[0]?.textContent)||currentText===desired:
         currentText===desired;
       if(backingApproved&&concordant){selected=true;evidence='OWNED_BACKING_VALUE';}
     }
     if(!selected&&exactSelected&&lists.length){selected=true;evidence='OWNED_SELECTED_OPTION';}
     // Only disagreeing positive current-selection evidence is a conflict.
     // A consistently different current selection remains an ordinary candidate
     // for the explicitly authorized existing-option operation.
     const positiveCurrentClaim=readonlyDisplayProven||field!=='tags'&&isRead&&explicit&&e.value===desired||
       exactSelected&&lists.length>0||isStatic&&owned&&currentText===desired;
     let backingConflict=false;
     if(backing&&positiveCurrentClaim){
       if(backing.tagName==='SELECT'){
         const chosen=[...backing.selectedOptions].filter(o=>!!o.value);
         backingConflict=chosen.length>0&&(chosen.length!==1||chosen.some(o=>!valueMatch(o)));
       }else if(backing.tagName==='INPUT'&&backing.value!==''){
         if(field==='waba')backingConflict=backing.value!==desired;
         else if(exactSelected&&exact.length===1){
           const mapped=optionValue(exact[0]);
           if(mapped!==null&&mapped!==undefined&&mapped!=='')backingConflict=backing.value!==mapped;
         }
       }
     }
     const confidence=owned&&kind!=='UNSUPPORTED_WIDGET'?association:'UNESTABLISHED';
     let failure=kind==='UNSUPPORTED_WIDGET'?'UNSUPPORTED_WIDGET':!owned?'OWNERSHIP_UNESTABLISHED':!selected?'SELECTION_NOT_PROVEN':null;
     if(confidence==='UNESTABLISHED')selected=false;
     if(backingConflict){selected=false;evidence='BACKING_SELECTION_CONFLICT';failure='BACKING_SELECTION_CONFLICT';}
     if(failure)failures.add(failure);
     // Fresh observation re-establishes label/group relationships. Only backing
     // values and distinct selected-value displays need an additional identity
     // pin; label/container replacement is not a global stability requirement.
     // Popup options are independently pinned immediately before selection.
     const nodes=[backing,display!==e?display:null].filter(n=>n&&n!==e);
     const evidence_paths=[...new Map(nodes.map(n=>[JSON.stringify(path(n)),path(n)])).values()].filter(Boolean);
     const descriptor={path:p,field_hint:field,visible_label:text(l),accessible_label:text(l),label_source:source,label_class:field,label_present:true,
       kind,visible:visible(e),enabled:!nativeDisabled(e)&&kind!=='READ_ONLY_SELECTED_VALUE'&&kind!=='STATIC_SELECTED_VALUE'&&!selectionOnly,
       safe:kind!=='UNSUPPORTED_WIDGET'&&!backingConflict,read_only:isRead||isStatic||nativeDisabled(e)||selectionOnly,selection_only:selectionOnly,
       owned,ownership_confidence:confidence,selected,
       selection_evidence:evidence,failure_code:failure,evidence_paths,owner_path:group?path(group):null,
       ownership_paths:[l,group].filter(Boolean).map(path).filter(Boolean),
       selected_value:backing?.tagName==='SELECT'&&backing.selectedOptions.length===1?backing.selectedOptions[0].value:
         backing?.tagName==='INPUT'?backing.value:isSelect&&e.selectedOptions.length===1?e.selectedOptions[0].value:
         readonlyDisplayProven?display.value:currentValue??null,
       list_paths:lists.map(path).filter(Boolean),expanded:e.getAttribute('aria-expanded')==='true'||lists.some(visible),
       has_other_tags:field==='tags'&&((isSelect?[...e.selectedOptions].some(o=>o.value&&!valueMatch(o)):
         selectedOptions.some(o=>!valueMatch(o)))||backing?.tagName==='SELECT'&&
         [...backing.selectedOptions].some(o=>o.value&&!valueMatch(o))),
       options:exact.map(o=>({path:path(o),value:isSelect?o.value:null,visible:visible(o),enabled:allowed(o),existing:existing(o),
         backing_value:isSelect?o.value:backingOption(o).length===1?backingOption(o)[0].value:null}))};
     descriptors.push(descriptor);
   }
   for(const d of descriptors){
     const at=controls.findIndex(c=>samePaths(c.path,d.path));
     if(at>=0)controls[at]=d;else if(controls.length<100)controls.push(d);else limit=true;
   }
   const observedGroups=labels.map(l=>{
     let g=fieldGroup(l);if(g)return g;
     for(let n=parent(l),depth=0;n&&depth<4;depth++,n=parent(n)){
       if(n.matches('body,html,form,[role=form]')||fieldLabels.filter(x=>within(x,n)).length!==1)break;
       if(all.some(x=>x!==l&&within(x,n)&&!within(x,l)&&!within(l,x)))return n;
     }
     return null;
   }).filter(Boolean);
   const observed=all.filter(n=>observedGroups.some(g=>within(n,g)));
   const structure_counts={native_selects:observed.filter(n=>n.tagName==='SELECT').length,
     aria_comboboxes:observed.filter(n=>n.getAttribute('role')==='combobox').length,
     button_triggers:observed.filter(trigger).length,read_only_inputs:observed.filter(inputDisplay).length,
     static_displays:observed.filter(staticDisplay).length,hidden_values:observed.filter(n=>n.matches('input[type=hidden]')).length,
     listboxes:observed.filter(n=>n.matches('[role=listbox]')).length,
     custom_element_hosts:observed.filter(n=>n.tagName.includes('-')).length,
     open_shadow_hosts:observed.filter(n=>!!n.shadowRoot).length,
     readonly_value_displays:observed.filter(n=>inputDisplay(n)||staticDisplay(n)||n.getAttribute('aria-readonly')==='true').length,
     unclassified_elements:observed.filter(n=>!n.matches('input,select,button,label,legend,output,[role=button],[role=combobox],[role=listbox],[role=option]')&&
       !staticDisplay(n)&&!n.shadowRoot&&!n.tagName.includes('-')).length};
   diagnostics[field]={label_count:labels.length,associated_count:descriptors.length,structure_counts,
     kinds:[...new Set(descriptors.map(d=>d.kind))],ownership_confidence:[...new Set(descriptors.map(d=>d.ownership_confidence))],
     selection_evidence:[...new Set(descriptors.map(d=>d.selection_evidence))],failure_codes:[...failures]};
 }
 const headings=all.filter(e=>visible(e)&&e.matches('h1,h2,h3,h4,[role=heading]')).map(e=>{
   const s=simple(e.textContent);return s==='setup'?'SETUP':/whatsapp/i.test(s)?'WHATSAPP':
     /^(create|new|edit) campaign$/.test(s)?'CAMPAIGN_EDITOR':'OTHER_OBSERVED_HEADING';});
 const publicHeadings=new Set(['setup','create campaign','new campaign','edit campaign','whatsapp campaign','whatsapp','create whatsapp campaign']);
 const heading_labels=all.filter(e=>visible(e)&&e.matches('h1,h2,h3,h4,[role=heading]')&&publicHeadings.has(simple(e.textContent))).map(e=>norm(e.textContent));
 return {controls,field_diagnostics:diagnostics,headings:[...new Set(headings)],heading_labels:[...new Set(heading_labels)],setup_active:setup,active_step:other?simple(other.textContent).toUpperCase():setup?'SETUP':'UNKNOWN',
   legacy_identity:{whatsapp_heading_probe:legacy,setup_active:legacySetup,page_identity:legacy&&legacySetup,
     failed_predicates:[...(!legacy?['LEGACY_WHATSAPP_HEADING_PROBE_FALSE']:[]),...(!legacySetup?['LEGACY_SETUP_STEP_NOT_ESTABLISHED']:[])]},
   access_denied:all.some(e=>visible(e)&&e.matches('[role=alert],.alert,.error,h1,h2')&&
     /access denied|permission denied|not authorized|unauthori[sz]ed|^forbidden$/i.test(norm(e.textContent))),
   traversal_complete:!limit,open_roots_inspected:roots.length-1,html_form_present:all.some(e=>e.matches('form,[role=form]'))};
})(SETTINGS)'''


def lookup(path):
    """Exact privately observed root chain, never a caller-supplied selector."""
    import json
    return '(parts=>{let root=document,e=null;for(const p of parts){const found=root?.querySelectorAll(p);'+\
        'if(!found||found.length!==1)return null;e=found[0];root=e.shadowRoot;}return e})('+json.dumps(path)+')'


COMMIT_BLOCK = r'''(()=>{
 if(window.__browserBaselineBlock)return true;
 const state={attempts:0};Object.defineProperty(window,'__browserBaselineBlock',{value:state});
 const deny=e=>{state.attempts++;e.preventDefault();e.stopImmediatePropagation()};
 document.addEventListener('submit',deny,true);
 document.addEventListener('keydown',e=>{if(e.key==='Enter')deny(e)},true);
 document.addEventListener('click',e=>{
   const nodes=e.composedPath().filter(n=>n?.matches?.('button,a,[role=button],[role=tab],input[type=submit]'));
   if(nodes.some(n=>n.type==='submit'||/\b(save|next(?:\s+step)?|finish\s+later|schedule|publish|activate|send)\b/i.test(
     n.textContent||n.value||n.getAttribute('aria-label')||'')||/^(audience|content)$/i.test((n.textContent||'').trim())))deny(e);
 },true);return true;
})()'''

from modules.whatsapp.contract import FIELD_ALIASES
import json as _json
DISCOVER=DISCOVER.replace("FIELD_ALIASES_JSON",_json.dumps(dict(FIELD_ALIASES)))
