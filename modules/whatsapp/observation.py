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
V2_LABEL_HELPERS = r'''
 const controlSelector='input,textarea,select,[role=combobox],[role=checkbox],[role=switch],[role=radio]';
 const gaLabels=new Set(['ga tracking','google analytics tracking','enable google analytics tracking']);
 const fieldLabel=(e,strategy)=>{
   const clean=value=>String(value??'').replace(/\s+/g,' ').trim();
   const shown=node=>!!node&&node.getClientRects().length>0&&getComputedStyle(node).visibility!=='hidden';
   const semantic=clean(e.getAttribute('aria-label')||[...(e.labels||[])].map(n=>n.textContent).join(' ')||
     (e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)?.textContent||'').join(' ')||
     e.getAttribute('title'));
   if(semantic||strategy!=='dom')return semantic;
   for(let parent=e.parentElement,depth=0;parent&&depth<3;parent=parent.parentElement,depth++){
     const controls=[...parent.querySelectorAll(controlSelector)].filter(shown);
     if(controls.length!==1||controls[0]!==e)break;
     const labels=[...parent.querySelectorAll('label,mat-label')].filter(n=>shown(n)&&
       (!n.getAttribute('for')||!!e.id&&n.getAttribute('for')===e.id)&&clean(n.textContent));
     if(labels.length===1)return clean(labels[0].textContent);
     if(labels.length>1)break;
     if(e.matches('input[type=checkbox],[role=checkbox],[role=switch]')){
       const analytics=[...parent.querySelectorAll('span,p,div')].filter(n=>shown(n)&&n.children.length===0&&
         gaLabels.has(clean(n.textContent).replace(/[:*]+$/g,'').trim().toLowerCase()));
       if(analytics.length===1)return clean(analytics[0].textContent);
       if(analytics.length>1)break;
     }
   }
   return clean(e.getAttribute('placeholder'));
 };
'''

