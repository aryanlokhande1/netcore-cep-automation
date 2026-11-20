"""Passive, bounded DOM inventory. Candidates never establish authentication."""

VISIBILITY_HELPERS = r'''
 const styleCache=new WeakMap(),paintCache=new WeakMap();
 const styleOf=e=>{if(!styleCache.has(e)){const s=getComputedStyle(e);styleCache.set(e,{
   display:s.display,visibility:s.visibility,opacity:s.opacity,contentVisibility:s.contentVisibility,
   transform:s.transform,overflowX:s.overflowX,overflowY:s.overflowY,contain:s.contain});}return styleCache.get(e);};
 const composedParent=e=>e.assignedSlot||e.parentElement||e.getRootNode?.().host||null;
 const paintBlock=e=>{
   const path=[];let n=e,reason=null;
   for(let depth=0;n&&depth<128;depth++,n=composedParent(n)){
     if(paintCache.has(n)){reason=paintCache.get(n);n=null;break;}
     path.push(n);const s=styleOf(n);
     if(s.display==='none'){reason='ANCESTOR_DISPLAY_NONE';n=null;break;}
     if(s.opacity==='0'){reason='ANCESTOR_OPACITY_ZERO';n=null;break;}
     if(s.contentVisibility==='hidden'){reason='CONTENT_VISIBILITY_HIDDEN';n=null;break;}
   }
   if(n)reason='ANCESTOR_SCOPE_LIMIT';
   for(const item of path)paintCache.set(item,reason);
   return reason;
 };
 const renderState=e=>{
   if(!e)return {visible:false,reason:'NODE_UNAVAILABLE'};
   const s=styleOf(e),rects=[...e.getClientRects()],area=rects.some(r=>r.width>0&&r.height>0),blocked=paintBlock(e);
   const reason=e.isConnected===false?'NODE_DETACHED':blocked||
     (s.visibility==='hidden'||s.visibility==='collapse'?'CSS_VISIBILITY_HIDDEN':
     !rects.length?'NO_CLIENT_RECTS':!area?'ZERO_AREA_RECTS':'RENDERED_RECT');
   const r=e.getBoundingClientRect?.();
   const rect=r?{x:Math.round(r.x*10)/10,y:Math.round(r.y*10)/10,width:Math.round(r.width*10)/10,height:Math.round(r.height*10)/10}:null;
   return {visible:reason==='RENDERED_RECT',reason,rect_count:rects.length,positive_area:area,rect,
     display:s.display==='contents'?'CONTENTS':s.display==='none'?'NONE':'OTHER',
     transform_present:!!s.transform&&s.transform!=='none',
     in_viewport:!!r&&typeof innerWidth==='number'&&r.right>0&&r.bottom>0&&r.x<innerWidth&&r.y<innerHeight};
 };
 const visible=e=>renderState(e).visible;
'''

