"""Actual-label inspection only. No site selectors or arbitrary model JavaScript."""
SCAN = r'''(settings => {
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility!=="hidden";
 const norm=s=>(s||"").replace(/\s+/g," ").trim();
 const labelText=s=>norm(s).replace(/[\s*:]+$/g,"").toLowerCase();
 const label=e=>norm(e.getAttribute("aria-label")||[...(e.labels||[])].map(l=>l.textContent).join(" ")||
   (e.getAttribute("aria-labelledby")||"").split(/\s+/).map(id=>document.getElementById(id)?.textContent||"").join(" "));
 const safeControl=e=>e&&e.tagName!=="TEXTAREA"&&
   !["submit","button","password","hidden","checkbox","radio","file"].includes(e.type)&&
   (e.tagName==="SELECT"||e.tagName==="INPUT"||e.getAttribute("role")==="combobox");
 const all=[...document.querySelectorAll("input,select,[role=combobox]")].slice(0,500);
 const fields={};const found=[];
 for(const [key,wanted] of Object.entries(settings.labels)){
   let candidates=all.filter(e=>safeControl(e)&&labelText(label(e))===labelText(wanted));
   if(!candidates.length){
     for(const l of [...document.querySelectorAll("label")].slice(0,500)){
       if(labelText(l.textContent)!==labelText(wanted))continue;
       if(safeControl(l.control))candidates.push(l.control);
       else{
         let parent=l.parentElement;
         for(let depth=0;parent&&depth<2;depth++,parent=parent.parentElement){
           let matches=[...parent.querySelectorAll("input,select,[role=combobox]")].filter(safeControl);
           const native=matches.filter(e=>e.tagName==="SELECT");if(native.length===1)matches=native;
           if(matches.length===1){candidates.push(matches[0]);break;}
         }
       }
     }
   }
   // A native select with a visible labelled wrapper is a real inspected control,
   // even when its UI proxy hides the select. Its actual option values remain authoritative.
   candidates=[...new Set(candidates)].filter(e=>visible(e)||e.tagName==="SELECT"&&visible(e.parentElement));
   const native=candidates.filter(e=>e.tagName==="SELECT");if(native.length===1)candidates=native;
   if(candidates.length!==1){fields[key]={discovered:false,ambiguous:candidates.length>1};continue;}
   const e=candidates[0];found.push(e);
   const token=settings.prefix+"-"+key;e.setAttribute("data-netcore-setup",token);
   const selector="[data-netcore-setup="+JSON.stringify(token)+"]";
   const desired=settings.values[key];
   const optionMatches=o=>{
     if(o.disabled||o.getAttribute("data-select2-tag")==="true"||o.getAttribute("data-create")==="true")return false;
     if(e.tagName==="SELECT"&&!o.value)return false;
     if(key==="tags"&&e.tagName!=="SELECT"&&o.getAttribute("data-existing")!=="true")return false;
     if(key==="waba")return o.value===desired||o.getAttribute("data-waba-id")===desired;
     return norm(o.textContent)===desired;
   };
   let options=[],selected=false,value=null,kind=e.tagName==="SELECT"?"NATIVE_SELECT":key==="campaign_name"?"TEXT_INPUT":"OWNED_COMBOBOX";
   let open_selector=null,search_selector=null,expanded=false;
   if(kind==="NATIVE_SELECT"){
     options=[...e.options].slice(0,2000).filter(optionMatches);
     selected=[...e.selectedOptions].some(optionMatches);
     if(key!=="tags"&&e.selectedOptions.length!==1)selected=false;
     if(key==="tags"&&[...e.selectedOptions].some(o=>o.value&&!optionMatches(o)))selected=false;
     if(options.length===1)value=options[0].value;
   }else if(kind==="TEXT_INPUT")selected=e.value===desired;
   else{
     const ids=(e.getAttribute("aria-controls")||e.getAttribute("aria-owns")||"").split(/\s+/).filter(Boolean);
     const roots=ids.map(id=>document.getElementById(id)).filter(Boolean);
     expanded=e.getAttribute("aria-expanded")==="true"||roots.some(visible);
     const choices=roots.flatMap(root=>[...root.querySelectorAll("[role=option]")]).filter(visible);
     options=choices.filter(optionMatches);
     const chosen=options.length===1?options[0]:null;
     if(chosen){
       const otoken=token+"-option";chosen.setAttribute("data-netcore-setup",otoken);
       value="[data-netcore-setup="+JSON.stringify(otoken)+"]";
       selected=chosen.getAttribute("aria-selected")==="true"&&
         (key==="waba"?(e.value===desired||e.getAttribute("data-waba-id")===desired):norm(e.value||e.textContent)===desired);
     }
     if(visible(e)&&e.getAttribute("role")==="combobox")open_selector=selector;
     const searches=roots.flatMap(root=>[...root.querySelectorAll("input[type=search],[role=searchbox]")]).filter(visible);
     if(searches.length===1){searches[0].setAttribute("data-netcore-setup",token+"-search");search_selector="[data-netcore-setup="+JSON.stringify(token+"-search")+"]";}
   }
   fields[key]={discovered:true,selector,kind,selected,exact_option_count:options.length,value,
     open_selector,search_selector,expanded,multiple:!!e.multiple,disabled:!!e.disabled||!!e.readOnly||e.getAttribute("aria-disabled")==="true",
     has_other_tags:key==="tags"&&e.tagName==="SELECT"&&[...e.selectedOptions].some(o=>o.value&&!optionMatches(o))};
 }
 const forms=found.map(e=>e.closest("form,[role=form]"));
 const same_form=found.length===4&&!!forms[0]&&forms.every(f=>f===forms[0]);
 const text=(document.body?.innerText||"").slice(0,200000);
 const stepNodes=[...document.querySelectorAll("h1,h2,h3,h4,[role=heading],[aria-current],[aria-selected],li,button,a")].filter(visible);
 const active=e=>[e,e.parentElement].filter(Boolean).some(n=>["step","page","true"].includes(n.getAttribute("aria-current"))||n.getAttribute("aria-selected")==="true"||[...n.classList].some(c=>/(^|[-_])(active|current|selected)([-_]|$)/i.test(c)));
 const setup_active=stepNodes.some(e=>labelText(e.textContent)==="setup"&&(active(e)||e.matches("h1,h2,h3,h4,[role=heading]")))&&
   !stepNodes.some(e=>["audience","content","schedule"].includes(labelText(e.textContent))&&active(e));
 const page_identity=/whatsapp/i.test(text)&&same_form;
 return {fields,same_form,setup_active,page_identity,forbidden_attempts:window.__netcoreSetupGuard?.attempts??null,
   guard_installed:!!window.__netcoreSetupGuard};
})(SETTINGS)'''

INSTALL_GUARD = r'''(() => {
 if(window.__netcoreSetupGuard)return true;
 const state={attempts:0};Object.defineProperty(window,"__netcoreSetupGuard",{value:state,writable:false,configurable:false});
 const deny=e=>{state.attempts++;e.preventDefault();e.stopImmediatePropagation();};
 document.addEventListener("submit",deny,true);
 document.addEventListener("keydown",e=>{if(e.key==="Enter"&&e.target.closest("form,[role=form]"))deny(e);},true);
 document.addEventListener("click",e=>{
   const target=e.target.closest("button,a,[role=button],[role=tab],input[type=submit]");
   const label=(target?.textContent||target?.value||target?.getAttribute("aria-label")||"").replace(/\s+/g," ").trim();
   if(target&&(/\b(next step|finish later|save|schedule|publish|activate|send)\b/i.test(label)||/^(audience|content|schedule)$/i.test(label)||target.type==="submit"))deny(e);
 },true);
 return true;
})()'''