TAG_CHIP_HELPERS = r'''
 const chipText=value=>String(value??'').replace(/\s+/g,' ').trim();
 const chipPainted=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const chipVisible=e=>chipPainted(e)&&!e.closest('[aria-hidden=true],[inert]');
 const tagRowSelector='[role=option]';
 const chipForbidden='a[href],input,[formaction],[data-create],[data-select2-tag=true],[aria-disabled=true],[inert]';
 const chipOwnerSelector='button,[role=button],[role=option]';
 const collectChipLabel=(root,exclude)=>{
   const collect=n=>{
     if(n===exclude)return '';
     if(n.nodeType===3)return n.textContent||'';
     if(n.nodeType!==1||getComputedStyle(n).visibility==='hidden'||getComputedStyle(n).display==='none')return '';
     return [...n.childNodes].map(collect).join('');
   };
   return collect(root).trim();
 };
 const chipActionCue=(n,marker)=>{
   if(chipText(n.innerText??n.textContent)===marker)return 'literal';
   for(const pseudo of ['::before','::after']){
     if(chipText(getComputedStyle(n,pseudo).content).replace(/^["']|["']$/g,'')===marker)return pseudo;
   }
   const icon=marker==='+'?['add','add_circle','add_circle_outline','plus']:['close','remove','cancel','clear'];
   const cues=[n.getAttribute('fonticon'),n.getAttribute('data-mat-icon-name'),
               n.getAttribute('aria-label'),n.getAttribute('title')].map(value=>chipText(value).toLowerCase());
   if(cues.some(value=>icon.includes(value)))return 'icon_attribute';
   if(n.matches('mat-icon,svg,i')&&icon.includes(chipText(n.textContent).toLowerCase()))return 'icon_text';
   const classes=chipText(n.getAttribute('class')).split(' ');
   if(classes.some(c=>icon.some(name=>['fa-','bi-','icon-','lucide-'].some(prefix=>c===prefix+name))))return 'icon_class';
   const href=n.getAttribute('href')||n.getAttribute('xlink:href');
   if(n.matches('use')&&href?.startsWith('#')&&icon.includes(href.slice(1)))return 'svg_reference';
   return null;
 };
 const chipParts=(e,marker)=>{
   const fail=(reason,count=0)=>({label:'',action:null,count,reason});
   if(!chipVisible(e)||e.closest(chipForbidden)||e.disabled||e.type==='submit')return fail('ROW_NOT_ACTIONABLE');
   const members=[e,...e.querySelectorAll('*')].filter(chipPainted);
   const candidates=members.filter(n=>!n.closest(chipForbidden)&&!!chipActionCue(n,marker));
   const leaves=candidates.filter(n=>!candidates.some(other=>other!==n&&n.contains(other)));
   if(leaves.length!==1)return fail(leaves.length?'ADD_MARKER_AMBIGUOUS':'ADD_MARKER_MISSING',leaves.length);
   const glyph=leaves[0];
   const owner=glyph.closest(chipOwnerSelector);
   const action=owner&&(owner===e||e.contains(owner))?owner:glyph;
   if(!chipPainted(action)||action.closest(chipForbidden)||action.disabled||action.type==='submit')
     return fail('ADD_TARGET_NOT_ACTIONABLE',1);
   const nested=[...e.querySelectorAll(tagRowSelector)].filter(chipPainted);
   // A nested option can supply the action only if it holds the glyph alone.
   if(nested.some(row=>!row.contains(glyph)||collectChipLabel(row,glyph)))
     return fail('NESTED_OPTION_OWNS_LABEL',1);
   // Exclude the glyph subtree, even when the clickable owner includes the label.
   // A CSS glyph on the owner itself contributes no DOM text.
   const cue=chipActionCue(glyph,marker);
   const label=collectChipLabel(e,cue.startsWith('::')?null:glyph);
   if(!label||/^create\s+new\b/i.test(label))return fail('EXISTING_LABEL_NOT_PROVEN',1);
   return {label,action,count:1,reason:'BOUND',cue,marker_tag:glyph.tagName,
           owner_tag:action.tagName,owner_is_option:action===e};
 };
 const tagChipShape=(e,chip,expected)=>{
   const compare=label=>label===expected?'EXACT':
     label&&label.toLowerCase()===expected.toLowerCase()?'CASE_ONLY':'OTHER';
   const visibleText=[e,...e.querySelectorAll('*')].filter(chipPainted);
   const exactLabel=visibleText.find(n=>compare(collectChipLabel(n,null))==='EXACT');
   const caseLabel=visibleText.find(n=>compare(collectChipLabel(n,null))==='CASE_ONLY');
   return {option_tag:e.tagName, option_role:e.getAttribute('role'), bind_status:chip.reason,
     label_comparison:compare(chip.label), label_node_tag:(exactLabel||caseLabel)?.tagName||null,
     visible_label_comparison:exactLabel?'EXACT':caseLabel?'CASE_ONLY':'OTHER',
     action_button_count:visibleText.filter(n=>n.matches('button,[role=button]')).length,
     icon_count:visibleText.filter(n=>n.matches('mat-icon,i,svg,use')).length,
     marker_matches:chip.count, marker_tag:chip.marker_tag||null, marker_cue:chip.cue||null,
     click_target_tag:chip.owner_tag||null, click_target_is_option:chip.owner_is_option===true};
 };

'''

