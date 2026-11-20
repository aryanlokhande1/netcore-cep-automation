import asyncio
import json
from time import monotonic
from browser.observed_control import observed_node, protected_document
from modules.whatsapp.browser_baseline import reject_security_negatives
from modules.whatsapp.contract import SETUP_URL, PAGE1_SETUP_FIELDS, STAGE_FIELDS, V2Stop
from modules.whatsapp.destination import cdp_frame_url, campaign_step_route, classify_setup_page
from modules.whatsapp.observation import V2_LABEL_HELPERS
from modules.whatsapp.observer import deterministic_readback


def audience_route(url, expected=SETUP_URL):
    return campaign_step_route(url, 'audience', expected)


CAMPAIGN_DOM = r'''
 const norm=x=>String(x??'').replace(/\s+/g,' ').trim().toLowerCase();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility==='visible'&&
   !e.closest('[aria-hidden=true],[inert]');
 const one=q=>{if(!q)return null;const rows=document.querySelectorAll(q);return rows.length===1?rows[0]:null};
 const actionLabel=e=>norm(e.innerText||e.value||e.getAttribute('aria-label'));
 const actionable=e=>visible(e)&&!e.disabled&&!e.matches(':disabled')&&
   !e.closest('[aria-disabled=true],[inert]')&&!e.matches('a[href]');
 const actionKind='button,[role=button],input[type=submit]';
 const active=[...document.querySelectorAll('[aria-current="step"],[aria-selected="true"],[role="tab"].active,.mat-step-header[aria-selected=true]')]
   .filter(visible).map(e=>norm(e.getAttribute('aria-label')||e.innerText));
 const stageNames=['setup','audience','content','schedule'];
 const namedSteps=stageNames.filter(step=>active.some(text=>text===step||/^[1-4][ .]/.test(text)&&text.replace(/^[1-4][ .]+/,'')===step));
 const lookupStage=(step,fragment)=>{
   const activeOk=namedSteps.length===1?namedSteps[0]===step:
     namedSteps.length===0&&fragment===step;
   if(!activeOk)return null;
   const labels=[...document.querySelectorAll(controlSelector+',label,mat-label,legend,[role=heading],h2,h3,h4')]
     .filter(visible).map(e=>norm(fieldLabel(e,'dom')||e.innerText));
   const match=names=>labels.some(label=>names.includes(label.replace(/[:*]+$/g,'').trim()));
   if(step==='setup')return match(['campaign name','campaign title'])?'CAMPAIGN_NAME':null;
   if(step==='audience')return match(['lists/segments','lists / segments'])?'LISTS_SEGMENTS':null;
   if(step==='content')return match(['template','select template','choose template','whatsapp template','select whatsapp template'])?
     'TEMPLATE_SELECTOR':null;
   return match(['send later','schedule date','schedule time','schedule date and time','frequency cap','contact limit'])?
     'SCHEDULE_CONTROL':null;
 };
 const headerBinding=scope=>{
   const actions=[...document.querySelectorAll(actionKind)].filter(visible);
   const next=actions.filter(e=>actionLabel(e)==='next step');
   const finish=actions.filter(e=>actionLabel(e)==='finish later');
   const result={visible_next:next.length,visible_finish_later:finish.length,
     next_inside_field_scope:next.length===1&&scope.contains(next[0]),header_pair:false,enabled:false,target:null};
   if(next.length!==1||finish.length!==1)return result;
   const target=next[0],neighbor=finish[0];
   const parents=[];
   for(let n=target.parentElement;n&&n!==document.body&&parents.length<5;n=n.parentElement)parents.push(n);
   result.header_pair=parents.some(n=>n.contains(neighbor)&&!n.contains(scope));
   result.enabled=actionable(target);
   if(result.enabled&&(result.header_pair||result.next_inside_field_scope))result.target=target;
   return result;
 };
 const cssPath=e=>{
   if(e.id){const q='#'+CSS.escape(e.id);if(one(q)===e)return q;}
   const parts=[];
   for(let n=e;n&&parts.length<32;n=n.parentElement){
     const same=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
     parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(same.indexOf(n)+1)+')');
     const q=parts.join(' > ');
     if(one(q)===e)return q;
   }
   return null;
 };
 const validation=()=>{
   const errors=[...document.querySelectorAll('[role=alert],[aria-invalid=true],.mat-error,.mat-mdc-form-field-error')].filter(visible);
   return [...new Set(errors.map(e=>{
     const t=norm(e.innerText);
     if(/\brequired\b/.test(t))return 'REQUIRED_FIELD';
     if(/\bduplicate\b|already exists/.test(t))return 'DUPLICATE_VALUE';
     if(/\binvalid\b/.test(t))return 'INVALID_VALUE';
     return 'VISIBLE_VALIDATION_INDICATOR';
   }))];
 };
'''

