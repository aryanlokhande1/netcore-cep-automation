"""Controller sequencing for known Setup controls, with bounded Doer recovery."""
import asyncio
import json

from modules.whatsapp.contract import PAGE1_SETUP_FIELDS, STAGE_FIELDS, V2Stop
from modules.whatsapp.observer import deterministic_readback


def controls_for(tools, field, part='control'):
    return [item for item in (tools.last_observation or {}).get('controls', ())
            if item.get('field') == field and item.get('part') == part and item.get('enabled') is True
            and not item.get('create') and (part != 'option' or item.get('exact') is True)]


def blocked(code, **details):
    return {'ok': False, 'error': code, **details}


def has_verified_open(tools, field):
    return bool(controls_for(tools, field, 'option') or any(
        item.get('expanded') is True for item in controls_for(tools, field)))


async def field_action(tools, field, action, reference):
    args = {'action': action, 'reference': reference}
    if action != 'inspect':
        args['field'] = field
    result, _ = await tools.action(args)
    return result


async def choose_settled_template(tools):
    # The exact name can appear in the unfiltered result list. Wait for the
    # search to replace it and verify a stable backend node before selection.
    await asyncio.sleep(.8)
    previous = None
    for attempt in range(4):
        await tools.observe()
        choices = controls_for(tools, 'template', 'option')
        evidence = (tools.last_observation or {}).get('scoped_option_evidence', {})
        current = None
        if len(choices) == 1 and has_verified_open(tools, 'template'):
            reference = choices[0]['reference']
            entry = tools.references.get(reference)
            if entry:
                current = (entry['identity'], evidence.get('visible_owned'))
                if current == previous:
                    tools.browser.trace.agent_event('content_template_results_settled', role='observer',
                        status='UNIQUE_STABLE_RESULT', visible_option_count=evidence.get('visible_owned'),
                        observations=attempt+1)
                    return await field_action(tools, 'template', 'select', reference)
        previous = current
        if attempt < 3:
            await asyncio.sleep(.4)
    reason = ('CONTENT_TEMPLATE_RESULT_AMBIGUOUS' if len(choices) > 1 else
              'CONTENT_TEMPLATE_RESULT_MISSING' if not choices else 'CONTENT_TEMPLATE_RESULT_NOT_STABLE')
    tools.browser.trace.agent_event('content_template_results_settled', role='observer',
        status=reason, exact_option_count=len(choices), visible_option_count=evidence.get('visible_owned'))
    return blocked(reason, option_evidence=evidence)


