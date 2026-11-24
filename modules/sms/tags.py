"""Read-only inspection of the Add tags region proven by the SMS Setup scope."""

import json

from browser.observed_control import observed_node
from modules.whatsapp.contract import V2Stop


TAG_REGION = r'''(rootSelector => {
 const root=document.querySelector(rootSelector);
 if(!root) return {anchor_count:0, controls:[], region:null};
 const norm=v=>String(v??'').replace(/\s+/g,' ').trim().replace(/[\s:*]+$/g,'').toLowerCase();
 const visible=n=>!!n&&n.getClientRects().length>0&&getComputedStyle(n).visibility==='visible'&&!n.closest('[aria-hidden=true],[inert]');
 const labelText=l=>[...l.childNodes].filter(n=>!n.matches?.('input,select,textarea,button')).map(n=>n.textContent).join(' ');
 const label=n=>norm(n.getAttribute('aria-label')||n.getAttribute('placeholder')||
  [...(n.labels||[])].map(labelText).join(' ')||n.getAttribute('title')||
  (n.matches('label,mat-label')?labelText(n):n.innerText));
 const action='input,select,button,[role=combobox],[role=button],[role=textbox],[contenteditable=true],[tabindex="0"]';
 const leads=[...root.querySelectorAll('label,mat-label,span,div,p,'+action)].filter(n=>visible(n)&&label(n)==='add tags');
 const leaf=leads.filter(n=>!leads.some(m=>m!==n&&n.contains(m)));
 const selector=n=>{
   const parts=[];
   for(let el=n;el&&el!==document.body;el=el.parentElement){
     const siblings=[...el.parentElement.children].filter(m=>m.localName===el.localName);
     parts.unshift(CSS.escape(el.localName)+':nth-of-type('+(siblings.indexOf(el)+1)+')');
   }
   return 'body > '+parts.join(' > ');
 };
 if(leaf.length!==1)return {anchor_count:leaf.length,controls:[],region:null};
 const marker=leaf[0];
 const tags=new Set(['label','mat-label','span','div','input','button','select','mat-form-field','mat-select',
    'mat-chip-list','mat-chip-grid','mat-chip-listbox','ng-select','tag-input','ul','li']);
 const role=new Set(['combobox','button','textbox','listbox','option','searchbox','group','none','presentation']);
 const tag=n=>tags.has(n.localName)?n.localName:n.localName.includes('-')?'custom_element':'other';
 const describe=n=>({tag:tag(n),role:role.has(n.getAttribute('role'))?n.getAttribute('role'):null,
     type:['text','search','button','select-one','select-multiple'].includes(n.type)?n.type:null,
     disabled:n.matches(':disabled')||!!n.closest('[aria-disabled=true]'), readonly:n.readOnly===true,
     offers_dropdown:n.getAttribute('aria-haspopup')==='listbox'||n.getAttribute('aria-haspopup')==='true',
     owns_popup:!!(n.getAttribute('aria-controls')||n.getAttribute('aria-owns')),
     exact_label:label(n)==='add tags'});
 const nearby='mat-select,mat-chip-grid,mat-chip-list,mat-chip-listbox,ng-select,tag-input,[aria-haspopup],[tabindex]';
 let region=marker, controls=[];
 const nearby_controls=[];
 for(let depth=0,n=marker;n&&n!==root&&root.contains(n)&&depth<6;depth++,n=n.parentElement){
   const candidates=[n,...n.querySelectorAll(action+','+nearby)]
     .filter(m=>m.matches(action+','+nearby)&&visible(m));
   if(candidates.some(m=>['campaign name','next step'].includes(label(m)))||candidates.length>6)break;
   const actual=candidates.filter(m=>m.matches(action));
   if(!controls.length&&actual.length){region=n;controls=actual;}
   if(candidates.length)nearby_controls.push({ancestor_depth:depth,tag:tag(n),controls:candidates.map(describe)});
 }
 return {anchor_count:1, marker_tag:tag(marker), container_tag:tag(region), region:selector(region),
   controls:controls.map(describe), nearby_controls};
})(ROOT_SELECTOR)'''


async def inspect_tag_region(executor):
    facts, doc = await executor.snapshot()
    root = facts['observation_anchor']
    if executor.stage != 'setup' or not facts['stage_visible'] or not facts['scope_available'] or not root:
        return {'complete': False, 'reason': 'SMS_SETUP_SCOPE_UNVERIFIED', 'controls': []}, None
    browser = executor.browser
    root_identity = await observed_node(browser, root)
    candidate = facts.get('tag_candidate') or {}
    candidate_ids = {selector: await observed_node(browser, selector) for selector in candidate.values() if selector}
    dom = await browser.page.evaluate(TAG_REGION.replace('ROOT_SELECTOR', json.dumps(root)))
    region = dom.pop('region')
    identity = await observed_node(browser, region) if region else root_identity
    tree = await browser.guard.send('Accessibility.queryAXTree', {'backendNodeId': identity[-1]}, identity[1])
    if (await executor.protect() != doc or await observed_node(browser, root) != root_identity
            or region and await observed_node(browser, region) != identity):
        raise V2Stop('SMS_TAG_EVIDENCE_CHANGED', owner='browser', unsafe=True)
    binding_checks = dict(facts.get('tag_checks') or {})
    if candidate_ids:
        current_ids = {selector: await observed_node(browser, selector) for selector in candidate_ids}
        binding_checks['target_fresh'] = current_ids == candidate_ids
        if not binding_checks['target_fresh']:
            raise V2Stop('SMS_TAG_EVIDENCE_CHANGED', owner='browser', unsafe=True)
    allowed = {'combobox', 'button', 'textbox', 'listbox', 'option', 'searchbox', 'StaticText', 'LabelText', 'generic'}
    roles = {node.get('role', {}).get('value') for node in tree.get('nodes', ()) if not node.get('ignored')
             and str(node.get('name', {}).get('value', '')).strip().lower() == 'add tags'}
    tag = facts['fields'].get('tag', {})
    binding = tag.get('widget') if tag.get('enabled') else None
    return {'complete': bool(region), **dom, 'exact_label_ax_roles': sorted(roles & allowed),
            'binding_checks': binding_checks,
            'binding_kind': binding, 'native_binding_found': binding == 'native_select',
            'owned_exact_option_visible': bool(tag.get('option', {}).get('enabled')),
            'selected_tag_matches_payload': tag.get('matched') is True}, region
