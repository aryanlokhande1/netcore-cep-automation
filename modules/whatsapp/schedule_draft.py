"""Bounded Schedule picker and separately approved draft preview."""
import asyncio
import calendar
import json
import re
from datetime import datetime

from browser.observed_control import observed_node, stagehand_locator
from modules.whatsapp.contract import V2Stop
from modules.whatsapp.destination import campaign_step_route, cdp_frame_url
from modules.whatsapp.page1_transition import CAMPAIGN_DOM, verify_active_campaign_stage
from modules.whatsapp.observation import V2_LABEL_HELPERS


SCHEDULE_DOM = r"""
 const byText=(root,query,pattern)=>[...root.querySelectorAll(query)]
   .filter(e=>visible(e)&&pattern.test(norm(e.innerText||e.value||e.getAttribute('aria-label'))));
 const exactDate=/^(?:[a-z]{3,9} \d{1,2}, \d{4}|\d{4}-\d\d-\d\d)[ ,T]+\d{1,2}:\d\d(?: [ap]m)?$/i;
 const dateLabel=/^select date and time\s*\*?$/i;
 const views=e=>[e.value,e.innerText,e.getAttribute('aria-label'),e.getAttribute('title')].filter(Boolean);
 const calendarAction=(root,name)=>[...root.querySelectorAll('button,[role=button],input[type=button]')]
   .filter(e=>actionable(e)&&(norm(e.innerText||e.value)===name||
      [...e.querySelectorAll('span')].some(n=>visible(n)&&norm(n.innerText)===name)));
 const dateTargets=root=>{
   const labels=byText(root,'label,mat-label,span,div,p',dateLabel);
   const tight=labels.filter(e=>!labels.some(other=>other!==e&&e.contains(other)));
   const targets=new Set();
   for(const label of tight){
     if(label.control&&visible(label.control))targets.add(label.control);
     for(let n=label.parentElement,depth=0;n&&n!==root&&depth<5;n=n.parentElement,depth++){
       const surface=[...n.querySelectorAll('input,button,[role=button],[role=combobox],[tabindex]')]
         .filter(e=>visible(e)&&!e.matches('input[type=hidden],input[type=password]')&&
           views(e).some(value=>exactDate.test(value.trim())||dateLabel.test(value.trim())));
       const leaves=surface.filter(e=>!surface.some(other=>other!==e&&e.contains(other)));
       if(leaves.length>1)break;
       if(leaves.length===1){targets.add(leaves[0]);break;}
       const dates=byText(n,'div,span',exactDate);
       const rendered=dates.filter(e=>!dates.some(other=>other!==e&&e.contains(other)));
       if(rendered.length===1){targets.add(rendered[0]);break;}
     }
   }
   return [...targets];
 };
 const menuButtons='button,[role=button],input[type=button],span,td,[role=option],[role=gridcell]';
 const caption=e=>e.tagName==='SELECT'?e.selectedOptions[0]?.textContent||'':
   [...e.querySelectorAll('.mat-mdc-select-value-text,.mat-select-value-text')].find(visible)?.innerText||
   e.innerText||e.getAttribute('aria-label')||'';
 const describe=n=>n?{selector:cssPath(n),tag:n.tagName,role:n.getAttribute('role'),expanded:n.getAttribute('aria-expanded')==='true'}:null;
 const chosen=e=>!!e&&(e.matches('[aria-selected=true],[aria-pressed=true],.active,.selected,.is-selected')||
   !!e.querySelector('[aria-selected=true],[aria-pressed=true],.active,.selected,.is-selected'));
 const selectLeaf=(nodes)=>nodes.filter(e=>!nodes.some(other=>other!==e&&e.contains(other)));
 const pickerRoots=()=>{
   const apply=calendarAction(document,'apply');
   if(apply.length!==1)return [];
   for(let e=apply[0].parentElement,depth=0;e&&e!==document.body&&depth<8;e=e.parentElement,depth++){
     if(calendarAction(e,'discard').length===1&&
        byText(e,menuButtons,/^(sun|su|mon|mo|tue|tu|wed|we|thu|th|fri|fr|sat|sa)$/i).length>=3)
        return [e];
   }
   return [];
 };
 const timedropdowns=popup=>{
   const candidates=[...popup.querySelectorAll('select,[role=combobox],button[aria-haspopup],[role=button][aria-haspopup]')]
     .filter(e=>visible(e)&&/^\d{1,2}$/.test(norm(caption(e))));
   const direct=(name)=>candidates.filter(e=>new RegExp(name,'i').test(fieldLabel(e,'dom')));
   const hours=direct('hour'),minutes=direct('minute');
   if(hours.length===1&&minutes.length===1&&hours[0]!==minutes[0])return [hours[0],minutes[0]];
   return candidates.length===2?candidates:[];
 };
 const optionsFor=(trigger,n)=> {
   const ids=((trigger.getAttribute('aria-controls')||'')+' '+(trigger.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
   const roots=ids.map(id=>document.getElementById(id)).filter(visible);
   const options=roots.flatMap(p=>[...p.querySelectorAll('[role=option],mat-option')]).filter(actionable);
   return options.filter(e=>/^\d{1,2}$/.test(norm(e.innerText))&&Number(norm(e.innerText))===n);
 };
"""