async def choose_dropdown(tools, field, control):
    if tools.binding(field).kind == 'native_select':
        if field == 'tag':
            return blocked('TAG_CHIP_PICKER_NOT_BOUND')
        options = await field_action(tools, field, 'options', control['reference'])
        if not options.get('ok'):
            return options
        choices = [item for item in options['options'] if item.get('exact') and item.get('enabled')
                   and item.get('existing')]
        if len(choices) != 1 or not options.get('control_reference'):
            return blocked('EXACT_EXISTING_OPTION_NOT_UNIQUE', exact_option_count=len(choices))
        return await field_action(tools, field, 'select', options['control_reference'])

    if not has_verified_open(tools, field):
        opened = await field_action(tools, field, 'click', control['reference'])
        if not opened.get('dropdown_open_verified'):
            if tools.terminal or tools.pending_write:
                return opened
            # The click path refreshed the observation. Re-resolve before
            # distinguishing a stale trigger from an open or closed popup.
            fresh = controls_for(tools, field)
            inspection = (await field_action(tools, field, 'inspect', fresh[0]['reference'])
                          if len(fresh) == 1 else blocked('FRESH_TRIGGER_NOT_UNIQUE'))
            observed_open = has_verified_open(tools, field)
            tools.browser.trace.agent_event('page1_dropdown_recovery', role='observer', field=field,
                failure=opened.get('error'), trigger_resolved=len(fresh) == 1,
                trigger_expanded=inspection.get('expanded'), owned_menu_observed=observed_open,
                inspection_error=inspection.get('error'))
            if not observed_open and field == 'template' and len(fresh) == 1 and inspection.get('expanded') is False:
                retried = await field_action(tools, field, 'click', fresh[0]['reference'])
                if not retried.get('dropdown_open_verified'):
                    return retried
            elif not observed_open:
                return blocked(opened.get('error', 'OWNED_DROPDOWN_NOT_OPEN'),
                               trigger_inspection=inspection.get('error'),
                               trigger_expanded=inspection.get('expanded'))
    searched = False
    audience_search = getattr(tools, 'audience_option', None) is not None
    if field in {'tag', 'template'} or audience_search or not controls_for(tools, field, 'option'):
        searches = controls_for(tools, field, 'search')
        if not searches:
            searches = [item for item in controls_for(tools, field)
                        if item.get('tag') == 'INPUT' and item.get('type') in {'text', 'search'}]
        if len(searches) == 1:
            if field == 'template':
                focused = await field_action(tools, field, 'click', searches[0]['reference'])
                if not focused.get('focused'):
                    return focused
            search = await field_action(tools, field, 'search', searches[0]['reference'])
            if not search.get('ok'):
                return search
            searched = True
            await tools.observe()
        elif field in {'tag', 'template'} or audience_search:
            code = {'tag':'TAG_PICKER_SEARCH_NOT_UNIQUE','template':'CONTENT_OWNED_SEARCH_INPUT_NOT_UNIQUE'}
            return blocked(code.get(field, 'AUDIENCE_OWNED_SEARCH_INPUT_NOT_UNIQUE'), found=len(searches))
    if field == 'template':
        return await choose_settled_template(tools)
    # Read-only wait for the options of the already-open, owned picker.
    for attempt in range(3):
        choices = controls_for(tools, field, 'option')
        if field == 'tag' and (choices or attempt == 2):
            tools.browser.trace.agent_event('page1_tag_picker_evidence', role='observer',
                search_verified=searched, exact_action_count=len(choices),
                evidence=(tools.last_observation or {}).get('scoped_option_evidence', {}))
        if len(choices) == 1 and has_verified_open(tools, field):
            return await field_action(tools, field, 'select', choices[0]['reference'])
        if len(choices) > 1:
            return blocked('EXACT_EXISTING_OPTION_NOT_UNIQUE', exact_option_count=len(choices))
        if attempt < 2:
            await asyncio.sleep(.4)
            await tools.observe()
    return blocked('OWNED_MENU_EXACT_OPTION_NOT_OBSERVED', searched=searched,
                   option_evidence=(tools.last_observation or {}).get('scoped_option_evidence', {}))


async def apply_known_field(tools, field):
    choices = controls_for(tools, field)
    if len(choices) != 1:
        reason = (tools.last_observation or {}).get('binding_failures', {}).get(field)
        return blocked(reason or 'CURRENT_ENABLED_CONTROL_NOT_UNIQUE', control_count=len(choices))
    control = choices[0]
    kind = tools.binding(field).kind
    if kind == 'text' and field == 'campaign_name':
        return await field_action(tools, field, 'type', control['reference'])
    if kind in {'combobox', 'native_select'} and field in {'waba', 'business_number', 'tag'}:
        return await choose_dropdown(tools, field, control)
    return blocked('FIELD_CONTROL_KIND_NOT_SUPPORTED', control_kind=kind)


