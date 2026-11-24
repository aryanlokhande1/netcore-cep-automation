"""Deterministic SMS actions and fresh readback for the protected campaign tab."""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from langchain_core.tools import StructuredTool
from pydantic import create_model

from app.browser_mcp import EmptyArguments
from browser.discovery import login_page_context
from browser.observed_control import observed_node, protected_document, stagehand_locator
from modules.sms.preflight import SETUP_DESCRIPTION
from modules.whatsapp.browser_baseline import reject_security_negatives
from modules.whatsapp.contract import V2Stop
from modules.whatsapp.destination import campaign_step_route
from modules.whatsapp.observation import V2_LABEL_HELPERS


@dataclass(frozen=True)
class Field:
    kind: str
    labels: tuple[str, ...]


STAGES = {
    'setup': {'campaign_name': Field('text', ('campaign name',)), 'tag': Field('select', ('tag', 'add tag', 'add tags', 'tags'))},
    'audience': {'audience_type': Field('radio', ('segments/lists',)),
                 'include_audience': Field('select', ('included audience', 'include list/segment', 'include audience'))},
    'content': {'channel': Field('select', ('channel',)), 'sender_id': Field('select', ('sender id',)),
                'custom_domain': Field('select', ('custom domain',)), 'template': Field('template', ('template',))},
    'schedule': {'send_type': Field('radio', ('send later',)), 'schedule_datetime': Field('schedule', ('scheduled datetime',))},
    'preview': {},
}
FIELDS = {name: field for stage in STAGES.values() for name, field in stage.items()}
ACTION_FIELDS = {'set_sms_field': frozenset(name for name, field in FIELDS.items() if field.kind in {'text', 'radio', 'select'}),
                 'use_sms_template': frozenset({'template'}), 'apply_sms_schedule': frozenset({'schedule_datetime'})}
CAPABILITIES = frozenset((stage, action, name) for stage, fields in STAGES.items() for action, allowed in ACTION_FIELDS.items()
                         for name in allowed & fields.keys())