DOCUMENT_HELPERS = VISIBILITY_HELPERS + r'''
 const selector=(e,doc,account="")=>{
   const acceptable=s=>s.length<=256&&(!account||!s.includes(account))&&doc.querySelectorAll(s).length===1;
   if(e.id&&(!account||!e.id.includes(account))){const s="#"+CSS.escape(e.id);if(acceptable(s))return s;}
   const test=e.getAttribute("data-testid");
   if(test&&(!account||!test.includes(account))){const s="[data-testid="+JSON.stringify(test)+"]";if(acceptable(s))return s;}
   let node=e,parts=[];
   for(let n=0;node&&node!==doc.body&&n<8;n++,node=node.parentElement){
     if(!node.parentElement)break;
     const siblings=[...node.parentElement.children].filter(x=>x.tagName===node.tagName);
     parts.unshift(node.tagName.toLowerCase()+":nth-of-type("+(siblings.indexOf(node)+1)+")");
     const s=parts.join(" > ");if(acceptable(s)&&doc.querySelector(s)===e)return s;
   }
   return null;
 };
 const inventory={documents:[],permitted_frames_unavailable:0,external_frames_skipped:0,document_limit_reached:false};
 const visit=(doc,path,depth)=>{
   if(inventory.documents.length>=8){inventory.document_limit_reached=true;return;}
   inventory.documents.push({doc,path});
   const frames=[...doc.querySelectorAll("iframe,frame")];
   if(frames.length>32)inventory.document_limit_reached=true;
   for(const frame of frames.slice(0,32)){
     if(!visible(frame))continue;
     let origin;
     try{origin=new URL(frame.getAttribute("src")||doc.URL,doc.URL).origin}catch{continue;}
     if(origin==="null"&&(!frame.getAttribute("src")||frame.hasAttribute("srcdoc")))origin=new URL(doc.URL).origin;
     if(!settings.origins.includes(origin)){inventory.external_frames_skipped++;continue;}
     const s=selector(frame,doc,settings.account||"");
     try{
       const child=frame.contentDocument;
       if(!s||!child||depth>=3||!settings.origins.includes(new URL(['about:blank','about:srcdoc'].includes(child.URL)?doc.URL:child.URL).origin)){
         inventory.permitted_frames_unavailable++;continue;
       }
       visit(child,[...path,s],depth+1);
     }catch{inventory.permitted_frames_unavailable++;}
   }
 };
 visit(document,[],0);
 const scopedDocument=path=>{
   let doc=document;
   for(const s of path||[]){
     const matches=doc.querySelectorAll(s);
     if(matches.length!==1||!visible(matches[0]))return null;
     doc=matches[0].contentDocument;
     if(!doc||!inventory.documents.some(item=>item.doc===doc))return null;
   }
   return doc;
 };
'''


OPEN_ROOT_HELPERS = r"""
 const scopes=[];
 const coverage={open_shadow_roots_inspected:0,shadow_roots_uninspected:0,inaccessible_or_unknown_hosts:0,node_limit_reached:false,shadow_limit_reached:false};
 let visited=0;
 const collect=(root,path,shadow,depth)=>{
   const nodes=[];const stack=[{next:(root.body||root).firstElementChild}];
   while(stack.length){
     const cursor=stack[stack.length-1];const e=cursor.next;
     if(!e){stack.pop();continue;}
     cursor.next=e.nextElementSibling;
     if(++visited>12000){coverage.node_limit_reached=true;break;}
     nodes.push(e);
     if(e.shadowRoot){
       const host=selector(e,root,settings.account||'');
       if(!host||depth>=8||coverage.open_shadow_roots_inspected>=64){coverage.shadow_roots_uninspected++;coverage.shadow_limit_reached=true;}
       else {coverage.open_shadow_roots_inspected++;collect(e.shadowRoot,path,[...shadow,host],depth+1);}
     }else if(e.localName?.includes('-'))coverage.inaccessible_or_unknown_hosts++;
     if(e.firstElementChild)stack.push({next:e.firstElementChild});
   }
   scopes.push({doc:root,path,shadow,nodes});
 };
 for(const {doc,path} of inventory.documents)collect(doc,path,[],0);
 const scopedRoot=(frames,hosts)=>{
   let root=scopedDocument(frames);
   for(const host of hosts||[]){const matches=root?.querySelectorAll(host);if(matches?.length!==1||!matches[0].shadowRoot)return null;root=matches[0].shadowRoot;}
   return root;
 };
 const composedClosest=(element,query)=>{
   for(let e=element,depth=0;e&&depth++<128;e=composedParent(e)){
     if(e.matches?.(query))return e;
   }
   return null;
 };
 const contentsShellVisible=root=>{
   if(!root||styleOf(root).display!=='contents'||paintBlock(root)||
      styleOf(root).visibility==='hidden'||styleOf(root).visibility==='collapse'||
      !(root.matches('main,[role=main],[role=application]')||root.localName?.includes('-'))||
      composedClosest(root,'nav,[role=navigation],header,[role=banner],[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert'))return false;
   for(const {nodes} of scopes)for(const child of nodes){
     if(!visible(child)||!(child.matches('a,button,[role=button],[role=link]'))||
         composedClosest(child,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert'))continue;
     for(let n=child,depth=0;n&&depth++<128;n=composedParent(n))if(n===root)return true;
   }
   return false;
 };
"""