async def run_page1_fields(tools, recover):
    from modules.whatsapp.workflow import setup_attempt_verdict

    trace = tools.browser.trace
    rows = {}
    for field in PAGE1_SETUP_FIELDS:
        if tools.terminal or tools.pending_write:
            break
        tools.mutations = 0
        tools.page1_current_field = field
        first_event = len(tools.events)
        # Values remain in the immutable payload and do not enter the model context.
        approved = getattr(tools.payload, field)
        prior = tools.last_independent_readback.get('fields', {})
        dependency = ('campaign_name' if field != 'campaign_name' and not prior.get('campaign_name') else
                      'waba' if field == 'business_number' and not prior.get('waba') else None)
        trace.agent_event('page1_current_field', role='controller', field=field, verified_fields=[
            name for name, ok in prior.items() if ok is True], value_type=type(approved).__name__)
        if dependency:
            result = blocked('SETUP_FIELD_DEPENDENCY_NOT_VERIFIED', dependency=dependency)
        else:
            if field == 'business_number':
                await tools.observe()
            result = await apply_known_field(tools, field)
            tag_picker_decided = field == 'tag' and result.get('error') in {
                'OWNED_MENU_EXACT_OPTION_NOT_OBSERVED', 'EXACT_EXISTING_OPTION_NOT_UNIQUE',
                'EXACT_EXISTING_TAG_ADD_CONTROL_NOT_BOUND',
            }
            if (not result.get('ok') and not tag_picker_decided and not tools.terminal
                    and not tools.pending_write and tools.mutations == 0):
                await recover(field, result)
                verified_after_recovery = tools.last_independent_readback.get('fields', {}).get(field) is True
                if (not verified_after_recovery and not tools.terminal and not tools.pending_write
                        and tools.mutations == 0):
                    prepared = [e for e in tools.events[first_event:] if e.get('field') == field and e.get('ok')]
                    if any(e.get('action') == 'search' for e in prepared):
                        # Only finish the preparation the Doer actually completed.
                        await tools.observe()
                        options = controls_for(tools, field, 'option')
                        if len(options) == 1 and has_verified_open(tools, field):
                            result = await field_action(tools, field, 'select', options[0]['reference'])
                        elif field == 'tag':
                            result = blocked('OWNED_MENU_EXACT_OPTION_NOT_OBSERVED', searched=True,
                                option_evidence=(tools.last_observation or {}).get('scoped_option_evidence', {}))
                    elif any(e.get('action') == 'click' and e.get('result', {}).get('dropdown_open_verified')
                             for e in prepared):
                        fresh = controls_for(tools, field)
                        if len(fresh) == 1 and has_verified_open(tools, field):
                            result = await choose_dropdown(tools, field, fresh[0])
                    verified_after_recovery = tools.last_independent_readback.get('fields', {}).get(field) is True
                if not verified_after_recovery:
                    recent = [event for event in tools.events[first_event:]
                              if event.get('field') == field and event.get('error')]
                    if recent and not result.get('option_evidence'):
                        result = blocked(recent[-1]['error'])
                else:
                    result = {'ok': True, 'fields': {field: True}}
        events = tools.events[first_event:]
        verified = tools.last_independent_readback.get('fields', {}).get(field) is True
        wrote = any(e.get('action') in {'type', 'select'} and e.get('attempted') and e.get('ok')
                    and e.get('verification', {}).get('actual_match') is True for e in events)
        if field == 'campaign_name' and not wrote:
            verified = False
        row = {'field': field, 'action': 'WRITTEN' if wrote else 'ALREADY_MATCHED' if verified else 'UNRESOLVED',
               'readback': 'MATCH' if verified else 'UNRESOLVED',
               'reason': None if verified else result.get('error', 'FIELD_READBACK_NOT_ESTABLISHED'),
               'native_menu_opened': any(e.get('action') == 'click' and e.get('method') == 'native'
                   and e.get('result', {}).get('dropdown_open_verified') is True for e in events)}
        if dependency:
            row['dependency'] = dependency
        if not verified and result.get('option_evidence'):
            row['option_evidence'] = result['option_evidence']
        rows[field] = row
        trace.agent_event('page1_field_result', role='observer', **row)
        print('PAGE1_FIELD ' + json.dumps(row, sort_keys=True), flush=True)
    tools.page1_current_field = None
    facts = (tools.last_independent_readback if tools.terminal or tools.pending_write
             else await deterministic_readback(tools, fields=PAGE1_SETUP_FIELDS))
    verdict = setup_attempt_verdict(tools, facts)
    for field, row in rows.items():
        if row['readback'] == 'MATCH' and facts.get('fields', {}).get(field) is not True:
            row.update(readback='MISMATCH', reason='FINAL_SETUP_READBACK_CHANGED')
    verdict['page1_fields'] = rows
    if verdict['status'] != 'COMPLETE':
        code = tools.terminal or ('WRITE_UNCERTAIN_NO_RETRY' if tools.pending_write else 'STUCK')
        verdict['status'] = 'BLOCKED' if tools.terminal or tools.pending_write else 'STUCK'
        verdict['failure'] = {'code': code, 'owner': getattr(tools, 'terminal_owner', 'recognition')
                              if tools.terminal else 'recognition',
                              'unsafe': bool(tools.pending_write or getattr(tools, 'terminal_unsafe', False))}
    trace.agent_event('page1_final_readback', role='observer', **verdict)
    print('PAGE1_FINAL_READBACK ' + json.dumps(rows, sort_keys=True), flush=True)
    return verdict