CAMPAIGN_SURFACE = r'''(s=>{''' + V2_LABEL_HELPERS + CAMPAIGN_DOM + r'''
 return {active_steps:namedSteps, control:lookupStage(s.stage,location.hash.slice(1)),validation:validation()};
})(SETTINGS)'''

CAMPAIGN_HEADER = r'''(s=>{''' + V2_LABEL_HELPERS + CAMPAIGN_DOM + r'''
 const scope=one(s.scope);
 if(!visible(scope))return {status:'ACTIVE_STAGE_SCOPE_NOT_VISIBLE'};
 const surface=lookupStage(s.stage,location.hash.slice(1));
 if(!surface)return {status:'ACTIVE_STAGE_CONTROL_NOT_VISIBLE',active_steps:namedSteps};
 const {target,...proof}=headerBinding(scope);
 const selector=target?cssPath(target):null;
 if(!s.click)return {status:selector?'BOUND':'HEADER_CONTROL_NOT_UNIQUE_OR_ENABLED',selector,...proof};
 if(location.href!==s.url||!window.__netcoreV2Guard||!selector||selector!==s.target)return false;
 const grant={control:target,clicked:false,submitted:false};
 window.__netcoreV2Page1Next=grant;
 try{target.click();return grant.clicked;}finally{delete window.__netcoreV2Page1Next;}
})(SETTINGS)'''


async def verify_active_campaign_stage(tools, stage):
    document = await tools.protect()
    facts = await tools.browser.page.evaluate(CAMPAIGN_SURFACE.replace('SETTINGS', json.dumps({'stage': stage})))
    if not facts.get('control') or await tools.protect() != document:
        raise V2Stop('CAMPAIGN_ACTIVE_STAGE_NOT_VERIFIED', owner='recognition')
    return facts


async def campaign_content_requirements(tools):
    document = await tools.protect()
    settings = {'scope': tools.ui.stage_selectors.get('content')}
    requirements = await tools.browser.page.evaluate(
        (r'''(s=>{''' + V2_LABEL_HELPERS + CAMPAIGN_DOM + r'''
          const scope=one(s.scope);
          if(!visible(scope))return null;
          return [...scope.querySelectorAll('input,textarea,[contenteditable=true]')].filter(e=>
            visible(e)&&!e.disabled&&!e.matches('input[type=hidden],input[type=radio],input[type=checkbox],input[type=password],input[type=search]')&&
            (e.required||e.getAttribute('aria-required')==='true')&&!String(e.value??e.innerText??'').trim()
          ).map((e,i)=>{
            const label=(fieldLabel(e,'dom')||'').trim();
            return /^[a-z0-9 _{}*.:#()-]{1,80}$/i.test(label)&& /\b(variable|parameter|placeholder|header|body|button)\b/i.test(label)?
              label:'required template input '+(i+1);
          });
        })(SETTINGS)''').replace('SETTINGS', json.dumps(settings)))
    if requirements is None or await tools.protect() != document:
        raise V2Stop('CONTENT_REQUIRED_INPUTS_NOT_OBSERVABLE', owner='recognition')
    return requirements


async def advance_setup_to_audience(tools, mark_attempt):
    return await advance_campaign_step(tools, 'setup', 'audience', mark_attempt)


