"""Bounded, read-only negative predicates over documents and accessible open roots.

Only fixed flags/counts leave JavaScript. Closed-root absence needs the separate
protected CDP inventory; page JavaScript cannot establish it.
"""

NEGATIVE_SCAN = r'''(settings => {
 const flags={credential_form:false,challenge:false,rejected:false,expired:false,account_restricted:false};
 const coverage={documents_inspected:0,nodes_inspected:0,permitted_frames_unavailable:0,external_frames_skipped:0,
   open_shadow_roots_inspected:0,shadow_roots_uninspected:0,document_limit_reached:false,node_limit_reached:false,
   shadow_root_limit_reached:false,shadow_depth_limit_reached:false,tree_depth_limit_reached:false,text_limit_reached:false};
 const seenDocs=new Set(),seenRoots=new Set();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=="hidden";
 const inspect=e=>{
   if(!visible(e))return;
   flags.credential_form ||= e.matches('input[type=password]');
   flags.challenge ||= e.matches('iframe[src*=captcha],input[autocomplete=one-time-code]');
   if(e.matches('[role=alert],.alert,.error')){
     const alerts=(e.textContent||'').toLowerCase();
     if(alerts.length>65536)coverage.text_limit_reached=true;
     const text=alerts.slice(0,65536);
     flags.rejected ||= /invalid (credentials|password|username)|incorrect (password|credentials)|login failed|authentication failed/.test(text);
     flags.expired ||= /session (has )?expired|session timed out/.test(text);
     flags.account_restricted ||= /account.{0,30}(locked|disabled|suspended|restricted)|access denied|permission denied|not authorized|too many (login|sign.?in) attempts/.test(text);
   }
 };
 const visitDocument=(doc,frameDepth)=>{
   if(seenDocs.has(doc))return;
   if(seenDocs.size>=8){coverage.document_limit_reached=true;return;}
   seenDocs.add(doc);coverage.documents_inspected++;
   let frames=0;
   // Cursor frames bound memory even for a very broad DOM. No all-node arrays.
   const stack=[{next:doc.documentElement||doc.body,shadowDepth:0,treeDepth:0}];
   while(stack.length){
     const cursor=stack[stack.length-1],e=cursor.next;
     if(!e){stack.pop();continue;}
     cursor.next=e.nextElementSibling;
     if(coverage.nodes_inspected>=12000){coverage.node_limit_reached=true;return;}
     coverage.nodes_inspected++;inspect(e);
     if(e.matches('iframe,frame')&&visible(e)){
       frames++;
       if(frames>32){coverage.document_limit_reached=true;continue;}
       try{
         let origin=new URL(e.getAttribute('src')||doc.URL,doc.URL).origin;
         if(origin==='null'&&(!e.getAttribute('src')||e.hasAttribute('srcdoc')))origin=new URL(doc.URL).origin;
         if(!settings.origins.includes(origin))coverage.external_frames_skipped++;
         else {
           const child=e.contentDocument;
           if(!child||frameDepth>=3||!settings.origins.includes(new URL(['about:blank','about:srcdoc'].includes(child.URL)?doc.URL:child.URL).origin))
             coverage.permitted_frames_unavailable++;
           else visitDocument(child,frameDepth+1);
         }
       }catch{coverage.permitted_frames_unavailable++;}
     }
     const root=e.shadowRoot;
     if(root&&!seenRoots.has(root)){
       seenRoots.add(root);
       if(coverage.open_shadow_roots_inspected>=64){coverage.shadow_root_limit_reached=true;coverage.shadow_roots_uninspected++;}
       else if(cursor.shadowDepth>=8){coverage.shadow_depth_limit_reached=true;coverage.shadow_roots_uninspected++;}
       else {coverage.open_shadow_roots_inspected++;stack.push({next:root.firstElementChild,shadowDepth:cursor.shadowDepth+1,treeDepth:0});}
     }
     if(e.firstElementChild){
       if(cursor.treeDepth>=128)coverage.tree_depth_limit_reached=true;
       else stack.push({next:e.firstElementChild,shadowDepth:cursor.shadowDepth,treeDepth:cursor.treeDepth+1});
     }
   }
 };
 visitDocument(document,0);
 flags.negative_scope_complete=!coverage.permitted_frames_unavailable&&!coverage.document_limit_reached&&
   !coverage.node_limit_reached&&!coverage.shadow_roots_uninspected&&!coverage.tree_depth_limit_reached&&!coverage.text_limit_reached;
 return {flags,coverage};
})'''

NEGATIVE_SNAPSHOT = NEGATIVE_SCAN + '(SETTINGS)'
NEGATIVE_STATES = NEGATIVE_SCAN + '(SETTINGS).flags'