async def close_owned_audience_menu(tools, field):
    document = await tools.protect()
    binding = await tools.verify_binding(field)
    page = tools.browser.page
    script = r"""(s=>{
      const matches=document.querySelectorAll(s.q), e=matches.length===1?matches[0]:null;
      if(!e)return {status:'UNBOUND'};
      const ids=((e.getAttribute('aria-controls')||'')+' '+(e.getAttribute('aria-owns')||'')).split(/\s+/).filter(Boolean);
      const oldPanels=[...new Set([...s.previous,...ids])].map(id=>document.getElementById(id)).filter(Boolean);
      const visible=n=>n.getClientRects().length>0&&getComputedStyle(n).visibility==='visible'&&
        !n.closest('[hidden],[aria-hidden=true],[inert]');
      if(e.getAttribute('aria-expanded')!=='true')
        return {status:oldPanels.some(visible)?'PANEL_STILL_VISIBLE':'CLOSED',ids};
      if(!ids.some(id=>document.getElementById(id)?.getClientRects().length))return {status:'UNBOUND',ids};
      if(s.focus&&e.tabIndex>=0)e.focus({preventScroll:true});
      const active=document.activeElement;
      const owned=active===e||ids.some(id=>document.getElementById(id)?.contains(active));
      return {status:owned?'OWNED_FOCUS':'FOCUS_OUTSIDE_MENU',ids};
    })("""
    async def read_state(previous=(), focus=False):
        settings = {'q': binding.selector, 'previous': previous, 'focus': focus}
        return await page.evaluate(script + json.dumps(settings) + ')')

    approaches = []
    facts = await read_state()
    status = facts['status']
    ids = facts.get('ids', ())
    if status == 'FOCUS_OUTSIDE_MENU' and await tools.protect() == document:
        status = (await read_state(ids, focus=True))['status']
    if status == 'OWNED_FOCUS':
        if await tools.protect() != document or (await read_state(ids))['status'] != 'OWNED_FOCUS':
            raise V2Stop('AUDIENCE_MENU_FOCUS_CHANGED', owner='recognition')
        await page.key_press('Escape')
        approaches.append({'method':'escape','status':'DISPATCHED'})
    if status in {'OWNED_FOCUS', 'PANEL_STILL_VISIBLE'}:
        for _ in range(6):
            if await tools.protect() != document:
                raise V2Stop('AUDIENCE_MENU_DOCUMENT_CHANGED', owner='browser', unsafe=True)
            status = (await read_state(ids))['status']
            if status == 'CLOSED':
                break
            await asyncio.sleep(.15)
    if approaches:
        approaches[-1]['results_hidden'] = status=='CLOSED'
    if status == 'OWNED_FOCUS':
        from browser.observed_control import observed_node
        backdrop_script = r'''(s=>{
          const triggers=document.querySelectorAll(s.q), trigger=triggers.length===1?triggers[0]:null;
          if(!trigger||trigger.getAttribute('aria-expanded')!=='true')return null;
          const live=((trigger.getAttribute('aria-controls')||'')+' '+(trigger.getAttribute('aria-owns')||''))
            .split(/\s+/).filter(Boolean);
          const panels=s.ids.filter(id=>live.includes(id)).map(id=>document.getElementById(id)).filter(p=>
            p?.getClientRects().length&&getComputedStyle(p).visibility==='visible');
          if(panels.length!==1)return null;
          const pane=panels[0].closest('.cdk-overlay-pane');
          const container=pane?.closest('.cdk-overlay-container');
          let owner=pane;
          while(owner&&owner.parentElement!==container)owner=owner.parentElement;
          const backdrop=owner?.previousElementSibling;
          if(!container||!backdrop?.matches('.cdk-overlay-backdrop.cdk-overlay-backdrop-showing')||
             !backdrop.getClientRects().length)return null;
          const selector='.cdk-overlay-container > :nth-child('+([...container.children].indexOf(backdrop)+1)+')';
          const found=document.querySelectorAll(selector);
          if(found.length!==1||found[0]!==backdrop)return null;
          const rect=backdrop.getBoundingClientRect();
          const left=Math.max(0,rect.left),right=Math.min(innerWidth,rect.right);
          const top=Math.max(0,rect.top),bottom=Math.min(innerHeight,rect.bottom);
          if(right-left<4||bottom-top<4)return null;
          for(const f of [.1,.9,.5]){
            const x=left+(right-left)*f,y=top+(bottom-top)*f;
            if(document.elementFromPoint(x,y)===backdrop)return {selector,x,y};
          }
          return null;
        })(SETTINGS)'''.replace('SETTINGS',json.dumps({'q':binding.selector,'ids':ids}))
        hit = await page.evaluate(backdrop_script)
        if hit:
            identity = await observed_node(tools.browser, hit['selector'])
            if (await tools.protect() != document or await observed_node(tools.browser, hit['selector']) != identity
                    or await page.evaluate(backdrop_script) != hit):
                raise V2Stop('AUDIENCE_MENU_BACKDROP_CHANGED', owner='recognition')
            await page.click(hit['x'], hit['y'])
            for _ in range(6):
                if await tools.protect() != document:
                    raise V2Stop('AUDIENCE_MENU_DOCUMENT_CHANGED', owner='browser', unsafe=True)
                status = (await read_state(ids))['status']
                if status == 'CLOSED':
                    break
                await asyncio.sleep(.15)
            approaches.append({'method':'owned_backdrop','status':'DISPATCHED','results_hidden':status=='CLOSED'})
        else:
            approaches.append({'method':'owned_backdrop','status':'NO_PROVEN_HIT'})
    tools.browser.trace.agent_event('campaign_audience_menu_close', role='observer', field=field, status=status,
                                    results_hidden=status=='CLOSED', approaches=approaches)
    if status != 'CLOSED':
        raise V2Stop('AUDIENCE_OWNED_MENU_CLOSE_NOT_VERIFIED', owner='recognition')
    await tools.observe()