SURFACE = r'''(config => {
''' + V2_LABEL_HELPERS + SETUP_DESCRIPTION + r'''
 const norm = value => String(value ?? '').replace(/\s+/g, ' ').trim();
 const label = node => {
   const labels = [...(node.labels || [])];
   const implicit = labels.length === 1 && labels[0].contains(node) && !node.hasAttribute('aria-label') ?
     [...labels[0].childNodes].filter(child=>child!==node).map(child=>child.textContent).join(' ') : null;
   return norm(implicit ?? fieldLabel(node, 'dom')).replace(/[\s:*]+$/g, '').toLowerCase();
 };
 const text = node => norm(node?.innerText ?? node?.textContent);
 const visible = node => !!node && node.getClientRects().length > 0 &&
   getComputedStyle(node).visibility === 'visible' && !node.closest('[aria-hidden=true],[inert]');
 const enabled = node => visible(node) && !node.matches(':disabled,[readonly]') && !node.closest('[aria-disabled=true]');
 const unique = nodes => nodes.length === 1 ? nodes[0] : null;
 const selector = node => {
   if (!node) return null;
   const parts = [];
   for (let el=node; el && el!==document.body; el=el.parentElement) {
     const siblings=[...el.parentElement.children].filter(n=>n.localName===el.localName);
     parts.unshift(CSS.escape(el.localName)+':nth-of-type('+(siblings.indexOf(el)+1)+')');
   }
   return 'body > '+parts.join(' > ');
 };
 const state = n => ({selector: selector(n), enabled: !!n && enabled(n)});
 const title = config.stage;
 const activeStep = n => [n,n.parentElement].filter(Boolean).some(p=>
   ['page','step','true'].includes(p.getAttribute('aria-current')) || p.getAttribute('aria-selected')==='true' ||
   [...p.classList].some(c=>/(^|[-_])(active|current|selected)([-_]|$)/i.test(c)));
 const headings = [...document.querySelectorAll('h1,h2,h3,h4,[role=heading]')].filter(visible);
 const stepName = n => text(n).toLowerCase();
 const titled = n => [title, 'sms '+title, 'campaign '+title, 'sms campaign '+title].includes(stepName(n));
 const landmarks = [...document.querySelectorAll('main,[role=main]')].filter(visible);
 const landmark = unique(landmarks);
 const setup = title === 'setup' ? describeSmsSetup() : null;
 const setupControl = setup?.control;
 const scope = setup ? (landmark?.contains(setupControl) ? landmark :
                        visible(setupControl?.form) ? setupControl.form : null) : landmark;
 const markers = scope ? [...scope.querySelectorAll('li,button,a,[aria-current],[aria-selected]')].filter(visible) : [];
 const conflicting = markers.some(n=>['setup','audience','content','schedule','preview'].includes(stepName(n)) &&
                                      stepName(n)!==title && activeStep(n));
 const stageVisible = setup ? !!setupControl && setup.setup_view : !!scope && !conflicting &&
   (headings.some(n=>scope.contains(n)&&titled(n)) || markers.some(n=>titled(n)&&activeStep(n)));
 if (!stageVisible || !scope) return {stage_visible:stageVisible, scope_available:!!scope, fields:{},
   observation_anchor:selector(setupControl || scope)};
 const controls = [...scope.querySelectorAll('input,select,textarea')].filter(visible);
 const buttons = (root, names, transition=false) => unique([...root.querySelectorAll('button,input[type=button],[role=button]')]
   .filter(n=>enabled(n) && !n.closest('a,[formaction]') &&
     (transition || n.type !== 'submit' || !n.form) &&
     names.includes(norm(n.getAttribute('aria-label') || n.value || text(n)).toLowerCase())));
 const field = (labels, kind) => unique(controls.filter(n=>labels.includes(label(n)) &&
   (kind==='select' ? n.matches('select:not([multiple])') : n.type===kind)));
 const tagOwners = n => `${n.getAttribute('aria-controls') ?? ''} ${n.getAttribute('aria-owns') ?? ''}`
   .split(/\s+/).filter(Boolean);
 const tag_checks = {};
 let tag_candidate = null;
 const tagWidget = wanted => {
   const markers = [...scope.querySelectorAll('mat-label')].filter(visible);
   const exact = n=>text(n).toLowerCase()==='add tags';
   const anchors = markers.filter(n=>text(n).replace(/[\s:*]+$/g,'').toLowerCase()==='add tags');
   const anchor = unique(anchors);
   Object.assign(tag_checks,{label_count:anchors.length,legacy_label_count:markers.filter(exact).length,
     setup_scope:scope.contains(setupControl),initial_binding:false,popup_owned:false,exact_option_visible:false,
     target_fresh:null});
   for(let group=anchor?.parentElement, depth=1;group&&group!==scope&&depth<=5;group=group.parentElement,depth++){
     const inputs=[...group.querySelectorAll('input,select,textarea')].filter(visible);
     const grids=[...group.querySelectorAll('mat-chip-grid')].filter(visible);
     Object.assign(tag_checks,{ancestor_depth:depth,visible_input_count:inputs.length,visible_grid_count:grids.length,
       includes_campaign_name:inputs.includes(setupControl)});
     if(inputs.length>1 || inputs.includes(setupControl)) return null;
     if(!grids.length||!inputs.length)continue;
     const grid=unique(grids), input=unique(inputs);
     const dropdown=input?.matches('input[role=combobox]:is([aria-haspopup=listbox],[aria-haspopup=true])');
     const safe=!!input&&!input.closest('a,[formaction],[contenteditable=true]');
     const form=!!input&&input.form===setupControl.form;
     const gridOwner=!!grid&&grid.closest('form')===setupControl.form;
     const textInput=!!input&&input.type==='text';
     tag_candidate={input:selector(input),grid:selector(grid),group:selector(group)};
     Object.assign(tag_checks,{enabled:enabled(input),setup_form_owner:form,chip_grid_owner:gridOwner,
       combobox_dropdown:!!dropdown,safe_target:safe,effective_text_type:textInput,
       legacy_explicit_text_type:!!input?.matches('input[type=text]')});
     if(!gridOwner||!form||!dropdown||!textInput||!safe||!enabled(input))return null;
     tag_checks.initial_binding=true;
     const chips=[...grid.querySelectorAll('mat-chip-row,mat-chip,mat-chip-option')].filter(visible);
     const chip=unique(chips);
     const chipLabel=chip && unique([...chip.querySelectorAll('.mat-mdc-chip-action-label,.mdc-evolution-chip__text-label')].filter(visible));
     const ids=[...new Set(tagOwners(input))], id=unique(ids);
     const list=id && unique([...document.querySelectorAll('#'+CSS.escape(id))]);
     const contested=id && [...document.querySelectorAll('[aria-controls],[aria-owns]')]
       .some(n=>n!==input&&tagOwners(n).includes(id));
     const owned=list?.matches?.('[role=listbox]')&&!contested&&visible(list)&&input.getAttribute('aria-expanded')==='true';
     const option=owned && unique([...list.querySelectorAll('mat-option[role=option]')].filter(n=>enabled(n) &&
       n.closest('[role=listbox]')===list && !n.closest('a,[href],[formaction],[data-create],[data-select2-tag]') &&
       !n.querySelector('button,a,[role=button]') && text(n)===wanted));
     Object.assign(tag_checks,{expanded:input.getAttribute('aria-expanded')==='true',popup_reference_count:ids.length,
       popup_visible:!!list&&visible(list),popup_contested:!!contested,popup_owned:!!owned,exact_option_visible:!!option});
     return {...state(input), widget:'chip_combobox', group:selector(group), grid:selector(grid),
       selected_count:chips.length, matched:!!chip && text(chipLabel||chip)===wanted,
       query_empty:input.value==='', expanded:input.getAttribute('aria-expanded')==='true', option:state(option)};
   }
   return null;
 };
 const exactOption = (n,wanted) => n ? unique([...n.options].filter(o=>!o.disabled && !o.closest('optgroup[disabled]') &&
   norm(o.textContent)===wanted)) : null;
 const fields = {};
 for (const [key,spec] of Object.entries(config.fields)) {
   const v = config.values[key];
   if (['text','radio','select'].includes(spec.kind)) {
     const n = field(spec.labels,spec.kind);
     if(key==='tag'&&!n&&!controls.some(c=>spec.labels.includes(label(c))&&c.matches('select'))){
       const widget=tagWidget(v);
       if(widget){fields[key]=widget;continue;}
     }
     const option = spec.kind==='select' ? exactOption(n,v) : null;
     const optionBound = !!option && [...n.options].filter(o=>o.value===option.value).length===1;
     fields[key] = {...state(n), ...(key==='tag'&&n?{widget:'native_select'}:{}),
       option_value: optionBound ? option.value : null,
       matched: !!n && (spec.kind==='radio' ? n.checked : spec.kind==='select' ?
         optionBound && n.selectedOptions[0]===option : n.value===v)};
   }
 }
 if (title==='content') {
   const wanted = config.values.template;
   const card = unique([...scope.querySelectorAll('article,[role=listitem]')].filter(n=>visible(n) &&
     n.getAttribute('data-template-id')===String(config.template_id) &&
     (norm(n.getAttribute('aria-label'))===wanted || [...n.querySelectorAll('h1,h2,h3,h4,[role=heading]')]
       .some(h=>visible(h)&&text(h)===wanted))));
   const selected = unique([...scope.querySelectorAll('[data-selected-template-id]')].filter(visible));
   fields.template = {...state(card), pointer_over: !!card && card.matches(':hover'),
     menu: state(card && buttons(card,['more options','more actions','⋮','...'])),
     use: state(card && buttons(card,['use'])), matched: !!selected &&
       selected.getAttribute('data-selected-template-id')===String(config.template_id) && text(selected)===wanted};
 }
 if (title==='schedule') {
   const date = field(['date','schedule date'],'date'), time = field(['time','schedule time'],'time');
   const zoneLabels = ['time zone','timezone','schedule timezone','scheduling timezone'];
   const zones = [...scope.querySelectorAll('select:not([multiple]),input,[role=combobox],output,[role=status]')]
     .filter(n=>visible(n) && zoneLabels.includes(label(n)));
   const zone = unique(zones);
   const rawZone = norm(zone?.matches('select') ? zone.selectedOptions[0]?.textContent :
                        zone?.matches('input') ? zone.value : text(zone));
   const zoneName = /^(?:UTC|GMT|[A-Za-z_]+(?:\/[A-Za-z0-9_+-]+)+)$/.test(rawZone) ? rawZone : null;
   const zoneMatches = !!zoneName && config.zone_labels.includes(zoneName);
   const summary = unique([...scope.querySelectorAll('[aria-label="Schedule summary"]')].filter(visible));
   const dateMatches = !!date && date.value===config.date, timeMatches = !!time && time.value===config.time;
   fields.schedule_datetime = {date:state(date), time:state(time), apply:state(buttons(scope,['apply'])),
     zone_matches:zoneMatches, zone_name:zoneName, zone_control_count:zones.length, date_matches:dateMatches, time_matches:timeMatches,
     summary_matches:!!summary && text(summary)===config.values.schedule_datetime,
     matched:!!summary && text(summary)===config.values.schedule_datetime && dateMatches && timeMatches && zoneMatches};
 }
 const nextControl = buttons(scope,title==='schedule' ? ['preview'] : ['next step'],true);
 const next = {...state(nextControl), native_form_submission:!!nextControl?.form && nextControl.type==='submit',
   form_method: nextControl?.form && nextControl.type==='submit' ? nextControl.form.method.toUpperCase() : null};
 const preview = {};
 if (title==='preview') {
   const pairs = [...scope.querySelectorAll('dt')].filter(visible).flatMap(n=>
     n.nextElementSibling?.matches('dd') && visible(n.nextElementSibling) ? [[text(n).toLowerCase(),text(n.nextElementSibling)]] : []);
   const byLabel = labels => pairs.filter(([key])=>labels.includes(key.replace(/[:*]+$/g,'').trim())).map(pair=>pair[1]);
   for (const [key,labels] of Object.entries(config.preview_labels)) {
     const values = byLabel(labels);
     const allowed = key==='timezone' ? config.zone_labels : [String(config.values[key] ?? config[key] ?? '')];
     preview[key] = {displayed:values.length>0, matched:values.length===1 && allowed.includes(values[0])};
   }
   const ids = byLabel(['campaign id']);
   preview.campaign_id = ids.length===1 && /^\d{1,30}$/.test(ids[0]) ? ids[0] : null;
 }
 return {stage_visible:true, scope_available:true, fields, next, preview, observation_anchor:selector(scope),
   ...(title==='setup'?{tag_checks,tag_candidate}:{})};
})(CONFIG)'''