CAMPAIGN_PICKER_HELPERS = r'''
 const campaignPickerField=field=>['include_audience','exclude_audience','template'].includes(field);
 const campaignText=value=>String(value??'').replace(/\s+/g,' ').trim();
 const campaignVisible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility==='visible'&&
   !e.closest('[aria-hidden=true],[inert]');
 const campaignHeadings=(stage)=>{
   const heading='h1,h2,h3,h4,h5,h6,[role=heading],legend';
   const nodes=[...document.querySelectorAll(heading)].filter(campaignVisible);
   const include=nodes.filter(e=>/^target audience$/i.test(campaignText(e.innerText)));
   const exclude=nodes.filter(e=>/^(exclude|excluded|exclusion)( audience| contacts| lists[ /]+segments)?( \(optional\))?$/i.test(campaignText(e.innerText)));
   return stage==='audience'?{include_audience:include,exclude_audience:exclude}:
     {template:nodes.filter(e=>/^select template$/i.test(campaignText(e.innerText)))};
 };
 const campaignPickerOwners=stage=>{
   const headings=campaignHeadings(stage), owners={};
   const anchors=Object.values(headings).flat();
   for(const [field,rows] of Object.entries(headings)){
     if(rows.length!==1)continue;
     const anchor=rows[0];
     for(let p=anchor.parentElement,depth=0;p&&p!==document.body&&depth<8;p=p.parentElement,depth++){
       if(anchors.some(n=>n!==anchor&&p.contains(n)))break;
       const triggers=[...p.querySelectorAll('mat-select[role=combobox],[role=combobox],select')]
          .filter(campaignVisible);
       if(triggers.length>1)break;
       if(triggers.length===1){owners[field]=triggers[0];break;}
     }
   }
   return owners;
 };
 const campaignOptionExact=(row,wanted)=>{
   if(!campaignVisible(row)||row.closest('[data-create],[aria-disabled=true],[inert]'))return false;
   const exact=n=>campaignText(n.innerText??n.textContent)===wanted;
   if(exact(row))return true;
   const leaves=[...row.querySelectorAll('td,[role=cell],span,div')]
      .filter(n=>campaignVisible(n)&&exact(n)&&n.closest('[role=option]')===row&&
          !n.closest('input,button,[role=checkbox],a[href]'));
   return leaves.filter(n=>!leaves.some(other=>n!==other&&n.contains(other))).length===1;
 };
 const campaignSelected=(trigger,wanted)=>{
   if(!campaignVisible(trigger))return false;
   const display=[...trigger.querySelectorAll('mat-select-trigger,.mat-mdc-select-value,.mat-select-value')]
     .filter(campaignVisible);
   const roots=display.filter(n=>!display.some(other=>other!==n&&other.contains(n)));
   if(roots.length>1)return false;
   const root=roots[0]||trigger;
   const text=n=>campaignText(n.innerText??n.textContent);
   if(text(root)===wanted)return true;
   const pills=[...root.querySelectorAll('mat-chip,mat-chip-row,mat-chip-option,[role=option]')]
     .filter(n=>campaignVisible(n)&&!n.closest('[role=listbox]'));
   if(pills.length>1)return false;
   const leaves=[...root.querySelectorAll('span,div')]
      .filter(n=>campaignVisible(n)&&text(n)===wanted&&!n.closest('input,button,[role=listbox],a[href]'));
   return leaves.filter(n=>!leaves.some(other=>n!==other&&n.contains(other))).length===1;
 };
'''