async def select_audience_set(tools, field):
    selected = await deterministic_readback(tools, fields=(field,), operator_details=field=='exclude_audience')
    unexpected = selected.get('terminal_unexpected', {}).get(field, [])
    if unexpected:
        print('AUDIENCE_UNEXPECTED_EXCLUSIONS ' + json.dumps(unexpected), flush=True)
        return blocked('AUDIENCE_UNEXPECTED_EXCLUSIONS_OPERATOR_INPUT_REQUIRED', observed_count=len(unexpected))
    if selected['fields'].get(field):
        if field == 'include_audience':
            await close_owned_audience_menu(tools, field)
            state = await deterministic_readback(tools, fields=(field,))
            if not state['fields'].get(field):
                return blocked('AUDIENCE_INCLUDE_READBACK_CHANGED_AFTER_CLOSE')
        else:
            from modules.whatsapp.observer import verify_audience_exclusion_summary
            tools.browser.trace.agent_event('campaign_exclude_chip_verified', role='observer',
                                            status='VERIFIED_EXISTING', count=len(tools.payload.audience_values(field)))
            summary = await verify_audience_exclusion_summary(tools, reveal=True)
            print('AUDIENCE_EXCLUSION_SUMMARY ' + json.dumps(summary), flush=True)
        return {'ok': True, 'changed': False}
    changed = False
    expected = tools.payload.audience_values(field)
    for value in expected:
        tools.audience_option = (field, value)
        try:
            tools.mutations = 0
            await tools.observe(strategy='dom')
            state = await deterministic_readback(tools, fields=(field,))
            if state['fields'].get(field):
                continue
            choices = controls_for(tools, field)
            if len(choices) != 1:
                reason = (tools.last_observation or {}).get('binding_failures', {}).get(field)
                return blocked(reason or 'CURRENT_ENABLED_CONTROL_NOT_UNIQUE', control_count=len(choices))
            if tools.binding(field).kind != 'combobox':
                return blocked('AUDIENCE_OWNED_PICKER_KIND_NOT_SUPPORTED')
            result = await choose_dropdown(tools, field, choices[0])
            if not result.get('ok'):
                return result
            changed = True
        finally:
            tools.audience_option = None
    if changed:
        await close_owned_audience_menu(tools, field)
    state = await deterministic_readback(tools, fields=(field,))
    proof = state.get('selected_sets', {}).get(field)
    if proof is None:
        reason = (tools.last_observation or {}).get('binding_failures', {}).get(field)
        return blocked(reason or 'AUDIENCE_SELECTION_STATE_UNOBSERVED')
    if not state['fields'].get(field):
        return blocked('AUDIENCE_SELECTED_SET_MISMATCH', observed_count=proof['count'])
    if field == 'exclude_audience':
        from modules.whatsapp.observer import verify_audience_exclusion_summary
        summary = await verify_audience_exclusion_summary(tools, reveal=True)
        print('AUDIENCE_EXCLUSION_SUMMARY ' + json.dumps(summary), flush=True)
    return {'ok': True, 'changed': changed}