SCHEDULE_VIEW = r"""(s=>{""" + V2_LABEL_HELPERS + CAMPAIGN_DOM + SCHEDULE_DOM + r"""
 const schedule=lookupStage('schedule',location.hash.slice(1));
 if(!schedule)return {code:'SCHEDULE_STEP_NOT_VERIFIED'};
 const mode=[...document.querySelectorAll('input[type=radio],[role=radio]')].filter(e=>
   norm(fieldLabel(e,'dom'))==='send later');
 const radios=mode.filter(e=>e.type==='radio');
 const labels=radios.length===1?[...radios[0].labels].filter(e=>actionable(e)&&
   e.control===radios[0]&&norm(e.innerText)==='send later'):[];
 const modeTarget=labels.length===1?labels[0]:mode.length===1&&actionable(mode[0])?mode[0]:null;
 const formDate=dateTargets(document.body);
 const date=formDate.length===1?formDate[0]:null;
 const selectedMode=mode.length===1&&(mode[0].type==='radio'?mode[0].checked:mode[0].getAttribute('aria-checked')==='true');
 const tzLabels=byText(document,'label,mat-label,span,div,p',/^time[\s-]*zone\s*\*?$/i);
 const tz=[];
 for(const label of selectLeaf(tzLabels)){
   for(let n=label.parentElement,depth=0;n&&n!==document.body&&depth<5;n=n.parentElement,depth++){
     const controls=[...n.querySelectorAll('select,[role=combobox]')].filter(visible);
     if(controls.length!==1){if(controls.length>1)break;continue;}
     const e=controls[0];tz.push(e.tagName==='SELECT'?e.selectedOptions[0]?.textContent:e.innerText);break;
   }
 }
 const timezone=[...new Set(tz.filter(Boolean).map(value=>value.trim()))];
 const dateValue=date?views(date).find(value=>exactDate.test(value.trim())):null;
 const rows=[...document.querySelectorAll('mat-expansion-panel,aside,[role=region],section')]
   .filter(e=>visible(e)&&!e.contains(date)&&/when to send\s*:/.test(norm(e.innerText)));
 const summary=rows.length===0?[]:selectLeaf(rows).map(e=>e.innerText);
 const save=byText(document,'button,[role=button],input[type=submit]',/^save (and|&) preview$/i).filter(actionable);
 const preview=byText(document,'button,[role=button],input[type=submit]',/^preview$/i).filter(actionable);
 const roots=pickerRoots();
 const popup=roots.length===1?roots[0]:null;
 let widget=null;
 if(popup){
   const monthNames=s.months.map(x=>x.toLowerCase());
   const headerNodes=[...popup.querySelectorAll('select,[role=combobox],button,[role=button],span,div,mat-label')]
     .filter(e=>visible(e)&&!e.closest('[role=listbox],[role=grid]'));
   const findUnique=(predicate)=>{
     const matches=headerNodes.filter(e=>predicate(norm(caption(e))));
     return matches.filter(e=>!matches.some(other=>other!==e&&e.contains(other)));
   };
   const months=findUnique(value=>monthNames.includes(value));
   const years=findUnique(value=>/^\d{4}$/.test(value)&&Number(value)>=2000);
   const month=months.length===1?norm(caption(months[0])):null;
   const year=years.length===1?Number(norm(caption(years[0]))):null;
   const dayNodes=byText(popup,'td,[role=gridcell],button,[role=button]',new RegExp('^'+s.day+'$'));
   const days=selectLeaf(dayNodes).filter(e=>actionable(e)&&
     !e.closest('.old,.new,.outside,.outside-month,.other-month,[aria-disabled=true],.disabled'));
   const pair=timedropdowns(popup);
   const number=e=>e?norm(caption(e))||null:null;
   const navigation=(direction)=>[...popup.querySelectorAll('button,[role=button]')].filter(e=>actionable(e)&&
     new RegExp('^'+direction+'(?: month)?$','i').test(e.getAttribute('aria-label')||e.getAttribute('title')||''));
   const apply=calendarAction(popup,'apply');
   widget={month,year,month_candidates:months.length,year_candidates:years.length,
     visible_selects:headerNodes.filter(e=>e.matches('select,[role=combobox]')).length,
     visible_buttons:headerNodes.filter(e=>e.matches('button,[role=button]')).length,
     day_count:days.length,day_selected:days.length===1&&chosen(days[0]),
     day:days.length===1?describe(days[0]):null,apply:apply.length===1?describe(apply[0]):null,
     hour:describe(pair[0]),minute:describe(pair[1]),hour_value:number(pair[0]),minute_value:number(pair[1]),
     hour_option:pair[0]&&optionsFor(pair[0],s.hour).length===1?describe(optionsFor(pair[0],s.hour)[0]):null,
     minute_option:pair[1]&&optionsFor(pair[1],s.minute).length===1?describe(optionsFor(pair[1],s.minute)[0]):null,
     next:navigation('next').length===1?describe(navigation('next')[0]):null,
     previous:navigation('previous').length===1?describe(navigation('previous')[0]):null};
 }
 return {mode_count:mode.length,send_later:selectedMode,send_later_label:describe(modeTarget),timezone,
   date_count:formDate.length,date:describe(date),date_value:dateValue,summary,
   popup_count:roots.length,widget,save:save.length===1?describe(save[0]):null,
   save_count:save.length,preview_only_count:preview.length};
})(SETTINGS)"""


