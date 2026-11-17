"""Passive, bounded DOM inventory. Candidates never establish authentication."""

DOCUMENT_HELPERS = r'''
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=="hidden";
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
   for(let e=element;e;){
     const match=e.closest?.(query);if(match)return match;
     e=e.getRootNode?.().host||null;
   }
   return null;
 };
"""

DOM_DISCOVERY = r'''(settings => {''' + DOCUMENT_HELPERS + OPEN_ROOT_HELPERS + r'''
 const dashboard_candidates=[],navigation_candidates=[],tenant_candidates=[];
 let node_limit_reached=false,shadow_roots_uninspected=0;
 for(const {doc,path,shadow,nodes} of scopes){
   for(const e of nodes){
     if(!visible(e))continue;
     const text=(e.textContent||"").trim();
     if(!text)continue;
     const shell=e.matches('main,[role=main],[role=application]')&&
       !!e.querySelector('a[href],button,[role=button],[role=link]');
     if(text.length>256&&!shell)continue;
     const heading=e.matches("h1,h2,h3,h4,h5,h6,[role=heading]");
     const transient=!!composedClosest(e,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert');
     if(transient)continue;
     const navigation=!!composedClosest(e,"nav,[role=navigation],header,[role=banner]");
     const active=e.matches("[aria-current=page]");
     const s=selector(e,doc,settings.account);
     if(!s)continue;
     if(shell&&!navigation){
       dashboard_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,
         kind:'APPLICATION_SHELL',confidence:'OPERATOR_REVIEW_REQUIRED'});
     }else if(heading&&!navigation){
       const content=!!composedClosest(e,"main,[role=main],article,[role=region]");
       dashboard_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,kind:content?"CONTENT_HEADING":"UNSCOPED_HEADING",
         confidence:content?"OPERATOR_REVIEW_REQUIRED":"LIMITED_NO_CONTENT_LANDMARK"});
     }else if((heading&&navigation)||active){
       navigation_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,kind:"NAVIGATION_ONLY",confidence:"NOT_DASHBOARD_EVIDENCE"});
     }
     if(text===settings.account&&!composedClosest(e,"form")&&!e.matches("input,textarea,script,style")&&
         ![...e.children].some(c=>(c.textContent||"").trim()===settings.account)){
       tenant_candidates.push({selector:s,frame_selectors:path,shadow_selectors:shadow,kind:"EXACT_EXPECTED_ACCOUNT_TEXT",confidence:"EXACT_MATCH_REQUIRES_REVIEW"});
     }
   }
 }
 return {dashboard_candidates:dashboard_candidates.slice(0,32),navigation_candidates:navigation_candidates.slice(0,32),
   tenant_candidates:tenant_candidates.slice(0,32),
   dashboard_selectors:dashboard_candidates.filter(c=>!c.frame_selectors.length).map(c=>c.selector),
   tenant_selectors:tenant_candidates.filter(c=>!c.frame_selectors.length).map(c=>c.selector),
   documents_inspected:inventory.documents.length,permitted_frames_unavailable:inventory.permitted_frames_unavailable,
   external_frames_skipped:inventory.external_frames_skipped,document_limit_reached:inventory.document_limit_reached,
   ...coverage};
})(SETTINGS)'''

from modules.login.negative_scan import NEGATIVE_STATES

# Lightweight semantic hints only. Readiness never supplies authentication gates.
# Traverse at most 512 nodes, including accessible roots, rather than inventorying
# the entire DOM on every poll. The full protected scan runs after readiness.
READINESS_DOM = r'''(() => {
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=='hidden';
 const result={shell:false,loading:document.readyState==='loading',credential_form:false,
   challenge:false,rejected:false,expired:false,account_restricted:false};
 const stack=[document.documentElement];let inspected=0;
 while(stack.length&&inspected++<512){
   const e=stack.pop();if(!e)continue;
   if(visible(e)){
     result.credential_form ||= e.matches('input[type=password]');
     result.challenge ||= e.matches('iframe[src*=captcha],input[autocomplete=one-time-code]');
     result.loading ||= e.matches('[aria-busy=true],[role=progressbar]');
     if(e.matches('[role=alert],.alert,.error')){
       const text=(e.textContent||'').slice(0,4096).toLowerCase();
       result.rejected ||= /invalid (credentials|password|username)|incorrect (password|credentials)|login failed|authentication failed/.test(text);
       result.expired ||= /session (has )?expired|session timed out/.test(text);
       result.account_restricted ||= /account.{0,30}(locked|disabled|suspended|restricted)|access denied|permission denied|not authorized|too many (login|sign.?in) attempts/.test(text);
     }
     if(e.matches('main,[role=main],nav,[role=navigation],header,[role=banner]')&&
         !e.closest('[role=alert],[role=dialog],dialog,nxt-error-log-alert')&&
         e.querySelector('a[href],button,[role=button],[role=link]'))result.shell=true;
   }
   // Cursor traversal bounds memory as well as work.
   if(e.nextElementSibling)stack.push(e.nextElementSibling);
   if(e.firstElementChild)stack.push(e.firstElementChild);
   if(e.shadowRoot?.firstElementChild)stack.push(e.shadowRoot.firstElementChild);
 }
 const limited=stack.length>0;
 return {...result,shell:result.shell?true:limited?null:false,
   scan_limited:limited,ready_state:document.readyState};
})()'''

POSITIVE_DOM = r'''(settings => {''' + DOCUMENT_HELPERS + OPEN_ROOT_HELPERS + r'''
 const issues=[];
 try{
   const dashboardDoc=scopedRoot(settings.dashboard_frame,settings.dashboard_shadow),tenantDoc=scopedRoot(settings.tenant_frame,settings.tenant_shadow);
   const unique=(doc,s)=>doc&&doc.querySelectorAll(s).length===1?doc.querySelector(s):null;
   const dashboard=unique(dashboardDoc,settings.dashboard_selector),tenant=unique(tenantDoc,settings.tenant_selector);
   const text=(tenant?.textContent||"").trim();
   const transient=!!composedClosest(dashboard,'[role=alert],[role=alertdialog],[role=dialog],dialog,[aria-hidden=true],[inert],nxt-error-log-alert');
   const dashboardVisible=visible(dashboard)&&!transient;
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