async def run_campaign_fields(tools, stage):
    from modules.whatsapp.page1_transition import campaign_content_requirements, verify_active_campaign_stage

    await verify_active_campaign_stage(tools, stage)
    fields = STAGE_FIELDS[stage]
    verified = {}
    for field in fields:
        tools.page1_current_field = field
        tools.mutations = 0
        try:
            await tools.observe(strategy='dom')
            readback = await deterministic_readback(tools, fields=(field,))
            result = {'ok': True}
            action = 'ALREADY_MATCHED'
            if stage == 'audience' and field in {'include_audience', 'exclude_audience'}:
                result = await select_audience_set(tools, field)
                action = 'UNRESOLVED'
                if result.get('ok'):
                    action = 'SELECTED' if result.get('changed') else 'VERIFIED_EXISTING'
                if result.get('ok'):
                    readback = await deterministic_readback(tools, fields=(field,))
            elif not readback['fields'].get(field):
                choices = controls_for(tools, field)
                if len(choices) != 1:
                    reason = (tools.last_observation or {}).get('binding_failures', {}).get(field)
                    result = blocked(reason or 'CURRENT_ENABLED_CONTROL_NOT_UNIQUE', control_count=len(choices))
                elif tools.binding(field).kind == 'radio':
                    result = await field_action(tools, field, 'select', choices[0]['reference'])
                elif tools.binding(field).kind in {'combobox', 'native_select'}:
                    result = await choose_dropdown(tools, field, choices[0])
                else:
                    result = blocked('FIELD_CONTROL_KIND_NOT_SUPPORTED')
                action = 'SELECTED' if result.get('ok') else 'UNRESOLVED'
                if result.get('ok'):
                    readback = await deterministic_readback(tools, fields=(field,))
            matched = result.get('ok') and readback['fields'].get(field) is True
            report = {'field': field, 'action': action, 'readback': 'MATCH' if matched else 'UNRESOLVED',
                      'reason': None if matched else result.get('error', 'FIELD_READBACK_NOT_ESTABLISHED')}
            tools.browser.trace.agent_event('campaign_field_readback', role='observer', campaign_step=stage, **report)
            effective = (tools.payload.audience_values(field)
                         if field in {'include_audience', 'exclude_audience'} else getattr(tools.payload, field))
            print('CAMPAIGN_FIELD ' + json.dumps({**report, 'approved_value': effective,
                  'observed_count': result.get('observed_count')}), flush=True)
            if not matched:
                if stage == 'audience':
                    pickers = (tools.last_observation or {}).get('audience_picker_evidence', [])
                    sections = (tools.last_observation or {}).get('audience_section_evidence', {})
                    print('AUDIENCE_PICKER_EVIDENCE ' + json.dumps({'pickers': pickers, 'sections': sections}), flush=True)
                raise V2Stop(report['reason'], owner='recognition', unsafe=bool(tools.pending_write))
            verified[field] = effective
        finally:
            tools.page1_current_field = None
    facts = await deterministic_readback(tools, fields=fields)
    if not facts.get('stage_visible') or not all(facts['fields'].get(field) for field in fields):
        raise V2Stop('CAMPAIGN_FINAL_READBACK_CHANGED', owner='recognition')
    if stage == 'content':
        for _ in range(6):
            if facts.get('checks', {}).get('template_preview_visible'):
                break
            await asyncio.sleep(.5)
            await tools.observe()
            facts = await deterministic_readback(tools, fields=fields)
        if not all(facts['fields'].get(field) for field in fields):
            raise V2Stop('CONTENT_CLOSED_TEMPLATE_READBACK_CHANGED', owner='recognition')
        requirements = await campaign_content_requirements(tools)
        if requirements:
            print('CONTENT INPUT REQUIRED. Provide the approved values for: ' + ', '.join(requirements), flush=True)
            tools.browser.trace.agent_event('campaign_content_inputs_missing', role='observer',
                                            required_variable_count=len(requirements))
            raise V2Stop('CONTENT_TEMPLATE_VARIABLE_INPUTS_REQUIRED', owner='recognition')
        if not facts.get('checks', {}).get('template_preview_visible'):
            raise V2Stop('CONTENT_RENDERED_PREVIEW_NOT_VERIFIED', owner='recognition')
    if stage == 'audience':
        for field in ('include_audience', 'exclude_audience'):
            verified[field] = facts['selected_sets'][field]['matched']
    print('CAMPAIGN_STEP_VERIFIED ' + json.dumps({'step': stage, 'values': verified}), flush=True)
    return facts