def _parse_display(text):
    text = re.sub(r'\s+', ' ', str(text or '')).strip()
    for pattern in ('%b %d, %Y %I:%M %p', '%B %d, %Y %I:%M %p',
                    '%b %d, %Y %H:%M', '%Y-%m-%d %H:%M', '%Y-%m-%dT%H:%M', '%B %d, %Y %H:%M'):
        try:
            return datetime.strptime(text, pattern)
        except ValueError:
            continue
    return None


def _zone_matches(text, expected):
    return text == expected or {text, expected} == {'Asia/Calcutta', 'Asia/Kolkata'}


def _summary_matches(lines, expected, zone):
    stamps = r'([A-Za-z]{3,9} \d{1,2}, \d{4}\s+\d{1,2}:\d\d(?:\s+[AP]M)?)'
    for text in lines:
        dates = [_parse_display(match) for match in re.findall(stamps, text, re.I)]
        match = re.search(r'Time\s*zone\s*:\s*([A-Za-z_]+(?:/[A-Za-z_]+)+)', text, re.I)
        if dates == [expected] and match and _zone_matches(match[1], zone):
            return True
    return False


class ScheduleDraft:
    def __init__(self, tools):
        self.tools = tools
        self.browser = tools.browser
        self.page = self.browser.page
        self.expected = tools.payload.scheduled_local().replace(tzinfo=None)
        self.zone = tools.payload.timezone
        if self.expected.second or self.expected.microsecond:
            raise V2Stop('SCHEDULE_PICKER_MINUTE_PRECISION_REQUIRED', owner='authorization')
        self.settings = {'day':self.expected.day,'hour':self.expected.hour,'minute':self.expected.minute,
                         'months':list(calendar.month_abbr)[1:]+list(calendar.month_name)[1:]}
        self.document = None
        self.saved_attempt = False

    def stop(self, code, **facts):
        self.browser.trace.agent_event('schedule_control_unresolved', role='observer', code=code, **facts)
        raise V2Stop(code, owner='recognition')

    async def view(self):
        doc = await self.tools.protect()
        if self.document is None:
            self.document = doc
        if (self.document != doc or self.tools.operator_approved_setup_document != doc
                or self.tools.operator_approved_payload is not self.tools.payload
                or not campaign_step_route(await self.page.url(), 'schedule')):
            raise V2Stop('SCHEDULE_BOUND_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        facts = await self.page.evaluate(SCHEDULE_VIEW.replace('SETTINGS', json.dumps(self.settings)))
        if not isinstance(facts, dict) or facts.get('code'):
            self.stop((facts or {}).get('code', 'SCHEDULE_VIEW_UNAVAILABLE'))
        if await self.tools.protect() != self.document:
            raise V2Stop('SCHEDULE_BOUND_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        return facts

    @staticmethod
    def target(facts, key):
        return (facts.get('widget') or {}).get(key) if key.startswith('hour') or key.startswith('minute') or key in {
            'day','apply','next','previous'} else facts.get(key)

    async def click(self, key, facts, *, method='native'):
        target = self.target(facts, key)
        if not target or not target.get('selector'):
            self.stop('SCHEDULE_' + key.upper() + '_NOT_BOUND')
        selector = target['selector']
        identity = await observed_node(self.browser, selector)
        loc = await stagehand_locator(self.browser, selector, identity)
        current = await self.view()
        if self.target(current, key) != target or await observed_node(self.browser, selector) != identity:
            self.stop('SCHEDULE_' + key.upper() + '_TARGET_CHANGED')
        self.browser.trace.agent_event('schedule_ui_action', role='controller', action=key, method=method)
        if method == 'native':
            await loc.click(timeout=self.tools.operation_timeout_ms)
        else:
            await loc.send_click_event(bubbles=True,cancelable=True,composed=True,
                                       timeout=self.tools.operation_timeout_ms)

    async def poll(self, predicate, attempts=5):
        for _ in range(attempts):
            facts = await self.view()
            if predicate(facts):
                return facts
            await asyncio.sleep(.2)
        return facts

    async def click_and_check(self, key, initial, predicate, *, unchanged=None):
        await self.click(key, initial)
        after = await self.poll(predicate)
        if (not predicate(after) and self.target(after, key) == self.target(initial, key)
                and (unchanged is None or unchanged(after))):
            await self.click(key, after, method='dom')
            after = await self.poll(predicate)
        return after

    async def prepare(self):
        await verify_active_campaign_stage(self.tools, 'schedule')
        facts = await self.view()
        if not facts['send_later']:
            await self.click('send_later_label', facts)
            facts = await self.poll(lambda result: result['send_later'])
        if not facts['send_later']:
            self.stop('SCHEDULE_SEND_LATER_READBACK_FAILED', radio_count=facts['mode_count'])
        if len(facts['timezone'])!=1 or not _zone_matches(facts['timezone'][0],self.zone):
            self.stop('SCHEDULE_TIMEZONE_READBACK_MISMATCH', timezone_count=len(facts['timezone']))
        self.browser.trace.agent_event('schedule_mode_timezone_verified', role='observer', send_later=True,
                                       timezone_matches=True, displayed_timezone=facts['timezone'][0])
        if not facts['date']:
            self.stop('SCHEDULE_DATE_TIME_CONTROL_NOT_UNIQUE', date_count=facts['date_count'])
        if facts['popup_count']:
            self.stop('SCHEDULE_PICKER_OPEN_BEFORE_FIELD_ACTION')
        opened = await self.click_and_check('date', facts, lambda result: result['popup_count']==1,
                                            unchanged=lambda result:result['popup_count']==0)
        if opened['popup_count']!=1:
            self.stop('SCHEDULE_DATE_PICKER_OPEN_NOT_VERIFIED', popup_count=opened['popup_count'])
        return opened

    def calendar_month(self, facts):
        picker = facts['widget']
        m = picker['month']
        if not m or not picker['year']:
            self.stop('SCHEDULE_CALENDAR_HEADER_UNVERIFIED', month_bound=bool(m), year_bound=bool(picker['year']),
                      month_candidates=picker['month_candidates'],year_candidates=picker['year_candidates'],
                      visible_selects=picker['visible_selects'],visible_buttons=picker['visible_buttons'])
        months = {name.lower():i for i in range(1,13) for name in (calendar.month_name[i],calendar.month_abbr[i])}
        return picker['year'], months[m]

    async def set_date(self, facts):
        if not facts['widget']:
            self.stop('SCHEDULE_DATE_PICKER_CLOSED')
        wanted = (self.expected.year, self.expected.month)
        for _ in range(24):
            current = self.calendar_month(facts)
            if current==wanted:
                break
            key = 'next' if current<wanted else 'previous'
            await self.click(key, facts)
            facts = await self.poll(lambda result: bool(result['widget']) and self.calendar_month(result)!=current)
            if not facts['widget'] or self.calendar_month(facts)==current:
                self.stop('SCHEDULE_CALENDAR_NAVIGATION_NOT_VERIFIED')
        else:
            self.stop('SCHEDULE_CALENDAR_NAVIGATION_LIMIT')
        if facts['widget']['day_count']!=1:
            self.stop('SCHEDULE_DAY_NOT_UNIQUE_OR_ENABLED', day_count=facts['widget']['day_count'])
        if not facts['widget']['day_selected']:
            facts = await self.click_and_check('day', facts,
                lambda result: bool(result['widget']) and result['widget']['day_selected'],
                unchanged=lambda result:bool(result['widget']) and self.calendar_month(result)==wanted)
        if not facts['widget'] or not facts['widget']['day_selected'] or self.calendar_month(facts)!=wanted:
            self.stop('SCHEDULE_SELECTED_DAY_READBACK_FAILED')
        return facts

    async def set_time_part(self, field, expected, facts):
        widget = facts.get('widget') or {}
        if not widget.get(field):
            self.stop('SCHEDULE_' + field.upper() + '_CONTROL_NOT_BOUND')
        if widget[field+'_value'] is not None and int(widget[field+'_value'])==expected:
            self.browser.trace.agent_event('schedule_time_part_verified', role='observer', field=field,
                                           value=expected, status='VERIFIED_EXISTING')
            return facts
        if widget[field]['tag']=='SELECT':
            selector=widget[field]['selector']
            identity=await observed_node(self.browser,selector)
            loc=await stagehand_locator(self.browser,selector,identity)
            opts=await self.page.evaluate(r"""(s=>[...document.querySelector(s.q).options].filter(
                o=>!o.disabled&&/^\d{1,2}$/.test(o.textContent.trim())&&Number(o.textContent.trim())===s.n
                ).map(o=>o.value))("""+json.dumps({'q':selector,'n':expected})+')')
            if len(opts)!=1 or await observed_node(self.browser,selector)!=identity:
                self.stop('SCHEDULE_' + field.upper() + '_OPTION_NOT_UNIQUE')
            await loc.select_option(opts,timeout=self.tools.operation_timeout_ms)
        else:
            name=field+'_option'
            popup=await self.click_and_check(field,facts,
                lambda result:bool((result['widget'] or {}).get(name)),
                unchanged=lambda result:bool(result['widget']) and
                    (result['widget'].get(field) or {}).get('expanded') is False)
            if not (popup.get('widget') or {}).get(name):
                self.stop('SCHEDULE_' + field.upper() + '_EXACT_OPTION_UNAVAILABLE')
            await self.click(name,popup)
        verified=await self.poll(lambda result:((result['widget'] or {}).get(field+'_value') or '').isdigit() and
            int(result['widget'][field+'_value'])==expected)
        raw=(verified.get('widget') or {}).get(field+'_value')
        if raw is None or int(raw)!=expected:
            self.stop('SCHEDULE_' + field.upper() + '_READBACK_MISMATCH')
        self.browser.trace.agent_event('schedule_time_part_verified', role='observer', field=field,
                                       value=expected, status='MATCH')
        return verified

    async def apply(self):
        self.tools.payload.check_schedule()
        facts = await self.prepare()
        facts = await self.set_date(facts)
        facts = await self.set_time_part('hour',self.expected.hour,facts)
        facts = await self.set_time_part('minute',self.expected.minute,facts)
        picker=facts.get('widget')
        if (not picker or self.calendar_month(facts)!=(self.expected.year,self.expected.month)
                or not picker['day_selected'] or int(picker['hour_value'])!=self.expected.hour
                or int(picker['minute_value'])!=self.expected.minute):
            self.stop('SCHEDULE_PICKER_READBACK_INCOMPLETE')
        self.browser.trace.agent_event('schedule_picker_values_verified', role='observer',
            year=self.expected.year,month=self.expected.month,day=self.expected.day,
            hour=self.expected.hour,minute=self.expected.minute)
        await self.click('apply',facts)
        await self.poll(lambda result:result['popup_count']==0)
        return await self.verify()

    async def verify(self):
        facts=await self.view()
        if facts['popup_count'] or _parse_display(facts['date_value'])!=self.expected:
            self.stop('SCHEDULE_APPLIED_FORM_READBACK_MISMATCH', popup_count=facts['popup_count'],
                      recognized_display=_parse_display(facts['date_value']) is not None)
        if not facts['send_later'] or len(facts['timezone'])!=1 or not _zone_matches(facts['timezone'][0],self.zone):
            self.stop('SCHEDULE_MODE_OR_TIMEZONE_CHANGED')
        if not _summary_matches(facts['summary'],self.expected,self.zone):
            self.stop('SCHEDULE_SUMMARY_READBACK_NOT_VERIFIED', summary_regions=len(facts['summary']))
        result={'date':self.expected.date().isoformat(),'time':self.expected.strftime('%H:%M'),
                'timezone':self.zone, 'summary_matches':True}
        self.browser.trace.agent_event('schedule_applied_readback', role='observer', **result)
        print('SCHEDULE_VERIFIED ' + json.dumps(result), flush=True)
        return facts


    async def require_save_control(self):
        facts = await self.verify()
        if not facts.get('save'):
            self.stop('SCHEDULE_SAVE_AND_PREVIEW_CONTROL_NOT_UNIQUE',
                      exact_save_controls=facts['save_count'], preview_only_controls=facts['preview_only_count'])
        return facts['save']

    async def save_and_preview(self, approved_target, mark_attempt):
        from browser.observed_control import protected_document

        self.tools.payload.check_schedule()
        current = await self.require_save_control()
        if current != approved_target:
            raise V2Stop('SCHEDULE_APPROVED_SAVE_CONTROL_CHANGED', owner='browser', unsafe=True)
        if (self.tools.operator_approved_payload is not self.tools.payload or
                self.tools.operator_approved_setup_document != self.document):
            raise V2Stop('SCHEDULE_DRAFT_APPROVAL_SCOPE_CHANGED', owner='authorization', unsafe=True)
        if self.saved_attempt:
            self.stop('SCHEDULE_DRAFT_SAVE_ALREADY_ATTEMPTED')
        selector = current['selector']
        identity = await observed_node(self.browser, selector)
        await self.tools.install_guard()
        if await self.tools.protect() != self.document or await observed_node(self.browser, selector) != identity:
            raise V2Stop('SCHEDULE_APPROVED_SAVE_CONTROL_CHANGED', owner='browser', unsafe=True)
        url = await self.page.url()
        self.saved_attempt = True
        mark_attempt()
        self.browser.trace.agent_event('schedule_draft_save_started', role='controller', operator_approved=True)
        settings = {'q':selector,'url':url}
        click_script = r"""(s=>{
          const nodes=document.querySelectorAll(s.q), e=nodes.length===1?nodes[0]:null;
          if(location.href!==s.url||!window.__netcoreV2Guard||!e||e.disabled||e.matches(':disabled')||
             !e.getClientRects().length||getComputedStyle(e).visibility!=='visible'||
             !/^save\s+(and|&)\s+preview$/i.test((e.innerText||e.value||'').trim()))return false;
          const grant={control:e,kind:'draft-preview',clicked:false,submitted:false};
          window.__netcoreV2Page1Next=grant;
          try{e.click();return grant.clicked;}finally{delete window.__netcoreV2Page1Next;}
        })(SETTINGS)"""
        try:
            clicked=await self.page.evaluate(click_script.replace('SETTINGS',json.dumps(settings)))
        except Exception:
            clicked=None
        self.browser.trace.agent_event('schedule_draft_click_result', role='controller',
                                       click_result='DISPATCHED' if clicked is True else 'BLOCKED' if clicked is False else 'UNCERTAIN')
        if clicked is False:
            self.stop('SCHEDULE_DRAFT_SAVE_CONTROL_NOT_ACTIONABLE')
        observation = {'preview_visible':False, 'campaign_match':False, 'schedule_match':False, 'draft_marker':False}
        for _ in range(15):
            stop=self.tools.operator_setup_stop() or self.tools.operator_login_binding.stop_code
            active=await self.browser.browser.context.active_page()
            if (stop or not active or active.page_id!=self.page.page_id
                    or protected_document(self.browser)!=self.document):
                break
            current_url=await self.page.url()
            tree=await self.browser.guard.send('Page.getFrameTree',session=self.document[1])
            frame=tree.get('frameTree',{}).get('frame',{})
            if (cdp_frame_url(frame)!=current_url or frame.get('id')!=self.document[4]
                    or (frame.get('loaderId') or None)!=self.document[5]):
                await asyncio.sleep(.2)
                continue
            is_preview=campaign_step_route(current_url,'preview')
            preview = await self.page.evaluate(r"""(()=>{
              const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility==='visible'&&!e.closest('[aria-hidden=true]');
              const n=x=>String(x??'').replace(/\s+/g,' ').trim();
              const roots=[...document.querySelectorAll('[role=dialog],mat-dialog-container,main,[role=main]')].filter(visible);
              const panels=roots.filter(r=>[...r.querySelectorAll('h1,h2,h3,[role=heading]')]
                .some(h=>visible(h)&&/^(campaign |whatsapp campaign )?preview$/i.test(n(h.innerText))));
              const unique=panels.filter(p=>!panels.some(q=>p!==q&&p.contains(q)));
              if(unique.length!==1)return null;
              const text=unique[0].innerText;
              return {text,labels:[...unique[0].querySelectorAll('span,dd,div,p,h2,h3')]
                .filter(e=>visible(e)&&!e.children.length).map(e=>n(e.innerText)).slice(0,240)};
            })()""")
            if protected_document(self.browser)!=self.document or await self.page.url()!=current_url:
                continue
            if preview:
                observation={'preview_visible':True,'campaign_match':self.tools.payload.campaign_name in preview['labels'],
                    'schedule_match':_summary_matches([preview['text']],self.expected,self.zone),
                    'draft_marker':any(re.fullmatch(r'(draft|draft saved|saved as draft)',x,re.I) for x in preview['labels'])}
                if all(observation.values()):
                    if is_preview:
                        self.tools.stage=self.tools.campaign_verified_stage='preview'
                    self.browser.trace.agent_event('schedule_saved_preview_verified', role='observer',
                        **observation,date=self.expected.date().isoformat(),time=self.expected.strftime('%H:%M'),timezone=self.zone)
                    return observation
            await asyncio.sleep(.35)
        self.browser.trace.agent_event('schedule_draft_outcome_unresolved', role='observer',
                                       click_attempted=True, **observation)
        raise V2Stop('SCHEDULE_DRAFT_SAVE_OUTCOME_UNVERIFIED_NO_RETRY', owner='uncertain_write', unsafe=True)