V2_EXPLORE=r'''(s=>{''' + V2_LABEL_HELPERS + TAG_CHIP_HELPERS + CAMPAIGN_PICKER_HELPERS + r'''
 const norm=x=>String(x??'').replace(/\s+/g,' ').trim();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const unique=q=>{if(!q)return null;const a=document.querySelectorAll(q);return a.length===1?a[0]:null};
 const selector=e=>{if(e.id){const q='#'+CSS.escape(e.id);if(unique(q)===e)return q;}
   const parts=[];for(let n=e;n&&parts.length<32;n=n.parentElement){
    const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
    parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
    const q=parts.join(' > ');if(unique(q)===e)return q;}return null;};
 const pickerOwners=s.campaign_walkthrough&&['audience','content'].includes(s.stage)?campaignPickerOwners(s.stage):{};
 const sectionField=e=>Object.keys(pickerOwners).find(key=>pickerOwners[key]===e);
 const label=e=>sectionField(e)?.replace('_',' ')||fieldLabel(e,s.strategy);
 const fieldKey=e=>{const key=sectionField(e)||s.aliases[label(e).replace(/[\s*:]+$/g,'').toLowerCase()];return key&&s.goal_fields.includes(key)?key:null};
 const forms=[...document.querySelectorAll('form,[role=form]')].filter(visible);
 const mains=[...document.querySelectorAll('main,[role=main]')].filter(visible);
 const declared=unique(s.scope);
 const labelledForms=forms.filter(form=>[...form.querySelectorAll(controlSelector)].some(e=>visible(e)&&fieldKey(e)));
 // The live Audience surface had multiple forms and one exact Lists/Segments
 // radio. Use its actual form owner (including an external form association),
 // or its exact radio group when it sits outside any form.
 const audienceModes=s.stage==='audience'?[...document.querySelectorAll('input[type=radio],[role=radio]')]
   .filter(e=>visible(e)&&fieldKey(e)==='audience_type'):[];
 const audienceMode=audienceModes.length===1?audienceModes[0]:null;
 const selectors='[role=tabpanel],mat-radio-group,[role=radiogroup],section';
 const namedAudienceControls=s.stage==='audience'?[...document.querySelectorAll(controlSelector)]
   .filter(e=>visible(e)&&fieldKey(e)):[];
 const containsAudienceFields=root=>!!root&&root.contains(audienceMode)&&namedAudienceControls.every(e=>root.contains(e));
 let group=null;
 for(let n=audienceMode?.parentElement;n&&n!==document.body;n=n.parentElement){
   if(n.matches(selectors)&&containsAudienceFields(n)){group=n;break;}
 }
 const audienceForm=audienceMode?.form||audienceMode?.closest('form,[role=form]');
 let campaignScope=null;
 const anchor=audienceMode||pickerOwners.template;
 if(anchor&&Object.keys(pickerOwners).length){
   for(let n=anchor.parentElement;n&&n!==document.body;n=n.parentElement){
     if(Object.values(pickerOwners).every(e=>n.contains(e))){campaignScope=n;break;}
   }
 }
 const audienceScope=containsAudienceFields(audienceForm)?audienceForm:
    containsAudienceFields(group)?group:null;
 const scope=declared||campaignScope||audienceScope||(forms.length===1?forms[0]:labelledForms.length===1?labelledForms[0]:
   forms.length===0&&mains.length===1?mains[0]:document.body);
 const scopeProven=!!declared||!!campaignScope||!!audienceScope||forms.length===1||labelledForms.length===1||
   forms.length===0&&mains.length===1;
 if(!scope||!visible(scope))return {stage_visible:false,nodes:[],field_candidates:[],scope_basis:'UNRESOLVED'};
 const bindings=Object.fromEntries(Object.entries(s.fields).filter(([field])=>s.reviewed_fields.includes(field))),fieldCandidates=[];
 for(const e of [...scope.querySelectorAll(controlSelector)].slice(0,1000)){
   if(!visible(e)||e.type==='password'||e.type==='file'||e.type==='hidden')continue;
   const key=fieldKey(e);if(!key)continue;
   let kind=e.tagName==='SELECT'?'native_select':e.type==='checkbox'?'checkbox':e.type==='radio'?'radio':
     (e.getAttribute('role')==='checkbox'||(key==='ga_tracking'&&e.getAttribute('role')==='switch'&&e.type!=='submit'))?'checkbox':
     e.getAttribute('role')==='radio'?'radio':
     e.getAttribute('role')==='combobox'?'combobox':['INPUT','TEXTAREA'].includes(e.tagName)?'text':null;
   const q=selector(e);if(!kind||!q)continue;
   fieldCandidates.push({field:key,selector:q,kind,field_label:label(e).toLowerCase(),scope_selector:scopeProven?selector(scope):null});
 }
 for(const c of fieldCandidates){
   if(bindings[c.field]||fieldCandidates.filter(x=>x.field===c.field).length!==1||!scopeProven)continue;
   const e=unique(c.selector),ids=((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
   const popups=ids.map(id=>document.getElementById(id)).filter(Boolean);
   const searches=popups.flatMap(p=>[...p.querySelectorAll('input:not([type]),input[type=text],input[type=search],[role=searchbox]')]).filter(visible);
   // Selection proof must remain outside the popup after it closes. Inspect
   // only this control or its one-control parent, never an unrelated value.
   const parent=e.parentElement;
   const parentControls=parent?[...parent.querySelectorAll(controlSelector)].filter(visible):[];
   const displayRoot=e.matches('input,textarea')?
     parentControls.length===1&&parentControls[0]===e?parent:null:e;
   const displayNodes=displayRoot?[displayRoot,...displayRoot.querySelectorAll('*')].filter(n=>
     visible(n)&&!n.isContentEditable&&!n.matches('input,textarea,select,button,label,svg,path,i,script,style,[role=searchbox],[role=option]')&&
     n.getAttribute('aria-hidden')!=='true'&&!popups.some(p=>p===n||p.contains(n))&&
     !n.querySelector('input,textarea,select,button,[role=option],[role=searchbox],[contenteditable=true]')&&
     norm(n.textContent)&&norm(n.textContent).toLowerCase()!==c.field_label&&
     n.children.length===0):[];
   const exactDisplays=displayNodes.filter(n=>norm(n.textContent)===s.values[c.field]);
   const selected=exactDisplays.length===1?exactDisplays:displayNodes.length===1?displayNodes:[];
   bindings[c.field]={selector:c.selector,kind:c.kind,selected_selector:c.kind==='combobox'&&selected.length===1?selector(selected[0]):null,
     search_selector:searches.length===1?selector(searches[0]):null,option_selector:c.kind==='combobox'?'[role=option]':null};
 }
 const publicLabels=new Set(['next step','save and preview','save & preview','setup','audience','content','schedule',
   'campaign name','waba id','business number','add tags','ga tracking','lists/segments','send later',
   'frequency cap','contact limit','search','close','cancel','campaign','campaigns',
   'whatsapp','whatsapp campaigns','create campaign','new campaign']);
 const forbidden=e=>/\b(publish|activate|send test|send now|save & publish|save and publish|finish later)\b/i.test(
   norm(e.textContent)+' '+norm(e.getAttribute('aria-label')))||e.type==='password'||e.type==='file';
 const candidates=[...scope.querySelectorAll('input,textarea,select,button,a[href],[role=combobox],[role=checkbox],[role=switch],[role=radio],[role=option],[role=button],[role=searchbox]')];
 for(const path of s.snapshot_paths||[]){
   try{const node=document.evaluate(path,document,null,XPathResult.FIRST_ORDERED_NODE_TYPE,null).singleNodeValue;
     if(node instanceof Element&&scope.contains(node)&&node.matches(controlSelector))candidates.push(node);
   }catch{}
 }
 for(const [field,b] of Object.entries(bindings)){
   const c=unique(b.selector);if(!c||!scope.contains(c))continue;
   candidates.push(c);
   for(const id of ((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean)){
    const popup=document.getElementById(id);
    if(popup)candidates.push(...popup.querySelectorAll('[role=option],input[type=search],[role=searchbox]'));
   }
 }
 const nodes=[],nodeLimit=s.current_field?12000:1000;
 const candidateNodes=[...new Set(candidates)];
 const scopedOptions=s.current_field?{visible_owned:0,exact_existing:0,scan_limited:candidateNodes.length>nodeLimit}:null;
 const tagChipCounts={with_unique_add:0,ambiguous_add:0,exact_lowercase:0,case_only_match:0,
   plain_text_match:0,label_action_samples:[]};
 for(const e of candidateNodes.slice(0,nodeLimit)){
   if(!visible(e)||forbidden(e))continue;
   let field=null,part='control';
   for(const [k,b] of Object.entries(bindings)){
    if(unique(b.selector)===e){field=k;break;}
    if(b.search_selector&&unique(b.search_selector)===e){field=k;part='search';break;}
    const c=unique(b.selector),ids=c?((c.getAttribute('aria-controls')||'')+' '+(c.getAttribute('aria-owns')||'')).split(/\s+/):[];
    if(e.matches('[role=option]')&&ids.some(id=>id&&document.getElementById(id)?.contains(e))){field=k;part='option';break;}
   }
   const text=norm(e.getAttribute('aria-label')||e.labels?.[0]?.textContent||e.textContent);
   const expected=field?s.values[field]:null;
   let exact=typeof expected==='string'&&(norm(e.value)===expected||norm(e.textContent)===expected);
   if(s.campaign_walkthrough&&part==='option'&&campaignPickerField(field)) exact=campaignOptionExact(e,expected);
   if(s.current_field==='tag'&&part==='option'&&field==='tag'){
     const chip=chipParts(e,'+');
     if(chip.action)tagChipCounts.with_unique_add++;
     if(chip.count>1)tagChipCounts.ambiguous_add++;
     if(chip.action&&chip.label===expected)tagChipCounts.exact_lowercase++;
     if(chip.action&&chip.label!==expected&&chip.label.toLowerCase()===expected.toLowerCase())tagChipCounts.case_only_match++;
     if(exact)tagChipCounts.plain_text_match++;
     if(tagChipCounts.label_action_samples.length<32)
       tagChipCounts.label_action_samples.push(tagChipShape(e,chip,expected));
     exact=!!chip.action&&chip.label===expected;
   }
   if(s.current_field&&e.matches('[role=option]')){
     if(part!=='option'||field!==s.current_field)continue;
     scopedOptions.visible_owned++;
     if(!exact||e.hasAttribute('data-create')||e.getAttribute('data-select2-tag')==='true')continue;
     scopedOptions.exact_existing++;
   }
   const q=selector(e);if(!q)continue;
   const selected=e.tagName==='SELECT'?[...e.selectedOptions]:[];
   const checked=typeof e.checked==='boolean'?e.checked:
     e.getAttribute('aria-checked')==='true'?true:e.getAttribute('aria-checked')==='false'?false:null;
   const invalid=e.getAttribute('aria-invalid')==='true'||!!e.matches(':invalid');
   const errorId=e.getAttribute('aria-errormessage'),errorNode=errorId?document.getElementById(errorId):null;
   const optionFacts=e.tagName==='SELECT'?[...e.options].slice(0,200).map((o,i)=>({index:i,enabled:!o.disabled,selected:o.selected,exact:typeof expected==='string'&&(norm(o.value)===expected||norm(o.textContent)===expected)})):[];
   nodes.push({selector:q,field,part,tag:e.tagName,role:e.getAttribute('role'),type:e.type||null,
    label:publicLabels.has(text.toLowerCase())?text.toLowerCase():null,
    field_label:field?label(e).toLowerCase():null,selected_exact:selected.length===1&&(norm(selected[0].value)===expected||norm(selected[0].textContent)===expected),
    checked:e.type==='checkbox'||e.type==='radio'||['checkbox','switch','radio'].includes(e.getAttribute('role'))?checked:null,
    value_present:e.matches('input:not([type=hidden]),textarea,select')?
      (e.type==='checkbox'||e.type==='radio'?checked:e.tagName==='SELECT'?selected.length>0:norm(e.value).length>0):
      ['checkbox','switch','radio'].includes(e.getAttribute('role'))?checked:null,
    selected_count:e.tagName==='SELECT'?selected.length:null,visible:true,option_facts:optionFacts,
    enabled:!e.disabled&&!e.readOnly&&e.getAttribute('aria-disabled')!=='true'&&!e.closest('[inert]'),
    invalid,validation_error_present:invalid||visible(errorNode),
    expanded:e.getAttribute('aria-expanded')==='true',exact,create:e.hasAttribute('data-create')||e.getAttribute('data-select2-tag')==='true'});
 }
 const advance=[...scope.querySelectorAll('button,[role=button],input[type=submit]')].filter(e=>visible(e)&&!forbidden(e)&&
   (s.stage==='schedule'?['save and preview','save & preview'].includes(norm(e.textContent||e.value).toLowerCase()):norm(e.textContent||e.value).toLowerCase()==='next step'));
 const previews=[...scope.querySelectorAll('[role=region]')].filter(e=>visible(e)&&norm(e.getAttribute('aria-label')).toLowerCase()==='template preview');
 const previewIdentity=s.stage==='preview'?[...scope.querySelectorAll('h1,h2,h3,[role=heading]')].filter(e=>visible(e)&&
   ['preview','campaign preview','whatsapp preview','whatsapp campaign preview'].includes(norm(e.textContent).toLowerCase())):[];
 const setupHeading=[...scope.querySelectorAll('h1,h2,h3,h4,[role=heading]')].filter(e=>visible(e)&&
   norm(e.textContent).toLowerCase()==='setup');
 const setupForm=scope.matches('form,[role=form]')&&[...scope.querySelectorAll(controlSelector)].some(visible);
 const scopeBasis=declared?'REVIEWED_SCOPE':campaignScope?'CAMPAIGN_PICKER_SECTIONS':audienceScope?'EXACT_AUDIENCE_CONTROL_OWNER':forms.length===1?'UNIQUE_FORM':
   labelledForms.length===1?'UNIQUE_LABELLED_FORM':forms.length===0&&mains.length===1?'UNIQUE_MAIN':'UNRESOLVED';
 const scopedElements=[scope,...scope.querySelectorAll('*')].slice(0,12000);
 const openShadowHosts=scopedElements.filter(e=>!!e.shadowRoot).length;
 const gaRows=s.stage==='setup'?[...scope.querySelectorAll('input[type=checkbox],[role=checkbox],[role=switch]')]
   .filter(visible).slice(0,12).map(e=>{
     const parent=e.parentElement;
     const row=parent?.parentElement||parent;
     const rowText=norm(row?.textContent).toLowerCase();
     const labelText=fieldLabel(e,'dom').replace(/[\s*:]+$/g,'').toLowerCase();
     return {role:e.getAttribute('role')==='switch'?'switch':'checkbox',
       expected_label_match:gaLabels.has(labelText),label_present:!!labelText,
       analytics_mentioned:/\b(ga|google analytics)\b/.test(rowText),tracking_mentioned:/\btracking\b/.test(rowText),
       row_visible_controls:row?[...row.querySelectorAll(controlSelector)].filter(visible).length:0,
       checked:typeof e.checked==='boolean'?e.checked:
         e.getAttribute('aria-checked')==='true'?true:e.getAttribute('aria-checked')==='false'?false:null};
   }):null;
 const audiencePickers=s.stage==='audience'&&scopeProven?
   [...scope.querySelectorAll('select,[role=combobox]')].filter(visible).slice(0,12).map(e=>{
     const container=e.closest('mat-form-field,[role=group]')||e.parentElement;
     const local=[...container.querySelectorAll('label,mat-label')].filter(visible);
     const cues=[fieldLabel(e,s.strategy),e.getAttribute('placeholder'),e.getAttribute('aria-label'),
                 ...local.map(n=>n.innerText)].filter(Boolean);
     return {tag:e.tagName,recognized_field:fieldKey(e),label_sources:cues.length,
             include_cue:cues.some(v=>/\binclude\b/i.test(v)),
             exclude_cue:cues.some(v=>/\bexclude\b/i.test(v)),
             list_cue:cues.some(v=>/\blists?\b|\bsegments?\b/i.test(v)),
             popup_owner_declared:!!(e.getAttribute('aria-controls')||e.getAttribute('aria-owns')),
             disabled:e.matches(':disabled,[aria-disabled=true]')||!!e.closest('[aria-disabled=true],[inert]')};
   }):[];
 return {ga_row_evidence:gaRows,audience_picker_evidence:audiencePickers,stage_visible:!!declared||(scopeProven&&(fieldCandidates.length>0||
   setupHeading.length===1&&setupForm)),nodes,truncated:candidateNodes.length>nodeLimit,
   scoped_option_evidence:s.current_field==='tag'?{...scopedOptions,tag_chips:tagChipCounts}:scopedOptions,
   scope_selector:scopeProven?selector(scope):null,field_candidates:fieldCandidates,
   scope_basis:scopeBasis,observation_scope:'DOCUMENT_LIGHT_DOM',closed_shadow_scope_certified:false,
   observation_counts:{visible_forms:forms.length,visible_iframes:[...scope.querySelectorAll('iframe')].filter(visible).length,
     open_shadow_hosts:openShadowHosts,shadow_host_scan_truncated:scopedElements.length>=12000,
     field_candidates:fieldCandidates.length,
     audience_mode_count:audienceModes.length,audience_form_owner:!!audienceForm,
     audience_radio_group:!!group,audience_labelled_controls:namedAudienceControls.length},
   preview_identity_selector:previewIdentity.length===1?selector(previewIdentity[0]):null,
   advance_selector:scopeProven&&advance.length===1?selector(advance[0]):null,
   content_preview_selector:scopeProven&&previews.length===1?selector(previews[0]):null,discovered_bindings:bindings};
})(SETTINGS)'''