# Capture structural selectors only. Page-provided IDs, hrefs, and label values
# are intentionally excluded from this report.
TARGETED_LOGIN_CONTROL = r"""
 const structural=e=>{
   const parts=[];
   for(let node=e;node;node=node.parentElement){
     if(parts.length>=24)return null;
     let index=1;for(let prev=node.previousElementSibling;prev;prev=prev.previousElementSibling)index++;
     parts.unshift(':nth-child('+index+')');
   }
   const path=parts.join(' > '),root=e.getRootNode(),matches=root.querySelectorAll(path);
   return matches.length===1&&matches[0]===e?path:null;
 };
 const locator=e=>{
   if(!e)return null;
   const selector=structural(e),frames=[],shadows=[];
   if(!selector)return null;
   let root=e.getRootNode();
   for(let host=root.host;host;host=root.host){
     const s=structural(host);if(!s||shadows.length>=8)return null;
     shadows.unshift(s);root=host.getRootNode();
   }
   // Frame owners may be in a different DOM root. Capture those only when
   // their existing permitted frame path is also a plain document path.
   for(let doc=e.ownerDocument;doc!==document;){
     const frame=doc.defaultView?.frameElement;
     if(!frame||frame.getRootNode()!==frame.ownerDocument||frames.length>=3)return null;
     const s=structural(frame);if(!s)return null;
     frames.unshift(s);doc=frame.ownerDocument;
   }
   return {selector,frame_selectors:frames,shadow_selectors:shadows};
 };
 const identityComparison=e=>{
   const text=visible(e)?(e.innerText??e.textContent??'').trim():'';
   const direct=(e.getAttribute('aria-label')||'').trim();
   const root=e.getRootNode(),refs=(e.getAttribute('aria-labelledby')||'').trim().split(/\s+/).filter(Boolean);
   const labelNodes=refs.length<=8?refs.map(id=>root.getElementById?.(id)):null;
   const referenced=labelNodes?.length&&labelNodes.every(node=>node&&visible(node))?
     labelNodes.map(node=>(node.innerText??node.textContent??'').trim()).join(' '):'';
   const label=refs.length?referenced:direct;
   return {visible:visible(e),rendered_text_present:!!text,rendered_text_exact:!!settings.account&&text===settings.account,
     accessible_label_present:!!label,accessible_label_exact:!!settings.account&&label===settings.account,
     label_basis:refs.length?(referenced?'VISIBLE_ARIA_LABELLEDBY':'ARIA_LABELLEDBY_UNRESOLVED'):direct?'ARIA_LABEL':'NONE',
     locator:locator(e)};
 };
 const snapshotTargeted = ()=>{
   const excluded=e=>composedClosest(e,'[aria-hidden=true],[inert],[role=alert],[role=alertdialog],nxt-error-log-alert,form');
   const controls=[],hovered=[];
   for(const {doc,nodes} of scopes)for(const e of nodes){
     if(!visible(e)||excluded(e))continue;
     if(e.matches('[aria-expanded=true][aria-controls]')){
       const refs=(e.getAttribute('aria-controls')||'').trim().split(/\s+/);
       const panel=refs.length===1?doc.getElementById?.(refs[0]):null;
       if(panel&&visible(panel)&&!excluded(panel))controls.push({trigger:e,panel});
     }
     if(e.matches(':hover')&&e.children.length===0&&(e.textContent||'').trim()&&
         !e.matches('script,style,input,textarea'))hovered.push(e);
   }
   const hoveredRow=hovered.length===1?hovered[0]:null;
   const hoveredControls=hoveredRow?controls.filter(item=>item.panel.contains(hoveredRow)):[];
   const control=hoveredControls.length===1?hoveredControls[0]:controls.length===1?controls[0]:null;
   const hoverInPanel=!!(control&&hoveredRow&&control.panel.contains(hoveredRow));
   const panelRows=[];
   if(control){
     const walker=control.panel.ownerDocument.createTreeWalker(control.panel,NodeFilter.SHOW_ELEMENT);
     let n=control.panel;
     for(let i=0;n&&i<96;i++,n=walker.nextNode()){
       if(!visible(n)||excluded(n)||n.matches('script,style,input,textarea'))continue;
       if(n.children.length===0||n.hasAttribute('aria-label')||n.hasAttribute('aria-labelledby')){
         panelRows.push(identityComparison(n));
       }
     }
     if(n)return {status:'OPEN_ACCOUNT_PANEL_SCAN_LIMIT',expanded_panel_count:controls.length};
   }
   const dashboard=dashboard_candidates.filter(x=>x.kind==='APPLICATION_SHELL'||x.kind==='CONTENT_HEADING');
   const shells=dashboard.filter(x=>x.kind==='APPLICATION_SHELL'), eligible=shells.length?shells:dashboard;
   const dashboardRow=eligible.length===1?eligible[0]:null;
   const dashboardElement=dashboardRow?scopedRoot(dashboardRow.frame_selectors,dashboardRow.shadow_selectors)?.querySelector(dashboardRow.selector):null;
   return {
     status:control?'EXPANDED_CONTROL_AND_VISIBLE_PANEL_OBSERVED':hoveredRow?'OPERATOR_HOVER_ROW_OBSERVED':controls.length>1?'MULTIPLE_OPEN_PANELS_WITHOUT_UNIQUE_OWNER':'ACCOUNT_CONTROL_NOT_RETAINED',
     expanded_panel_count:controls.length,hovered_row_count:hovered.length,hover_is_in_bound_panel:hoverInPanel,
     trigger:control?identityComparison(control.trigger):null,panel:control?locator(control.panel):null,
     hovered_row:hoveredRow?identityComparison(hoveredRow):null,panel_rows:panelRows,
     dashboard:{candidate_count:eligible.length,kind:dashboardRow?.kind||null,locator:locator(dashboardElement)}
   };
 };
"""