async def advance_campaign_step(tools, stage, destination, mark_attempt):
    if (stage, destination) not in {('setup','audience'), ('audience','content'), ('content','schedule')}:
        raise V2Stop('UNSUPPORTED_CAMPAIGN_TRANSITION', owner='authorization')
    browser = tools.browser
    if (tools.stage != stage or browser.setup_binding is not tools.operator_setup_binding or
            tools.operator_approved_payload is not tools.payload):
        raise V2Stop('PAGE1_TRANSITION_APPROVAL_BINDING_CHANGED', owner='authorization', unsafe=True)

    document = await tools.protect()
    form = await tools.observe()
    fields = PAGE1_SETUP_FIELDS if stage == 'setup' else STAGE_FIELDS[stage]
    facts = await deterministic_readback(tools, fields=fields)
    if (form.get('stage_visible') is not True or facts.get('stage_visible') is not True or
            not all(facts.get('fields', {}).get(field) is True for field in fields) or
            stage == 'content' and not facts.get('checks', {}).get('template_preview_visible')):
        raise V2Stop('PAGE1_ALL_FIELDS_READBACK_REQUIRED', owner='recognition')

    if stage == 'content' and await campaign_content_requirements(tools):
        raise V2Stop('CONTENT_TEMPLATE_VARIABLE_INPUTS_REQUIRED', owner='recognition')
    scope = tools.ui.stage_selectors.get(stage)
    settings = {'scope': scope, 'stage': stage, 'click': False}
    proof = await browser.page.evaluate(CAMPAIGN_HEADER.replace('SETTINGS', json.dumps(settings)))
    selector = proof.pop('selector', None)
    browser.trace.agent_event('campaign_header_binding', role='observer', source_step=stage, **proof)
    if not selector:
        raise V2Stop('PAGE1_NEXT_STEP_CONTROL_NOT_BOUND', owner='recognition')

    control = await observed_node(browser, selector)
    scope_id = await observed_node(browser, scope)
    await tools.install_guard()
    if (await tools.protect() != document or await observed_node(browser, selector) != control
            or await observed_node(browser, scope) != scope_id):
        raise V2Stop('PAGE1_NEXT_STEP_TARGET_CHANGED', owner='browser', unsafe=True)
    url = await browser.page.url()
    details = {'source_step': stage, 'destination_step': destination, 'operator_approved': True,
               'click_attempted': False, 'persistence_verified': False}
    mark_attempt()
    browser.trace.agent_event('page1_next_step_started', role='controller', **details)
    settings.update(click=True, target=selector, url=url)
    try:
        clicked = await browser.page.evaluate(CAMPAIGN_HEADER.replace('SETTINGS', json.dumps(settings)))
        clicked = clicked is True
    except Exception:
        clicked = None
    details.update(click_attempted=clicked is not False, click_result='DISPATCHED' if clicked is True
                   else 'BLOCKED' if clicked is False else 'UNCERTAIN')
    if clicked is False:
        details['status'] = 'PAGE1_NEXT_STEP_CONTROL_NOT_ACTIONABLE'
        browser.trace.agent_event('page1_transition_unresolved', role='observer', **details)
        return details

    deadline = monotonic() + 12
    while monotonic() < deadline:
        try:
            stop = tools.operator_setup_stop() or tools.operator_login_binding.stop_code
            if stop:
                details['status'] = stop
                break
            before = protected_document(browser)
            active = await browser.browser.context.active_page()
            if (browser.page is not browser.setup_page or not active or active.page_id != browser.page.page_id
                    or before != document):
                details['status'] = 'CAMPAIGN_TRANSITION_BOUND_DOCUMENT_CHANGED'
                break
            current = await browser.page.url()
            tree = await browser.guard.send('Page.getFrameTree', session=before[1])
            frame = tree.get('frameTree', {}).get('frame', {})
            if (current != cdp_frame_url(frame) or frame.get('id') != before[4]
                    or (frame.get('loaderId') or None) != before[5] or protected_document(browser) != before):
                await asyncio.sleep(.25)
                continue
            await reject_security_negatives(browser)
            observed = await browser.page.evaluate(CAMPAIGN_SURFACE.replace('SETTINGS', json.dumps({'stage': destination})))
            if protected_document(browser) != before or await browser.page.url() != current:
                continue
            route_match = campaign_step_route(current, destination)
            details.update(active_steps=observed['active_steps'], destination_route=route_match,
                           destination_control=observed['control'], setup_validation=observed['validation'])
            if observed['control'] and (route_match or classify_setup_page(current)['route_match']):
                details['status'] = destination.upper() + '_VISIBLE'
                if destination == 'audience':
                    details.update(audience_route=route_match, audience_control=observed['control'])
                browser.trace.agent_event('page1_' + destination + '_observed', role='observer', **details)
                return details
        except (ValueError, RuntimeError, PermissionError) as error:
            details['status'] = getattr(error, 'code', 'PAGE1_DESTINATION_OBSERVATION_UNAVAILABLE')
            break
        await asyncio.sleep(.35)
    details.setdefault('status', 'PAGE1_SETUP_VALIDATION_VISIBLE' if details.get('setup_validation')
                       else 'PAGE1_DESTINATION_UNCONFIRMED_NO_RETRY')
    browser.trace.agent_event('page1_transition_unresolved', role='observer', **details)
    return details