class SMSExecutor:
    def __init__(self, browser, binding, payload, *, route=None, clock=None):
        self.browser, self.binding, self.payload = browser, binding, payload
        self.route = route or payload.create_url
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.stage = 'setup'
        self.terminal = False
        self.preview_dispatched = False
        self.schedule_timezone_document = None
        self.early_progression_approved = False
        self.payload_approval_ref = None
        self.actions = []
        self.observations = set()
        self.last_observer_snapshot = {}
        self.mutation_count = 0
        self.browser.setup_binding = binding
        self.browser.setup_page = browser.page
        self.values = payload.model_dump()
        date = datetime.fromisoformat(payload.schedule_datetime)
        self.date = date.date().isoformat()
        self.time = date.strftime('%H:%M')

    async def protect(self):
        await self.binding.require_current(self.browser)
        before = protected_document(self.browser)
        context = await login_page_context(self.browser)
        url = await self.browser.page.url()
        if (not context['primary_document_coherent'] or not self.browser.policy.permits(url)
                or not (url == self.route or campaign_step_route(url, self.stage, self.route))
                or before != protected_document(self.browser)):
            raise V2Stop('SMS_STAGE_ROUTE_UNVERIFIED', owner='browser', unsafe=True)
        await reject_security_negatives(self.browser)
        self.browser.budget()
        return (*before, url)

    async def snapshot(self):
        bound = await self.protect()
        preview_labels = {key: list(spec.labels) for key, spec in FIELDS.items()}
        preview_labels.update(audience_type=['audience mode'], send_type=['send mode', 'when to send'],
                              template_id=['template id'], timezone=['timezone', 'time zone'])
        config = {'stage': self.stage, 'fields': {name: vars(spec) for name, spec in STAGES[self.stage].items()},
                  'values': self.values, 'template_id': self.payload.template_id, 'date': self.date, 'time': self.time,
                  'zone_labels': [self.payload.timezone, 'Asia/Calcutta'], 'preview_labels': preview_labels}
        facts = await self.browser.page.evaluate(SURFACE.replace('CONFIG', json.dumps(config)))
        if await self.protect() != bound:
            raise V2Stop('SMS_STAGE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        return facts, bound

    async def readback(self):
        facts, document = await self.snapshot()
        schedule = facts['fields'].get('schedule_datetime', {})
        timezone = ({'matched': schedule.get('zone_matches') is True, 'displayed_iana': schedule.get('zone_name'),
                    'labeled_control_count': schedule.get('zone_control_count', 0), 'source': 'LIVE_SCHEDULE_UI'}
                    if self.stage == 'schedule' else None)
        if (timezone and timezone['matched'] and facts['stage_visible']
                and facts['fields'].get('send_type', {}).get('matched') is True):
            self.schedule_timezone_document = document
        elif self.stage == 'schedule':
            self.schedule_timezone_document = None
        proof = {'stage': self.stage, 'stage_visible': facts['stage_visible'],
                'scope_available': facts['scope_available'],
                'bound_controls': sorted(name for name, state in facts['fields'].items()
                                         if state.get('enabled') is True or state.get('date', {}).get('enabled') is True),
                'fields': {name: data.get('matched') is True for name, data in facts['fields'].items()},
                **({'timezone': timezone} if timezone else {}),
                **({'preview': facts.get('preview', {})} if self.stage == 'preview' else {})}
        self.last_observer_snapshot = proof
        return proof

    async def observe_stage(self, approach):
        if (self.stage, approach) in self.observations:
            return {'ok': False, 'code': 'SMS_OBSERVATION_APPROACH_ALREADY_USED'}
        facts, document = await self.snapshot()
        result = {'stage_visible': facts['stage_visible'], 'scope_available': facts['scope_available'],
                  'bound_controls': sorted(name for name, state in facts['fields'].items() if state.get('enabled') is True)}
        if approach == 'semantic':
            anchor = facts['observation_anchor']
            if not anchor:
                return {'ok': False, 'code': 'SMS_SEMANTIC_ANCHOR_UNVERIFIED'}
            identity = await observed_node(self.browser, anchor)
            tree = await self.browser.guard.send('Accessibility.queryAXTree', {'backendNodeId': identity[-1]}, identity[1])
            if await self.protect() != document or await observed_node(self.browser, anchor) != identity:
                raise V2Stop('SMS_OBSERVATION_ANCHOR_CHANGED', owner='browser', unsafe=True)
            labels = {label for field in STAGES[self.stage].values() for label in field.labels}
            names = {str(node.get('name', {}).get('value', '')).strip().lower() for node in tree.get('nodes', []) if not node.get('ignored')}
            result['observed_approved_labels'] = sorted(names & labels)
        self.observations.add((self.stage, approach))
        self.browser.trace.agent_event('sms_doer_approach', role='doer', stage=self.stage,
                                      approach=approach, status='VALID_OBSERVATION', **result)
        return {'ok': True, **result}

    async def inspect_tag(self):
        from modules.sms.tags import inspect_tag_region

        facts, _ = await inspect_tag_region(self)
        self.browser.trace.agent_event('sms_doer_tag_inspection', role='doer', stage=self.stage, **facts)
        return facts

    async def locator(self, entry, document):
        if not entry or not entry.get('selector') or entry.get('enabled') is not True:
            raise V2Stop('SMS_TARGET_NOT_UNIQUE_OR_ACTIONABLE', owner='recognition')
        if await self.protect() != document:
            raise V2Stop('SMS_STAGE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        identity = await observed_node(self.browser, entry['selector'])
        locator = await stagehand_locator(self.browser, entry['selector'], identity)
        if await self.protect() != document or not await locator.is_visible(timeout=2000):
            raise V2Stop('SMS_TARGET_NOT_CURRENTLY_VISIBLE', owner='browser', unsafe=True)
        return locator, identity

    async def mutate(self, action, field):
        if (self.stage, action, field) not in CAPABILITIES or self.terminal:
            raise V2Stop('SMS_ACTION_OUTSIDE_SCOPE', owner='authorization', unsafe=self.terminal)
        facts, document = await self.snapshot()
        entry = facts['fields'].get(field)
        if not facts['stage_visible'] or not entry:
            return {'ok': False, 'code': 'SMS_STAGE_FIELD_UNVERIFIED', 'field': field}
        if entry['matched']:
            return {'ok': True, 'field': field, 'already_matching': True}
        try:
            if action == 'use_sms_template':
                await self.use_template(entry, document)
            elif action == 'apply_sms_schedule':
                await self.apply_schedule(entry, facts, document)
            else:
                await self.set_field(field, entry, document)
        except V2Stop as error:
            self.browser.trace.agent_event('sms_task_action_blocked', role='doer', stage=self.stage,
                                           action=action, field=field, code=error.code, retry_safe=not error.unsafe)
            if error.unsafe or self.terminal:
                self.terminal = True
                raise
            return {'ok': False, 'code': error.code, 'field': field}
        event = self.browser.trace.agent_event('sms_task_action', role='doer', stage=self.stage,
                                               action=action, field=field, status='EXECUTED')
        self.actions.append((self.stage, action, field, event))
        return {'ok': True, 'field': field, 'needs_independent_readback': True}

    async def write(self, operation):
        self.terminal = True
        self.mutation_count += 1
        self.browser.trace.agent_event('sms_campaign_mutation_dispatched', role='controller', stage=self.stage,
                                       mutation_count=self.mutation_count)
        try:
            await operation()
        except Exception:
            raise V2Stop('SMS_WRITE_OUTCOME_UNCERTAIN', owner='executor', unsafe=True) from None

    async def set_field(self, field, entry, document):
        loc, identity = await self.locator(entry, document)
        kind = FIELDS[field].kind
        custom_tag = field == 'tag' and entry.get('widget') == 'chip_combobox'
        if kind == 'select' and not custom_tag and entry['option_value'] is None:
            raise V2Stop('SMS_EXACT_ENABLED_OPTION_NOT_UNIQUE', owner='recognition')
        fresh, bound = await self.snapshot()
        if (bound != document or fresh['fields'].get(field) != entry
                or await observed_node(self.browser, entry['selector']) != identity):
            raise V2Stop('SMS_STAGE_FIELD_BINDING_CHANGED', owner='recognition')
        if custom_tag:
            await self.select_tag(loc, identity, entry, document)
        elif kind == 'select':
            await self.write(lambda: loc.select_option(entry['option_value'], timeout=3000))
        elif kind == 'text':
            await self.write(lambda: loc.fill(self.values[field], timeout=3000))
        else:
            await self.write(lambda: loc.click(timeout=3000))
        await self.confirm_write(field)

    async def select_tag(self, locator, identity, entry, document):
        if entry['selected_count'] or not entry['query_empty']:
            raise V2Stop('SMS_TAG_WIDGET_INITIAL_STATE_UNVERIFIED', owner='recognition')
        group = await observed_node(self.browser, entry['group'])
        grid = await observed_node(self.browser, entry['grid'])
        if not entry['expanded']:
            if (await self.protect() != document or
                    await observed_node(self.browser, entry['selector']) != identity):
                raise V2Stop('SMS_TAG_WIDGET_BINDING_CHANGED', owner='browser', unsafe=True)
            await self.write(lambda: locator.click(timeout=3000))
        for attempt in range(6):
            facts, current = await self.snapshot()
            tag = facts['fields'].get('tag', {})
            if (current != document or any(tag.get(key) != entry[key] for key in ('selector', 'group', 'grid', 'widget'))
                    or tag.get('selected_count') != 0 or tag.get('query_empty') is not True
                    or await observed_node(self.browser, entry['selector']) != identity
                    or await observed_node(self.browser, entry['group']) != group
                    or await observed_node(self.browser, entry['grid']) != grid):
                raise V2Stop('SMS_TAG_WIDGET_BINDING_CHANGED', owner='browser', unsafe=True)
            if tag['option']['enabled'] or attempt == 5:
                self.browser.trace.agent_event('sms_tag_action_checks', role='doer', stage='setup',
                    binding_checks={**facts['tag_checks'], 'target_fresh': True})
            if tag['option']['enabled']:
                break
            if attempt < 5:
                await asyncio.sleep(.2)
        else:
            raise V2Stop('SMS_TAG_OWNED_EXISTING_OPTION_UNVERIFIED', owner='recognition', unsafe=self.terminal)
        option, option_identity = await self.locator(tag['option'], document)
        fresh, current = await self.snapshot()
        if (current != document or fresh['fields'].get('tag') != tag
                or await observed_node(self.browser, entry['selector']) != identity
                or await observed_node(self.browser, entry['group']) != group
                or await observed_node(self.browser, entry['grid']) != grid
                or await observed_node(self.browser, tag['option']['selector']) != option_identity):
            raise V2Stop('SMS_TAG_EXISTING_OPTION_BINDING_CHANGED', owner='browser', unsafe=True)
        await self.write(lambda: option.click(timeout=3000))

    async def confirm_write(self, field):
        for attempt in range(6):
            facts, _ = await self.snapshot()
            if facts['fields'].get(field, {}).get('matched') is True:
                self.terminal = False
                return
            if attempt < 5:
                await asyncio.sleep(.2)
        raise V2Stop('SMS_WRITE_READBACK_MISMATCH', owner='observer', unsafe=True)

    async def use_template(self, entry, document):
        _, identity = await self.locator(entry, document)
        await self.browser.guard.send('DOM.scrollIntoViewIfNeeded', {'backendNodeId': identity[-1]}, identity[1])
        await self.locator(entry, document)
        point = await self.browser.page.evaluate(r'''(q=>{
          const card=document.querySelector(q), r=card?.getBoundingClientRect();
          if(!r) return null;
          const left=Math.max(0,r.left), right=Math.min(innerWidth,r.right);
          const top=Math.max(0,r.top), bottom=Math.min(innerHeight,r.bottom);
          if(right<=left || bottom<=top) return null;
          const x=(left+right)/2, y=(top+bottom)/2, hit=document.elementFromPoint(x,y);
          return hit && card.contains(hit) ? [x,y] : null;
        })(SELECTOR)'''.replace('SELECTOR', json.dumps(entry['selector'])))
        if not point or await self.protect() != document or await observed_node(self.browser, entry['selector']) != identity:
            raise V2Stop('SMS_TEMPLATE_HOVER_TARGET_UNVERIFIED', owner='recognition')
        await self.browser.guard.send('Input.dispatchMouseEvent',
            {'type': 'mouseMoved', 'x': point[0], 'y': point[1], 'button': 'none', 'buttons': 0}, identity[1])
        facts, current = await self.snapshot()
        bound = facts['fields'].get('template', {})
        if current != document or bound.get('selector') != entry['selector']:
            raise V2Stop('SMS_TEMPLATE_CARD_CHANGED_AFTER_HOVER', owner='recognition')
        if not bound.get('menu', {}).get('enabled'):
            raise V2Stop('SMS_TEMPLATE_MENU_NOT_VISIBLE_AFTER_HOVER', owner='recognition')
        await self.locator(entry, document)
        if await observed_node(self.browser, entry['selector']) != identity:
            raise V2Stop('SMS_TEMPLATE_CARD_CHANGED', owner='browser', unsafe=True)
        menu, _ = await self.locator(bound.get('menu'), document)
        await self.write(lambda: menu.click(timeout=3000))
        facts, current = await self.snapshot()
        bound = facts['fields'].get('template', {})
        if (current != document or bound.get('selector') != entry['selector']
                or await observed_node(self.browser, entry['selector']) != identity):
            raise V2Stop('SMS_TEMPLATE_MENU_OWNERSHIP_UNVERIFIED', owner='browser', unsafe=True)
        use, _ = await self.locator(bound.get('use'), document)
        await self.write(lambda: use.click(timeout=3000))
        await self.confirm_write('template')

    async def apply_schedule(self, entry, facts, document):
        if (self.schedule_timezone_document != document or not entry['zone_matches']
                or not facts['fields']['send_type']['matched']):
            raise V2Stop('SMS_SCHEDULE_MODE_OR_TIMEZONE_UNVERIFIED', owner='recognition')
        if datetime.fromisoformat(self.payload.schedule_datetime) <= self.clock():
            raise V2Stop('SMS_SCHEDULE_NOT_IN_FUTURE', owner='authorization')
        date, _ = await self.locator(entry['date'], document)
        await self.write(lambda: date.fill(self.date, timeout=3000))
        fresh, bound = await self.snapshot()
        if bound != document:
            raise V2Stop('SMS_STAGE_DOCUMENT_CHANGED', owner='browser', unsafe=True)
        time, _ = await self.locator(fresh['fields'].get('schedule_datetime', {}).get('time'), document)
        await self.write(lambda: time.fill(self.time, timeout=3000))
        fresh, current = await self.snapshot()
        state = fresh['fields'].get('schedule_datetime', {})
        if current != document or not all(state.get(key) is True for key in ('date_matches', 'time_matches', 'zone_matches')):
            raise V2Stop('SMS_SCHEDULE_INPUT_READBACK_MISMATCH', owner='observer', unsafe=True)
        apply, _ = await self.locator(state['apply'], document)
        await self.write(lambda: apply.click(timeout=3000))
        await self.confirm_write('schedule_datetime')

    def registered_tools(self):
        if self.stage == 'preview':
            return []
        observation_args = create_model('ObserveSMSStage', __base__=EmptyArguments,
                                        approach=(Literal['semantic', 'dom'], ...))
        tools = [StructuredTool.from_function(coroutine=self.observe_stage, name='observe_sms_stage',
                 description='Read scoped accessibility or DOM evidence without changing the form.', args_schema=observation_args)]
        if self.stage == 'setup':
            tools.append(StructuredTool.from_function(coroutine=self.inspect_tag, name='inspect_sms_tag',
                description='Read the Add tags binding, popup and exact-option checks in the current Setup form.',
                args_schema=EmptyArguments))
        for action, allowed in ACTION_FIELDS.items():
            fields = tuple(name for name in STAGES[self.stage] if name in allowed)
            if not fields:
                continue
            async def invoke(field, operation=action):
                return await self.mutate(operation, field)
            args = create_model(f'{self.stage}_{action}', __base__=EmptyArguments,
                                field=(Literal.__getitem__(fields), ...))
            tools.append(StructuredTool.from_function(coroutine=invoke, name=action,
                description=f'Apply the approved value for this {self.stage} field. No value or selector argument.', args_schema=args))
        return tools

    async def advance(self, verified, *, before_preview):
        facts, document = await self.snapshot()
        expected = set(STAGES[self.stage])
        if (self.terminal or verified['stage'] != self.stage or not verified['stage_visible'] or
                {key for key, ok in verified['fields'].items() if ok} != expected or not facts['stage_visible'] or
                {key for key, observed in facts['fields'].items() if observed.get('matched')} != expected):
            raise V2Stop('SMS_ADVANCE_REQUIRES_INDEPENDENT_READBACK', owner='observer')
        loc, identity = await self.locator(facts['next'], document)
        destination = tuple(STAGES)[tuple(STAGES).index(self.stage) + 1]
        if destination != 'preview':
            self.browser.trace.agent_event('sms_next_step_persistence_scope', role='controller', stage=self.stage,
                control='NEXT_STEP', destination=destination, server_persistence='POSSIBLE_UNDETERMINED',
                native_form_submission=facts['next']['native_form_submission'], form_method=facts['next']['form_method'],
                operator_approved=self.early_progression_approved, approval_ref=self.payload_approval_ref)
            if not self.early_progression_approved:
                raise V2Stop('SMS_POSSIBLY_PERSISTING_PROGRESSION_NOT_APPROVED', owner='authorization')
        if destination == 'preview':
            if self.preview_dispatched:
                raise V2Stop('SMS_PREVIEW_ALREADY_DISPATCHED', owner='authorization', unsafe=True)
            before_preview()
        if await self.protect() != document or await observed_node(self.browser, facts['next']['selector']) != identity:
            raise V2Stop('SMS_TRANSITION_CONTROL_CHANGED', owner='browser', unsafe=True)
        if destination == 'preview':
            self.preview_dispatched = True
        await self.write(lambda: loc.click(timeout=3000))
        self.stage = destination


class SMSObserver:
    def __init__(self, executor, *, tag_investigation=False):
        self.executor = executor
        self.browser = executor.browser
        self.stage = executor.stage
        self.tag_investigation = tag_investigation
        self.tag_region = None

    async def protect(self):
        return await self.executor.protect()

    def registered_tools(self):
        tools = [StructuredTool.from_function(coroutine=self.executor.readback, name='read_sms_stage',
                description='Independently read the visible stage and compare actual UI state to the fixed payload.',
                args_schema=EmptyArguments)]
        if self.tag_investigation and self.stage == 'setup':
            tools.append(StructuredTool.from_function(coroutine=self.inspect_tag, name='inspect_sms_tag',
                description='Read bounded, sanitized DOM and accessibility shape for the current Add tags region.',
                args_schema=EmptyArguments))
        return tools

    async def inspect_tag(self):
        from modules.sms.tags import inspect_tag_region

        facts, self.tag_region = await inspect_tag_region(self.executor)
        return facts