DOM_DISCOVERY = r'''(settings => {''' + DOCUMENT_HELPERS + OPEN_ROOT_HELPERS + r'''
 const dashboard_candidates=[],navigation_candidates=[],tenant_candidates=[],calibration_tenant_labels=[];
 const rejections={dashboard:{},tenant:{},navigation:{}};
 const diagnostics={documents:inventory.documents.map((item,i)=>({document_ref:'document-'+(i+1),
   frame_depth:item.path.length,authorized:true})),rejections,semantic_samples:[],
   inspected_elements:0,cross_shadow_control_owners:0,
   dashboard_visibility_rejections:{},exact_tenant_rejections:{},calibration_label_rejections:{}};
 const count=(table,reason)=>table[reason]=(table[reason]||0)+1;
 const reject=(kind,reason)=>count(rejections[kind],reason);
 const roots=new Map(),nodeRoots=new Map(),controls=new Set(),content=new Set(),navs=new Set();
 for(const scope of scopes){
   let root=scopedDocument(scope.path);
   for(const host of scope.shadow){const element=root?.querySelector(host);if(!element){root=null;break;}
     roots.set(element.shadowRoot,element);root=element.shadowRoot;}
   for(const e of scope.nodes)nodeRoots.set(e,scope.doc);
 }
 const parent=e=>e.assignedSlot||e.parentElement||roots.get(nodeRoots.get(e))||null;
 const ancestor=(e,query)=>{for(let n=e,depth=0;n&&depth++<128;n=parent(n))if(n.matches(query))return n;return null;};
 const mark=(set,e)=>{for(let n=e,depth=0;n&&depth++<128;n=parent(n))set.add(n);};
 for(const {nodes} of scopes)for(const e of nodes){
   if(!visible(e)||ancestor(e,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert'))continue;
   if(e.matches('button,[role=button],[role=link]')||(e.matches('a')&&e.getAttribute('href')!==null))mark(controls,e);
   if(e.matches('main,[role=main],article,[role=region]'))mark(content,e);
   if(e.matches('nav,[role=navigation]'))mark(navs,e);
 }
 const safeTags=new Set(['main','nav','header','article','section','div','span','h1','h2','h3','h4','h5','h6','a','button']);
 const safeRoles=new Set(['main','application','navigation','banner','region','heading','button','link','alert','dialog']);
 let serial=0;const samples=[];
 for(const {doc,path,shadow,nodes} of scopes)for(const e of nodes){
   diagnostics.inspected_elements++;
   const text=(e.textContent||'').trim(),directlyShown=visible(e);
   const heading=e.matches('h1,h2,h3,h4,h5,h6,[role=heading]');
   const semantic=e.matches('main,[role=main],[role=application]');
   const custom=e.localName?.includes('-')&&content.has(e)&&navs.has(e)&&controls.has(e);
   const navigation=!!ancestor(e,'nav,[role=navigation],header,[role=banner]');
   const navPotential=(heading&&navigation)||e.matches('[aria-current=page]')||
     (navigation&&(e.matches('a,button,[role=link],[role=button]')));
   const dashboardPotential=heading||semantic||custom;
   const composedShell=dashboardPotential&&!directlyShown&&contentsShellVisible(e);
   const shown=directlyShown||composedShell;
   const tenantPotential=!!text&&text.length<=256&&!e.matches('script,style,input,textarea');
   const exactLabel=settings.calibration===true&&!!settings.account&&
     (e.getAttribute('aria-label')||'').trim()===settings.account&&!e.matches('script,style,input,textarea');
   if(!dashboardPotential&&!navPotential&&!tenantPotential&&!exactLabel)continue;
   const transient=!!ancestor(e,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert');
   const observedSelector=selector(e,doc,settings.account);
   const s=shown&&!transient?observedSelector:null;
   const gate=!shown?'NOT_VISIBLE':transient?'TRANSIENT_OR_HIDDEN_SCOPE':!s?'NO_UNIQUE_SAFE_SELECTOR':null;
   if(exactLabel){
     const labelGate=gate||(ancestor(e,'form')?'FORM_CONTEXT':null);
     if(labelGate)count(diagnostics.calibration_label_rejections,labelGate);
     else if(calibration_tenant_labels.length<16)calibration_tenant_labels.push({
       selector:s,frame_selectors:path,shadow_selectors:shadow,kind:'VISIBLE_EXACT_ARIA_LABEL_CLUE'});
   }
   if(dashboardPotential||navPotential||(tenantPotential&&shown)){
     const ref='element-'+(++serial),visibility=renderState(e);
     const priority=shown&&dashboardPotential?0:shown&&navPotential?1:dashboardPotential?2:navPotential?3:4;
     const sample={element_ref:ref,element_type:safeTags.has(e.localName)?e.localName:'CUSTOM_OR_OTHER',
       document_ref:'document-'+(inventory.documents.findIndex(item=>JSON.stringify(item.path)===JSON.stringify(path))+1),
       root_ref:'accessible-root-'+(scopes.findIndex(item=>item.doc===doc)+1),
       role:safeRoles.has(e.getAttribute('role'))?e.getAttribute('role'):'OTHER_OR_ABSENT',
       visible:shown,selector_eligible:!!observedSelector,frame_depth:path.length,shadow_depth:shadow.length,
       potential_dashboard:dashboardPotential,potential_navigation:navPotential,expected_account_match:text===settings.account,
       rendering:visibility,collector_gate:gate||'ELIGIBLE_PRECLASSIFICATION',composed_shell_exception:composedShell,
       scope_exclusions:[
         ancestor(e,'[aria-hidden=true]')?'ARIA_HIDDEN_SCOPE':null,
         ancestor(e,'[inert]')?'INERT_SCOPE':null,
         ancestor(e,'[role=alert],[role=alertdialog],[role=dialog],dialog')?'ALERT_OR_DIALOG_SCOPE':null,
         ancestor(e,'nxt-error-log-alert')?'ERROR_WIDGET_SCOPE':null].filter(Boolean),
       cross_shadow_controls:controls.has(e)&&!e.querySelector('a[href],button,[role=button],[role=link]')};
     samples.push({priority,sample,target:observedSelector?{element_ref:ref,selector:observedSelector,
       frame_selectors:path,shadow_selectors:shadow}:null});
     samples.sort((a,b)=>a.priority-b.priority);if(samples.length>48)samples.pop();
   }
   const exactTenant=tenantPotential&&!!settings.account&&text===settings.account;
   if(gate){
     if(dashboardPotential){
       reject('dashboard',gate);
       if(!shown)count(diagnostics.dashboard_visibility_rejections,renderState(e).reason);
     }
     if(navPotential)reject('navigation',gate);
     if(tenantPotential)reject('tenant',gate);
     if(exactTenant)count(diagnostics.exact_tenant_rejections,gate);
     continue;
   }
   if(dashboardPotential){
     if(navigation)reject('dashboard','NAVIGATION_CONTEXT');
     else if(heading&&(!text||text.length>256))reject('dashboard','HEADING_TEXT_UNSUITABLE');
     else if(!heading&&!controls.has(e))reject('dashboard','NO_OBSERVED_CONTROL');
     else{
       const shell=(semantic||custom)&&controls.has(e);
       if(shell&&!e.querySelector('a[href],button,[role=button],[role=link]'))diagnostics.cross_shadow_control_owners++;
       dashboard_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,
         kind:shell?'APPLICATION_SHELL':ancestor(e,'main,[role=main],article,[role=region]')?'CONTENT_HEADING':'UNSCOPED_HEADING',
         confidence:shell||ancestor(e,'main,[role=main],article,[role=region]')?'OPERATOR_REVIEW_REQUIRED':'LIMITED_NO_CONTENT_LANDMARK'});
     }
   }
   if(navPotential){if(!text||text.length>256)reject('navigation','NO_BOUNDED_ACCESSIBLE_TEXT');
     else navigation_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,kind:'NAVIGATION_ONLY',confidence:'NOT_DASHBOARD_EVIDENCE'});}
   if(tenantPotential){
     if(ancestor(e,'form')){
       reject('tenant','FORM_CONTEXT');
       if(exactTenant)count(diagnostics.exact_tenant_rejections,'FORM_CONTEXT');
     }
     else if(text!==settings.account)reject('tenant','NOT_EXACT_EXPECTED_ACCOUNT');
     else if([...e.children].some(c=>(c.textContent||'').trim()===settings.account)){
       reject('tenant','DUPLICATE_PARENT_TEXT');
       count(diagnostics.exact_tenant_rejections,'DUPLICATE_PARENT_TEXT');
     }
     else tenant_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,kind:'EXACT_EXPECTED_ACCOUNT_TEXT',confidence:'EXACT_MATCH_REQUIRES_REVIEW'});
   }
 }
''' + TARGETED_LOGIN_CONTROL + r'''
 diagnostics.semantic_samples=samples.map(item=>item.sample);
 const targeted_capture=settings.targeted_capture?snapshotTargeted():undefined;
 return {_render_targets:samples.filter(item=>item.target).slice(0,12).map(item=>item.target),dashboard_candidates:dashboard_candidates.slice(0,32),navigation_candidates:navigation_candidates.slice(0,32),
   tenant_candidates:tenant_candidates.slice(0,32),calibration_tenant_labels,dom_diagnostics:diagnostics,targeted_capture,
   dashboard_selectors:dashboard_candidates.filter(c=>!c.frame_selectors.length).map(c=>c.selector),
   tenant_selectors:tenant_candidates.filter(c=>!c.frame_selectors.length).map(c=>c.selector),
   documents_inspected:inventory.documents.length,permitted_frames_unavailable:inventory.permitted_frames_unavailable,
   external_frames_skipped:inventory.external_frames_skipped,document_limit_reached:inventory.document_limit_reached,...coverage};
})(SETTINGS)'''