async def explore_v2(browser,ui,payload,stage,*,reviewed_fields=None,reviewed_stages=None,diagnostic=False,strategy=None,current_field=None,campaign_walkthrough=False):
    if diagnostic:
        if ui is not None or payload is not None:
            raise ValueError('Diagnostic observation cannot receive campaign context')
        settings={'stage':'diagnostic','scope':None,'reviewed_fields':[],'goal_fields':[],
            'aliases':{},'fields':{},'values':{}}
    else:
        from modules.whatsapp.contract import STAGE_FIELDS,FIELD_ALIASES
        aliases={alias:('tag' if field=='tags' else field) for alias,field in FIELD_ALIASES.items()}
        aliases.update({field.replace('_',' '):field for field in STAGE_FIELDS[stage]})
        if stage == 'setup':
            aliases.update({'google analytics tracking': 'ga_tracking', 'enable google analytics tracking': 'ga_tracking'})
        if stage == 'audience':
            aliases.update({name: 'include_audience' for name in ('include', 'include lists', 'include segments', 'include lists/segments')})
            aliases.update({name: 'exclude_audience' for name in ('exclude', 'exclude lists', 'exclude segments', 'exclude lists/segments')})
            aliases['lists / segments'] = 'audience_type'
        if stage == 'content':
            aliases.update({name: 'template' for name in ('select template', 'choose template', 'whatsapp template',
                                                          'select whatsapp template')})
        aliases.update({'waba id':'waba','add tags':'tag','lists/segments':'audience_type',
            'include audience':'include_audience','exclude audience':'exclude_audience','template':'template',
            'send later':'send_type','frequency cap':'frequency_cap_enabled','contact limit':'contact_limit_enabled'})
        trusted_fields=set(ui.fields) if reviewed_fields is None and ui.reviewed else set(reviewed_fields or ())
        trusted_stages=set(ui.stage_selectors) if reviewed_stages is None and ui.reviewed else set(reviewed_stages or ())
        settings={'stage':stage,'scope':ui.stage_selectors.get(stage) if stage in trusted_stages else None,
            'reviewed_fields':sorted(trusted_fields),'goal_fields':STAGE_FIELDS[stage],'aliases':aliases,
            'fields':{k:v.model_dump() for k,v in ui.fields.items()},'values':payload.model_dump()}
    settings['strategy']=strategy or 'semantic'
    settings['current_field'] = current_field
    settings['campaign_walkthrough'] = campaign_walkthrough
    if strategy=='semantic':
        # Deterministic SDK accessibility snapshot, not Stagehand's separately
        # metered model-backed observe API. Never expose its tree/URL map.
        snapshot=await browser.page.snapshot(include_iframes=False,timeout=3000)
        settings['snapshot_paths']=list(snapshot.xpath_map.values())[:1000]
        del snapshot
    return await browser.page.evaluate(V2_EXPLORE.replace('SETTINGS',json.dumps(settings)))