from modules.login.negative_scan import NEGATIVE_STATES

# Lightweight semantic hints only. Readiness never supplies authentication gates.
# Traverse at most 512 nodes, including accessible roots, rather than inventorying
# the entire DOM on every poll. The full protected scan runs after readiness.
READINESS_DOM = r'''(() => {''' + VISIBILITY_HELPERS + r'''
 const result={shell:false,loading:document.readyState==='loading',credential_form:false,
   challenge:false,rejected:false,expired:false,account_restricted:false};
 const shellLandmarks=new Set();
 const markShell=e=>{
   result.shell=true;
   shellLandmarks.add(e.matches('nav,[role=navigation],header,[role=banner]')?'NAVIGATION_LANDMARK':'CONTENT_LANDMARK');
 };
 const stack=[document.documentElement];let inspected=0;const limit=512;
 while(stack.length&&inspected++<limit){
   const e=stack.pop();if(!e)continue;
   // Keep the pre-existing conservative security visibility rule. Rendered
   // shell classification is stricter and must not suppress negative signals.
   if(e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden'){
     result.credential_form ||= e.matches('input[type=password]');
     result.challenge ||= e.matches('iframe[src*=captcha],input[autocomplete=one-time-code]');
     if(e.matches('[role=alert],.alert,.error')){
       const text=(e.textContent||'').slice(0,4096).toLowerCase();
       result.rejected ||= /invalid (credentials|password|username)|incorrect (password|credentials)|login failed|authentication failed/.test(text);
       result.expired ||= /session (has )?expired|session timed out/.test(text);
       result.account_restricted ||= /account.{0,30}(locked|disabled|suspended|restricted)|access denied|permission denied|not authorized|too many (login|sign.?in) attempts/.test(text);
     }
   }
   if(visible(e)){
     result.loading ||= e.matches('[aria-busy=true],[role=progressbar]');
     if(e.matches('main,[role=main],nav,[role=navigation],header,[role=banner],[role=application]')&&
         !e.closest('[role=alert],[role=dialog],dialog,nxt-error-log-alert')&&
         e.querySelector('a[href],button,[role=button],[role=link]'))markShell(e);
     if(e.matches('a,button,[role=button],[role=link]')){
       let blocked=false;
       for(let n=e,depth=0;n&&depth++<128;n=composedParent(n))
         if(n.matches('[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert')){blocked=true;break;}
       if(!blocked)for(let n=composedParent(e),depth=0;n&&depth++<128;n=composedParent(n))
         if(n.matches('main,[role=main],nav,[role=navigation],header,[role=banner],[role=application]')&&!paintBlock(n)){markShell(n);break;}
     }
   }
   // Cursor traversal bounds memory as well as work.
   if(e.nextElementSibling)stack.push(e.nextElementSibling);
   if(e.firstElementChild)stack.push(e.firstElementChild);
   if(e.shadowRoot?.firstElementChild)stack.push(e.shadowRoot.firstElementChild);
 }
 const limited=stack.length>0;
 return {...result,shell:result.shell?true:limited?null:false,shell_landmarks:[...shellLandmarks].sort(),
   scan_limited:limited,nodes_inspected:Math.min(inspected,limit),pending_nodes:stack.length,
   unknown_reason:!result.shell&&limited?'READINESS_SCAN_INCOMPLETE':null,ready_state:document.readyState};
})()'''

POSITIVE_DOM = r'''(settings => {''' + DOCUMENT_HELPERS + OPEN_ROOT_HELPERS + r'''
 const issues=[];
 try{
   const dashboardDoc=scopedRoot(settings.dashboard_frame,settings.dashboard_shadow),tenantDoc=scopedRoot(settings.tenant_frame,settings.tenant_shadow);
   const unique=(doc,s)=>doc&&doc.querySelectorAll(s).length===1?doc.querySelector(s):null;
   const dashboard=unique(dashboardDoc,settings.dashboard_selector),tenant=unique(tenantDoc,settings.tenant_selector);
   const text=(tenant?.textContent||"").trim();
   const transient=!!composedClosest(dashboard,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert');
   const dashboardVisible=(visible(dashboard)||contentsShellVisible(dashboard))&&!transient;
   if(!dashboardVisible)issues.push("MISSING_DASHBOARD_INDICATOR");
   if(!visible(tenant))issues.push("MISSING_TENANT_INDICATOR");
   if(visible(tenant)&&text!==settings.account)issues.push("TENANT_MISMATCH");
   if(inventory.permitted_frames_unavailable||inventory.document_limit_reached||coverage.node_limit_reached||coverage.shadow_limit_reached||coverage.shadow_roots_uninspected)issues.push("PERMITTED_DOCUMENT_UNOBSERVABLE");
   if(coverage.inaccessible_or_unknown_hosts)issues.push("CUSTOM_HOST_SCOPE_REQUIRES_PROTECTED_INVENTORY");
   return {dashboard:dashboardVisible,tenant_matches:visible(tenant)&&text===settings.account,
     mismatch:visible(tenant)&&text!==settings.account,issues,_scope_diagnostics:{...coverage,
       permitted_frames_unavailable:inventory.permitted_frames_unavailable,document_limit_reached:inventory.document_limit_reached}};
 }catch{return {dashboard:false,tenant_matches:false,mismatch:false,issues:["INVALID_SELECTOR"]};}
})(SETTINGS)'''
