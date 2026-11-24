"""Read-only reconciliation of the observed WhatsApp draft attempt."""
import asyncio
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from modules.whatsapp.browser_baseline import reject_security_negatives
from modules.whatsapp.contract import SETUP_URL, V2Stop
from modules.whatsapp.destination import campaign_step_route, cdp_frame_url
from modules.whatsapp.page1_transition import CAMPAIGN_DOM
from modules.whatsapp.schedule_draft import _parse_display, _zone_matches


def prior_save_timestamp(artifact_root, run_id):
    return prior_save_source(artifact_root, run_id)[0]


def prior_save_source(artifact_root, run_id):
    if not re.fullmatch('[0-9a-f]{32}', run_id):
        raise V2Stop('RECONCILIATION_RUN_ID_INVALID', owner='authorization')
    path = Path(artifact_root) / ('login-whatsapp-' + run_id) / 'terminal-events.jsonl'
    if not path.is_file():
        raise V2Stop('RECONCILIATION_SOURCE_TRACE_MISSING', owner='authorization')
    events = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    clicks = [event for event in events
              if event.get('kind') == 'schedule_draft_click_result'
              and event.get('click_result') == 'DISPATCHED' and event.get('run_id') == run_id]
    if len(clicks) != 1:
        raise V2Stop('RECONCILIATION_PRIOR_SAVE_CLICK_NOT_UNIQUE', owner='authorization')
    click = clicks[0]
    timestamp = datetime.fromisoformat(click['timestamp'])
    grants = [event for event in events if event.get('kind') == 'draft_approval_bound'
              and event.get('run_id') == run_id]
    if not grants:
        return timestamp, None
    if len(grants) != 1 or grants[0].get('iteration') != click.get('iteration'):
        raise V2Stop('RECONCILIATION_SOURCE_DRAFT_APPROVAL_AMBIGUOUS', owner='authorization')
    grant = grants[0]
    digest = grant.get('payload_sha256')
    if (not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest)
            or datetime.fromisoformat(grant['timestamp']) >= timestamp):
        raise V2Stop('RECONCILIATION_SOURCE_DRAFT_APPROVAL_INVALID', owner='authorization')
    return timestamp, digest


CAMPAIGN_LIST = r"""(s=>{""" + CAMPAIGN_DOM + r"""
 const name=e=>String(e.innerText??e.textContent??'').replace(/\s+/g,' ').trim();
 const attr=e=>e.getAttribute('aria-label')||e.getAttribute('placeholder')||e.getAttribute('title')||
   [...(e.labels||[])].map(n=>name(n)).join(' ')||'';
 const isSearch=text=>/^(?:search(?: campaigns?(?: by name)?| by campaign name)?|campaign name)$/i
   .test(text.trim().replace(/(?:\.{3}|\u2026)\s*$/,''));
 const buttonName=e=>attr(e)||name(e);
 const buttonScope=[...document.querySelectorAll('main,[role=main]')].filter(visible);
 const shell=buttonScope.length===1?buttonScope[0]:document.body;
 const hasSearchIcon=e=>[...e.querySelectorAll('mat-icon,.material-icons,[data-icon],svg,use,i')].some(icon=>
   /^(?:search|magnifying-glass)$/i.test(norm(icon.getAttribute('data-icon')||name(icon)))||
   icon.matches('.fa-search,.icon-search,.search-icon')||icon.getAttribute('href')==='#search');
 const searchTriggers=[...document.querySelectorAll('button,[role=button]')]
   .filter(e=>actionable(e)&&!e.closest('form,[role=dialog],nav,[role=navigation]')&&
     ((/^search\b/i.test(buttonName(e))&&isSearch(buttonName(e)))||hasSearchIcon(e)));
 const cues=['campaign','campaigns','all campaigns','whatsapp','search','search campaigns','filter','draft','drafts','no campaigns'];
 const draftTabs=[...shell.querySelectorAll('[role=tab]')].filter(e=>actionable(e)&&
   norm(buttonName(e))==='drafts'&&e.getAttribute('aria-selected')==='false'&&
   !!e.getAttribute('aria-controls')&&!e.matches('a[href],button[type=submit]'));
 const visibleCues=[...new Set([...shell.querySelectorAll('h1,h2,h3,h4,button,[role=button],[role=tab],a[href],label')]
    .filter(visible).map(e=>norm(buttonName(e))).filter(n=>cues.includes(n)))];
 const shape={known_visible_labels:visibleCues,visible_inputs:[...shell.querySelectorAll('input')].filter(visible).length,
   visible_frames:[...shell.querySelectorAll('iframe')].filter(visible).length,
   visible_headings:[...shell.querySelectorAll('h1,h2,h3,[role=heading]')].filter(visible).length,
   ready_state:document.readyState,
   campaigns_heading:[...document.querySelectorAll('h1,h2,h3,h4,[role=heading]')].some(e=>
     visible(e)&&/^(?:all |whatsapp )?campaigns$/i.test(name(e)))};
 const headers=(root)=>[...root.querySelectorAll('thead th,[role=columnheader],mat-header-cell')]
    .filter(visible).map(e=>norm(name(e)));
 const tableNodes=[...document.querySelectorAll('table,[role=table],[role=grid],mat-table')].filter(visible);
 const tables=tableNodes.filter(t=>headers(t).some(h=>['campaign name','name','campaign'].includes(h)));
 const scope=tables.length===1?tables[0]:tables.length===0&&tableNodes.length===1?tableNodes[0]:null;
 const editable=[...shell.querySelectorAll('input[type=search],input[type=text],input:not([type]),[role=searchbox]')]
    .filter(e=>e.tagName==='INPUT'&&actionable(e)&&!e.readOnly&&
      !e.closest('tr,[role=row],[role=dialog]'));
 const namedInputs=editable.filter(e=>isSearch(attr(e)));
 const outsideTable=scope?editable.filter(e=>!scope.contains(e)):[];
 const inputs=namedInputs.length?namedInputs:
   tables.length===1&&visibleCues.includes('campaigns')&&outsideTable.length===1?outsideTable:[];
 shape.non_row_editable_inputs=editable.length;
 shape.outside_table_editable_inputs=outsideTable.length;
 shape.search_basis=inputs===namedInputs?'EXPLICIT_LABEL':inputs.length===1?'UNIQUE_CAMPAIGN_TABLE_FILTER':'UNRESOLVED';
 const roots=scope?[scope]:[...document.querySelectorAll('main,[role=main]')].filter(visible);
 const rowNodes=roots.flatMap(root=>[...root.querySelectorAll('tr,[role=row],mat-row')]).filter(visible);
 const emptyLabel=/^(no (campaigns|results|records|data)( found| available)?|no matching (campaigns|records))\.?$/i;
 const rawRows=[...new Set(rowNodes)].filter(e=>!emptyLabel.test(name(e))&&
   !e.querySelector('th,[role=columnheader],mat-header-cell'));
 const rows=rawRows.filter(e=>!rawRows.some(other=>other!==e&&e.contains(other)));
 const columnNames=scope?headers(scope):[];
 shape.table_headers=columnNames.filter(value=>/^(campaign(?: name| id| channel)?|name|id|status|channel|type|created(?: (?:by|on|at|date|time))?|last modified|updated|actions?)$/i.test(value));
 const index=(pattern)=>columnNames.findIndex(h=>pattern.test(h));
 const matches=[];
 for(const row of rows){
   const cells=[...row.querySelectorAll('td,[role=cell],[role=gridcell],mat-cell')].filter(visible);
   const i=index(/^(campaign name|name|campaign)$/);
   const exactName=e=>e&&(name(e)===s.name||[...e.querySelectorAll('a[href],span')].some(n=>
     visible(n)&&name(n)===s.name));
   const named=i>=0?exactName(cells[i]):cells.some(e=>name(e)===s.name);
   if(!named)continue;
   const cell=(p)=>{const n=index(p);return n>=0&&cells[n]?name(cells[n]):null};
   const links=[...row.querySelectorAll('a[href]')].filter(visible).filter(e=>{
     const n=norm(e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText);
     return ['view','view details','details','preview','view campaign'].includes(n)||name(e)===s.name;
   }).map(e=>e.href);
   const controls=[...row.querySelectorAll('button,[role=button]')].filter(visible)
     .map(e=>norm(e.getAttribute('aria-label')||e.getAttribute('title')||e.innerText))
     .filter(n=>['view','view details','details','preview','view campaign','edit','more actions'].includes(n));
   const channel=cell(/^(channel|campaign channel|communication channel|type)$/);
   const namedChannel=[...row.querySelectorAll('[aria-label],[title],img[alt]')].some(e=>visible(e)&&
     /^whats\s*app(?: campaign)?$/i.test((e.getAttribute('aria-label')||e.getAttribute('title')||e.getAttribute('alt')||'').trim()));
   const filteredChannel=[...document.querySelectorAll('[role=tab][aria-selected=true],h1,h2,[role=heading]')]
     .some(e=>visible(e)&&/^whats\s*app(?: campaigns?)?$/i.test(name(e)));
   matches.push({id:cell(/^(campaign id|id|id no\.?)$/),status:cell(/^status$/),channel,
       whatsapp_channel:!!(namedChannel||filteredChannel||/^whats\s*app(?: campaign)?$/i.test(channel||'')),
       created:cell(/^(created(?: (?:at|on|date|time))?|creation date)(?: \((?:ist|utc)\))?$/),
       updated:cell(/^(updated(?: (?:at|on|date|time))?|last (?:modified|updated)(?: on)?)(?: \((?:ist|utc)\))?$/),
       links,controls});
 }
 const pagers=[...document.querySelectorAll('button,[role=button],a[href]')].filter(e=>actionable(e)&&
   /^(next page|go to next page)$/i.test(attr(e).trim()));
 const menuLinks=[...document.querySelectorAll('a[href]')].filter(e=>{
   if(!visible(e)||e.closest('[aria-disabled=true],[inert]')||e.hasAttribute('download')||
      !['','_self'].includes(e.getAttribute('target')||''))return false;
   const u=new URL(e.href);
   return u.origin===location.origin&&u.pathname===s.campaignsPath&&!u.search&&!u.hash&&
     /^(?:all )?campaigns?$/i.test(norm(buttonName(e)));
 });
 const sidebar='nav,aside,[role=navigation],[role=menu]';
 const sideLinks=menuLinks.filter(e=>e.closest(sidebar));
 const engage=[...document.querySelectorAll('button,[role=button],a[href],[role=menuitem]')].filter(e=>
   visible(e)&&e.closest(sidebar)&&!e.closest('[aria-disabled=true],[inert]')&&!e.disabled&&
   !e.matches('button[type=submit]')&&(!e.hasAttribute('href')||e.getAttribute('href')==='#')&&
   /^engage$/i.test(norm(buttonName(e))));
 const busy=[...document.querySelectorAll('[aria-busy=true],mat-spinner,[role=progressbar]')].some(visible)||
   [...shell.querySelectorAll('[role=status],p,span')].some(e=>visible(e)&&/^loading(?: campaigns)?[.…]*$/i.test(name(e)));
 const emptyLabels=[...shell.querySelectorAll('p,td,[role=status],span')].filter(visible).map(name);
 const explicitlyEmptyCampaigns=emptyLabels.some(value=>/^(no campaigns( found| available)?|no matching campaigns)\.?$/i.test(value));
 const noResults=emptyLabels.some(value=>emptyLabel.test(value));
 return {table_count:tables.length,visible_rows:rows.length,search_count:inputs.length,busy,no_results:noResults,
   drafts_tab:draftTabs.length===1?cssPath(draftTabs[0]):null,
   explicitly_empty_campaigns:explicitlyEmptyCampaigns,
   further_pages:!!pagers.length,search:inputs.length===1?cssPath(inputs[0]):null,
   campaigns_link_count:menuLinks.length,campaigns_link:menuLinks.length===1?cssPath(menuLinks[0]):null,
   sidebar_campaigns_link:sideLinks.length===1?cssPath(sideLinks[0]):null,
   engage_count:engage.length,engage:engage.length===1?cssPath(engage[0]):null,
   search_trigger_count:searchTriggers.length,search_trigger:searchTriggers.length===1?cssPath(searchTriggers[0]):null,shape,
   search_value:inputs.length===1?inputs[0].value:null,matches,
   time_column_zone:columnNames.some(n=>n.endsWith('(ist)'))?'IST':columnNames.some(n=>n.endsWith('(utc)'))?'UTC':null};
})(SETTINGS)"""


DETAIL = r"""(s=>{
 const visible=e=>e.getClientRects().length&&getComputedStyle(e).visibility==='visible'&&!e.closest('[aria-hidden=true],[inert]');
 const norm=t=>String(t??'').replace(/\s+/g,' ').trim();
 const scope=[...document.querySelectorAll('main,[role=main],mat-dialog-container,[role=dialog]')].filter(visible);
 const roots=scope.filter(e=>!scope.some(n=>n!==e&&e.contains(n)));
 const root=roots.length===1?roots[0]:roots.length===0?document.body:null;
 if(!root)return {scope_count:roots.length};
 const labels=[...root.querySelectorAll('span,div,p,dd,h1,h2,h3,label,dt,td')].filter(e=>visible(e)&&
   !e.closest('button,[role=button],a[href]')&&![...e.children].some(c=>visible(c)&&norm(c.innerText)));
 const nameLabels=labels.filter(e=>norm(e.innerText)===s.name);
 const present=nameLabels.length>0;
 const accessibleName=e=>norm(e.getAttribute('aria-label')||e.innerText);
 const hasPreviewName=e=>/^(?:campaign |whatsapp campaign )?preview$/i.test(accessibleName(e));
 const previewVisible=hasPreviewName(root)||labels.some(hasPreviewName)||
   [...root.querySelectorAll('[role=heading],[role=dialog],[aria-label]')]
     .some(e=>visible(e)&&!e.closest('button,[role=button],a[href]')&&hasPreviewName(e));
 const nearby=(pattern,shape)=>{
   const result=new Set();
   for(const label of labels){
     const text=norm(label.innerText);
     const inline=text.match(pattern);
     if(inline?.[1]&&shape.test(inline[1]))result.add(inline[1]);
     if(!inline||inline[1])continue;
     const next=label.nextElementSibling;
     if(next&&visible(next)&&shape.test(norm(next.innerText)))result.add(norm(next.innerText));
     const enclosing=norm(label.parentElement?.innerText).match(pattern);
     if(enclosing?.[1]&&shape.test(enclosing[1]))result.add(enclosing[1]);
   }
   return [...result];
 };
 const ids=nearby(/^(?:campaign id|record id)\s*:?\s*(.*)$/i,/^[A-Za-z0-9_-]{1,80}$/);
 const statuses=nearby(/^status\s*:?\s*(.*)$/i,/^[A-Za-z][A-Za-z _-]{0,30}$/);
 const savedValues=(names)=>{
   const values=new Set();
   for(const label of labels){
     if(label.closest('nav,[role=navigation],[role=alert],[role=status],form,[contenteditable]'))continue;
     const text=norm(label.innerText);
     const separator=text.indexOf(':');
     const key=norm(separator<0?text:text.slice(0,separator)).toLowerCase();
     if(!names.includes(key))continue;
     const next=label.nextElementSibling;
     const value=norm(separator<0?'':text.slice(separator+1))||
       (next&&visible(next)&&!next.matches('input,select,textarea,button,a,[role=button],[contenteditable]')&&
        !next.querySelector('input,select,textarea,button,a,[role=button],[contenteditable]')?norm(next.innerText):'');
     if(value&&value.length<=256)values.add(value);
   }
   return [...values];
 };
 // A badge paired with the exact rendered campaign name is status evidence.
 // A toast or a separate Drafts navigation control is not record status.
 const draftBadge=nameLabels.some(label=>{
   for(let parent=label.parentElement, depth=0;parent&&parent!==root&&depth<3;parent=parent.parentElement,depth++){
     if(norm(parent.innerText).length>s.name.length+64||parent.querySelector('input,[role=tab]'))return false;
     if(labels.some(e=>e!==label&&parent.contains(e)&&/^draft$/i.test(norm(e.innerText))&&
                    !e.closest('[role=alert],[role=status],nav,[role=navigation]')))return true;
   }
   return false;
 });
 if(draftBadge&&!statuses.some(value=>/^draft$/i.test(value)))statuses.push('Draft');
 const datePattern=/\b(?:[A-Za-z]{3,9} \d{1,2}, \d{4}|\d{1,2} [A-Za-z]{3,9} \d{4}|\d{4}-\d\d-\d\d)[ T]+\d{1,2}:\d\d(?: [AP]M)?\b/ig;
 const zonePattern=/\b(?:[A-Za-z_]+\/[A-Za-z_]+|IST)\b/g;
 const scheduleLabels=labels.filter(e=>/^send later\b/i.test(norm(e.innerText)));
 const scheduleTexts=[];
 for(const marker of scheduleLabels){
   let current=marker;
   let observed='';
   for(let i=0;current&&i<5;i++,current=current.parentElement){
     if(current===root)break;
     const text=norm(current.innerText);
     if(text.length>500)break;
     if(text.match(datePattern)?.length){
       observed ||= text;
       if(text.match(datePattern).length===1&&text.match(zonePattern)?.length){observed=text;break;}
     }
   }
   if(observed)scheduleTexts.push(observed);
 }
 const dates=[...new Set(scheduleTexts.flatMap(text=>text.match(datePattern)||[]))];
 const zones=[...new Set(scheduleTexts.flatMap(text=>text.match(zonePattern)||[]))];
 return {scope_count:1,preview_visible:previewVisible,draft_badge:draftBadge,campaign_name_visible:present,ids,statuses,zones,dates,
   senders:savedValues(['sender','sender number','business number']),templates:savedValues(['template','template name']),
   send_later_label_visible:scheduleLabels.length>0};
})(SETTINGS)"""


def _display_timestamp(text, zone_hint):
    if not text:
        return None
    if re.search(r'(?:\bIST|\bUTC|[+-]\d\d:\d\d)$', text):
        try:
            return datetime.fromisoformat(text.replace(' IST', '+05:30').replace(' UTC', '+00:00'))
        except ValueError:
            pass
    for pattern in ('%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M','%b %d, %Y %I:%M %p','%d %b %Y %I:%M %p'):
        try:
            stamp = datetime.strptime(text, pattern)
        except ValueError:
            continue
        zone = {'IST':timezone(timedelta(hours=5,minutes=30)), 'UTC':timezone.utc}.get(zone_hint)
        return stamp.replace(tzinfo=zone) if zone else None
    return None


def select_record(matches, timestamp, zone_hint, *, campaign_id=None):
    if campaign_id:
        identified = [row for row in matches if row.get('id') == campaign_id]
    elif timestamp is None:
        return None
    else:
        identified = [row for row in matches if any(
            value and abs((value - timestamp).total_seconds()) <= 90
            for value in (_display_timestamp(row.get(key), zone_hint)
                          for key in ('created', 'updated')))]
    return identified[0] if len(identified) == 1 else None


def campaign_list_ready(state, *, allow_trigger=False):
    table_ready = state['table_count'] == 1 and bool(state['visible_rows'] or state['no_results'])
    empty_ready = state['table_count'] == 0 and state.get('explicitly_empty_campaigns') is True
    search_ready = state['search_count'] == 1 and bool(state['search'])
    trigger_ready = allow_trigger and state.get('search_trigger_count') == 1 and bool(state.get('search_trigger'))
    return (state['shape']['ready_state'] == 'complete' and not state['busy']
            and (table_ready or empty_ready) and (search_ready or trigger_ready))


def campaigns_handoff_facts(url, campaigns_path, state):
    """Reduce the read-only list observation to evidence safe for the terminal."""
    route = urlsplit(url)
    shape = state.get('shape', {})
    route_match = (route.scheme == 'https' and route.netloc == urlsplit(SETUP_URL).netloc
                   and route.path == campaigns_path and not route.fragment)
    return {
        'route': {'hostname': route.hostname, 'pathname': route.path, 'fragment_present': bool(route.fragment)},
        'route_match': route_match,
        'ready': route_match and campaign_list_ready(state, allow_trigger=True),
        'document_ready_state': shape.get('ready_state'),
        'campaigns_heading': shape.get('campaigns_heading', False),
        'table_headers': shape.get('table_headers', []),
        'loading': state.get('busy'),
        'table_count': state.get('table_count'),
        'visible_rows': state.get('visible_rows'),
        'explicitly_empty_campaigns': state.get('explicitly_empty_campaigns'),
        'search_count': state.get('search_count'),
        'search_trigger_count': state.get('search_trigger_count'),
    }


async def campaign_ax_signals(browser, session):
    """Keep only allowlisted accessibility roles and exact UI labels."""
    browser.budget()
    try:
        async with asyncio.timeout(2):
            tree = await browser.guard.send('Accessibility.getFullAXTree', {'depth': 12}, session)
    except (TimeoutError, RuntimeError) as error:
        return {'status': type(error).__name__}
    roles = {'heading', 'link', 'searchbox', 'textbox', 'table', 'grid', 'row', 'progressbar', 'alert', 'dialog'}
    cues = {'campaigns', 'all campaigns', 'whatsapp', 'whatsapp campaigns', 'search', 'search campaigns',
            'preview', 'campaign preview', 'whatsapp campaign preview', 'campaign id', 'status',
            'draft', 'drafts', 'send later', 'when to send', 'no campaigns', 'no records found',
            'access denied', 'page not found', 'something went wrong'}
    counts = {}
    labels = set()
    for node in tree.get('nodes', []):
        if node.get('ignored'):
            continue
        role = (node.get('role') or {}).get('value')
        if role in roles:
            counts[role] = counts.get(role, 0) + 1
        name = (node.get('name') or {}).get('value')
        if isinstance(name, str):
            normalized = name.strip().rstrip(':').casefold()
            if normalized in cues:
                labels.add(normalized)
    return {'status': 'OBSERVED', 'role_counts': counts, 'known_labels': sorted(labels)}


async def resolve_draft_name_check(status, confirm_unused):
    if status not in {'CAMPAIGN_LIST_READINESS_FAILED', 'CAMPAIGN_SEARCH_CONTROL_NOT_ACTIONABLE',
                      'CAMPAIGN_SEARCH_INPUT_NOT_VISIBLE', 'CAMPAIGN_SEARCH_TRIGGER_NOT_ACTIONABLE'}:
        return status
    answer = await confirm_unused()
    return {'yes': 'CAMPAIGN_NAME_OPERATOR_CONFIRMED_UNUSED',
            'no': 'CAMPAIGN_NAME_OPERATOR_DECLINED',
            'timeout': 'CAMPAIGN_NAME_OPERATOR_CONFIRMATION_TIMEOUT'}[answer]


async def reconcile_campaign(browser, binding, payload, source_run, saved_at, *,
                             confirm=None, campaign_id=None, check_unused=False, diagnostic=None,
                             reload_once=False, require_configuration=False, announce=True, visible_duplicate=False):
    page, guard = browser.page, browser.guard
    entry = urlsplit(SETUP_URL)
    admin = entry.path.split('/campaign/', 1)[0]
    campaigns_path = admin + '/campaign/unified'
    roots = {admin, admin + '/'}
    preview_path = entry.path.split('/create/', 1)[0] + '/preview'

    def permitted(url, paths):
        try:
            current = urlsplit(url)
            return (current.scheme == 'https' and current.netloc == entry.netloc and not current.fragment
                    and current.path in paths and browser.policy.permits(url))
        except (ValueError,TypeError):
            return False

    def detail_route(url):
        return browser.policy.permits(url) and (permitted(url, {preview_path}) or campaign_step_route(url, 'preview'))

    async def checked(paths, *, detail=False):
        await binding.require_current(browser)
        url = await page.url()
        reply = await guard.send('Page.getFrameTree', session=binding.session)
        if (cdp_frame_url(reply.get('frameTree',{}).get('frame',{})) != url
                or not (detail_route(url) if detail else permitted(url, paths))):
            raise V2Stop('RECONCILIATION_PROTECTED_LIST_OR_RECORD_ROUTE_UNVERIFIED', owner='browser', unsafe=True)
        await reject_security_negatives(browser)
        return url

    def result(code, **facts):
        record={'status':code,'source_run':source_run,**facts}
        if saved_at is not None and not check_unused:
            record['prior_save_at'] = saved_at.isoformat()
        browser.trace.agent_event('campaign_reconciliation_result',role='observer',**record)
        if announce:
            print('CAMPAIGN_RECONCILIATION ' + json.dumps(record),flush=True)
        return record

    def list_paths(url):
        path = urlsplit(url).path
        relative = path.removeprefix(admin + '/campaign/')
        if (relative != path and relative and not any(
                re.search(r'^(?:create|edit|delete|preview|publish|schedule|send|activate|save)(?:$|[-_])',
                          part, re.I) for part in relative.split('/'))):
            return roots | {path}
        return roots

    settings = json.dumps({'name': payload.campaign_name, 'campaignsPath': campaigns_path})

    async def read_detail(row=None):
        for _ in range(9):
            url = await checked(None, detail=True)
            generation = guard.session_states[binding.session].document_generation
            browser.budget()
            detail = await page.evaluate(DETAIL.replace('SETTINGS', settings))
            if await checked(None, detail=True) != url or guard.session_states[binding.session].document_generation != generation:
                return result('CAMPAIGN_DETAIL_CHANGED_DURING_READBACK')
            has_configuration = detail.get('senders') and detail.get('templates')
            if (detail.get('campaign_name_visible') and detail.get('dates') and detail.get('zones')
                    and (has_configuration or not require_configuration)):
                break
            await asyncio.sleep(.5)
        ids, statuses = detail.get('ids', []), detail.get('statuses', [])
        row_id = row.get('id') if row else None
        expected_id = campaign_id or row_id
        if len(ids) > 1 or (expected_id and ids and ids != [expected_id]):
            return result('CAMPAIGN_ID_READBACK_CONFLICT', expected_id=expected_id, detail_ids=ids)
        observed_id = ids[0] if len(ids) == 1 else row_id
        row_status = row.get('status') if row else None
        status = statuses[0] if len(statuses) == 1 else row_status
        if not detail.get('campaign_name_visible') or not observed_id or not status:
            return result('CAMPAIGN_RECORD_IDENTITY_OR_STATUS_UNVERIFIED', campaign_id=observed_id,
                          netcore_status=status, campaign_name_visible=detail.get('campaign_name_visible', False))
        if statuses and (len(statuses) != 1 or row_status and row_status.strip().casefold() != status.strip().casefold()):
            return result('CAMPAIGN_STATUS_READBACK_CONFLICT', campaign_id=observed_id, list_status=row_status,
                          detail_statuses=statuses)
        if status.strip().casefold() != 'draft':
            return result('CAMPAIGN_DRAFT_STATUS_MISMATCH', campaign_id=observed_id, netcore_status=status)
        dates = {_parse_display(text) for text in detail.get('dates', [])} - {None}
        zones = detail.get('zones', [])
        if len(dates) != 1 or len(zones) != 1 or not detail.get('send_later_label_visible'):
            return result('PERSISTED_SEND_LATER_READBACK_UNAVAILABLE', campaign_id=observed_id, netcore_status=status,
                          parseable_dates=len(dates), timezone_count=len(zones),
                          send_later_label=detail.get('send_later_label_visible', False))
        date = next(iter(dates))
        expected = datetime.fromisoformat(payload.schedule_datetime)
        if expected.tzinfo is not None:
            expected = expected.astimezone(ZoneInfo(payload.timezone)).replace(tzinfo=None)
        facts = dict(campaign_id=observed_id, netcore_status='Draft', campaign_name=payload.campaign_name,
                     date=date.date().isoformat(), time=date.strftime('%H:%M'), timezone=zones[0],
                     id_source='DETAIL_DOM' if ids else 'DRAFT_ROW',
                     status_source='DETAIL_DOM' if statuses else 'DRAFT_ROW', schedule_source='SAVED_RECORD')
        if date != expected or not _zone_matches(zones[0], payload.timezone):
            return result('PERSISTED_SCHEDULE_DIFFERS', **facts)
        if not require_configuration:
            return result('PERSISTED_SCHEDULE_MATCH', **facts)
        senders, templates = detail.get('senders', []), detail.get('templates', [])
        if len(senders) != 1 or len(templates) != 1:
            return result('PERSISTED_SENDER_OR_TEMPLATE_UNRESOLVED', **facts,
                          sender_count=len(senders), template_count=len(templates))
        sender_match = senders[0] == payload.business_number
        template_match = templates[0] == payload.template
        if not (sender_match and template_match):
            return result('PERSISTED_SENDER_OR_TEMPLATE_MISMATCH', **facts,
                          sender_match=sender_match, template_match=template_match)
        return result('PERSISTED_DRAFT_MATCH', **facts, sender=senders[0], template=templates[0],
                      configuration_source='SAVED_RECORD')

    if campaign_id and confirm is None and detail_route(await page.url()):
        return await read_detail()

    async def snapshot():
        browser.budget()
        return await page.evaluate(CAMPAIGN_LIST.replace('SETTINGS', settings))

    async def observe_list():
        current = await page.url()
        checked_url = await checked(list_paths(current))
        state = await snapshot()
        if await checked(list_paths(current)) != checked_url:
            raise V2Stop('RECONCILIATION_LIST_CHANGED_DURING_READBACK', owner='browser', unsafe=True)
        if diagnostic:
            diagnostic.render(checked_url, state)
        return checked_url, state

    def readiness_fields(url, state):
        parts = urlsplit(url)
        return {'hostname': parts.hostname, 'pathname': parts.path, 'fragment_present': bool(parts.fragment),
                'load_finished': state['shape']['ready_state'] == 'complete' and not state['busy'],
                'table_count': state['table_count'], 'visible_rows': state['visible_rows'],
                'empty_state_visible': state['no_results'], 'search_count': state['search_count'],
                'search_trigger_count': state['search_trigger_count'],
                'campaigns_link_count': state['campaigns_link_count']}

    def ready(state):
        if diagnostic and (state.get('search_trigger_count') != 1 or not state.get('search_trigger')):
            return False
        return campaign_list_ready(state, allow_trigger=diagnostic is not None)

    def exact_id_visible(state):
        return (campaign_id is not None and not check_unused and not state['busy']
                and state['shape']['ready_state'] == 'complete' and state['table_count'] == 1
                and any(row['id'] == campaign_id for row in state['matches']))

    async def wait_for_list(attempts, predicate=ready):
        for _ in range(attempts):
            current, state = await observe_list()
            if predicate(state):
                break
            await asyncio.sleep(.4)
        return current, state

    current, state = await wait_for_list(20, lambda sample: exact_id_visible(sample) or ready(sample))
    if campaign_id and not diagnostic and not exact_id_visible(state) and state.get('drafts_tab'):
        selector = state['drafts_tab']
        fresh_url, fresh = await observe_list()
        tab = page.locator(selector)
        if (fresh_url == current and fresh.get('drafts_tab') == selector
                and await tab.count(timeout=2000) == 1 and await tab.is_visible(timeout=2000) is True):
            browser.trace.agent_event('campaign_list_navigation', role='controller', control='DRAFTS_TAB')
            await tab.click(timeout=3000)
            current, state = await wait_for_list(12, lambda sample: exact_id_visible(sample) or ready(sample))
    if reload_once and not ready(state):
        blank = not (state['table_count'] or state['visible_rows'] or state['no_results']
                     or state['search_trigger_count'] or state['search_count'])
        if blank:
            path = urlsplit(current).path
            if (path != campaigns_path or diagnostic.last_document_get_path != path
                    or await checked({campaigns_path}) != current):
                return result('CAMPAIGNS_RELOAD_OWNED_GET_DOCUMENT_UNVERIFIED', **readiness_fields(current, state))
            original_generation = guard.session_states[binding.session].document_generation
            diagnostic.phase = 'AFTER_RELOAD'
            diagnostic.mark('CAMPAIGNS_RELOAD_DISPATCHED')
            await guard.send('Page.reload', {'ignoreCache': False}, session=binding.session)
            for _ in range(20):
                await asyncio.sleep(.4)
                await binding.require_current(browser, allow_document_transition=True)
                owner = guard.session_states[binding.session]
                if owner.document_generation != original_generation and guard.primary_observable():
                    await checked({campaigns_path})
                    diagnostic.mark('CAMPAIGNS_RELOAD_DOCUMENT_COMMITTED')
                    current, state = await wait_for_list(20)
                    break
            else:
                return result('CAMPAIGNS_RELOAD_DOCUMENT_COMMIT_UNVERIFIED', **readiness_fields(current, state))
        else:
            diagnostic.mark('CAMPAIGNS_RELOAD_NOT_NEEDED_VIEW_NOT_BLANK')
    if diagnostic and not reload_once and not ready(state):
        async def click_observed(key):
            url, fresh = await observe_list()
            if url != current or not fresh.get(key):
                return False
            control = page.locator(fresh[key])
            if await control.count(timeout=2000) != 1 or await control.is_visible(timeout=2000) is not True:
                return False
            if await checked(list_paths(current)) != url:
                return False
            diagnostic.mark('ENGAGE_CLICK' if key == 'engage' else 'SIDEBAR_CAMPAIGNS_CLICK')
            await control.click(timeout=3000)
            return True

        diagnostic.phase = 'SIDEBAR_NAVIGATION'
        if state['sidebar_campaigns_link'] is None and state['engage'] and await click_observed('engage'):
            current, state = await wait_for_list(6, lambda sample: bool(sample['sidebar_campaigns_link']))
        if state['sidebar_campaigns_link'] and await click_observed('sidebar_campaigns_link'):
            diagnostic.phase = 'AFTER_SIDEBAR_CLICK'
            current, state = await wait_for_list(20)
        else:
            diagnostic.mark('SIDEBAR_CAMPAIGNS_CONTROL_UNAVAILABLE',
                            engage_count=state['engage_count'], campaigns_link_count=state['campaigns_link_count'])
    if (not diagnostic and not campaign_list_ready(state) and urlsplit(current).path in roots
            and state['campaigns_link']):
        selector = state['campaigns_link']
        link = page.locator(selector)
        fresh_url, fresh = await observe_list()
        if (fresh_url == current and fresh['campaigns_link'] == selector
                and await link.count(timeout=2000) == 1 and await link.is_visible(timeout=2000) is True):
            browser.trace.agent_event('campaign_list_navigation', role='controller', control='CAMPAIGNS_LINK')
            await link.click(timeout=3000)
            current, state = await wait_for_list(15)
    if (not diagnostic and not exact_id_visible(state) and not state['search_count']
            and state['search_trigger'] and state['table_count'] == 1):
        selector = state['search_trigger']
        trigger = page.locator(selector)
        fresh_url, fresh = await observe_list()
        if (fresh_url == current and fresh['search_trigger'] == selector
                and await trigger.count(timeout=2000) == 1 and await trigger.is_visible(timeout=2000) is True):
            await trigger.click(timeout=3000)
            current, state = await wait_for_list(8)
    if not ready(state) and not exact_id_visible(state):
        if visible_duplicate and check_unused and state['table_count'] == 1 and state['matches'] and not state['busy']:
            fresh_url, fresh = await observe_list()
            if fresh_url == current and fresh['table_count'] == 1 and not fresh['busy'] and fresh['matches'] == state['matches']:
                return result('CAMPAIGN_NAME_ALREADY_USED', exact_match_count=len(fresh['matches']),
                              source='VISIBLE_EXACT_CAMPAIGN_NAME_ROW', **readiness_fields(current, fresh))
        accessibility = await campaign_ax_signals(browser, binding.session)
        if await checked(list_paths(current)) != current:
            raise V2Stop('RECONCILIATION_LIST_CHANGED_DURING_READBACK', owner='browser', unsafe=True)
        return result('CAMPAIGN_LIST_READINESS_FAILED', **readiness_fields(current, state),
                      list_surface=state['shape'], accessibility=accessibility)

    if check_unused and (state['search_value'] or not (state['visible_rows'] or state['explicitly_empty_campaigns'])):
        return result('CAMPAIGN_NAME_CHECK_REQUIRES_POPULATED_UNFILTERED_LIST',
                      **readiness_fields(current, state))

    ready_document = (getattr(guard.session_states[binding.session], 'document_generation', None), current)
    if diagnostic and not state['search_count']:
        trigger = page.locator(state['search_trigger'])
        if await trigger.count(timeout=2000) != 1 or await trigger.is_visible(timeout=2000) is not True:
            return result('CAMPAIGN_SEARCH_TRIGGER_NOT_ACTIONABLE', **readiness_fields(current, state))
    browser.trace.agent_event('campaign_list_ready', role='observer', **readiness_fields(current, state))
    if confirm is not None:
        decision = await confirm()
        if decision != 'yes':
            return result('CAMPAIGN_LIST_OPERATOR_READY_TIMEOUT' if decision == 'timeout'
                          else 'CAMPAIGN_LIST_NOT_CONFIRMED_VISIBLE')
        fresh_url, fresh = await observe_list()
        fresh_document = (getattr(guard.session_states[binding.session], 'document_generation', None), fresh_url)
        if ready_document != fresh_document or not (ready(fresh) or exact_id_visible(fresh)):
            return result('CAMPAIGN_LIST_READINESS_CHANGED_AFTER_CONFIRMATION', **readiness_fields(fresh_url, fresh))
        current, state = fresh_url, fresh
    list_path = urlsplit(current).path
    browser.trace.agent_event('campaign_list_view_confirmed', role='observer',
                             operator_confirmed=confirm is not None, **readiness_fields(current, state))
    if diagnostic:
        diagnostic.phase = 'CAMPAIGN_LOOKUP'
        if state['search_trigger']:
            trigger = page.locator(state['search_trigger'])
            fresh_url, fresh = await observe_list()
            if (fresh_url != current or fresh['search_trigger'] != state['search_trigger']
                    or await trigger.count(timeout=2000) != 1 or await trigger.is_visible(timeout=2000) is not True):
                return result('CAMPAIGN_SEARCH_TRIGGER_CHANGED')
            diagnostic.mark('SEARCH_TRIGGER_CLICK')
            await trigger.click(timeout=3000)
            current, state = await wait_for_list(8, campaign_list_ready)
        if not campaign_list_ready(state) or urlsplit(current).path != list_path:
            return result('CAMPAIGN_SEARCH_INPUT_NOT_VISIBLE')
        diagnostic.mark('SEARCH_INPUT_VISIBLE')
    id_direct = exact_id_visible(state)
    if not id_direct:
        locator = page.locator(state['search'])
        if await locator.count(timeout=2000) != 1 or await locator.is_visible(timeout=2000) is not True:
            return result('CAMPAIGN_SEARCH_CONTROL_NOT_ACTIONABLE')
        await checked({list_path})
        if (await snapshot()).get('search') != state['search']:
            return result('CAMPAIGN_SEARCH_CONTROL_CHANGED')
        await locator.fill(payload.campaign_name,timeout=5000)
        if await locator.input_value(timeout=2000)!=payload.campaign_name:
            return result('CAMPAIGN_SEARCH_INPUT_READBACK_FAILED')
        previous=None
        for _ in range(12):
            await asyncio.sleep(.45)
            await checked({list_path})
            state=await snapshot()
            if (not state['busy'] and state['search_value']==payload.campaign_name
                    and (state['matches'] or state['no_results']) and previous==state['matches']):
                break
            previous=state['matches'] if not state['busy'] else None
        else:
            return result('CAMPAIGN_SEARCH_RESULTS_NOT_VERIFIED',visible_rows=state['visible_rows'],
                          exact_match_count=len(state['matches']),explicit_empty=state['no_results'])
    matches=state['matches']
    if check_unused and matches:
        eligible = [row for row in matches if row['whatsapp_channel'] and row['id']]
        if not require_configuration or len(matches) != 1 or len(eligible) != 1 or state['further_pages']:
            return result('CAMPAIGN_NAME_ALREADY_USED', exact_match_count=len(matches),
                          whatsapp_match_count=len(eligible), further_pages=state['further_pages'])
        campaign_id = eligible[0]['id']
        check_unused = False
    if check_unused:
        if state['no_results'] and not state['visible_rows'] and not state['further_pages']:
            return result('CAMPAIGN_NAME_UNUSED', campaign_name=payload.campaign_name)
        return result('CAMPAIGN_NAME_AVAILABILITY_UNCONFIRMED', **readiness_fields(current, state))
    facts=[{k:row[k] for k in ('id','status','created','updated','channel','whatsapp_channel')} for row in matches]
    if state['further_pages'] and not id_direct:
        return result('CAMPAIGN_SEARCH_PAGINATION_UNRESOLVED',exact_visible_matches=facts)
    eligible=[row for row in matches if row['whatsapp_channel']]
    if matches and not eligible:
        return result('CAMPAIGN_WHATSAPP_CHANNEL_NOT_VERIFIED',exact_matches=facts)
    matched=select_record(eligible,saved_at,state['time_column_zone'],campaign_id=campaign_id)
    if not matched:
        if not matches:
            code = 'CAMPAIGN_EXACT_NAME_NOT_FOUND'
        elif campaign_id:
            code = 'CAMPAIGN_EXPECTED_ID_NOT_UNIQUE'
        elif len(eligible) == 1:
            code = 'CAMPAIGN_TIMESTAMP_CORRELATION_UNVERIFIED'
        else:
            code = 'CAMPAIGN_EXACT_NAME_AMBIGUOUS'
        return result(code, exact_matches=facts, list_timestamp_timezone=state['time_column_zone'])
    links=list(dict.fromkeys(url for url in matched['links'] if detail_route(url)))
    if len(links)!=1:
        return result('MATCHING_CAMPAIGN_READ_ONLY_LINK_NOT_UNIQUE',exact_matches=facts,
                      safe_view_link_count=len(links),visible_row_controls=matched['controls'])
    fresh=await snapshot()
    if fresh['matches']!=matches or fresh['busy'] or (fresh['further_pages'] and not id_direct):
        return result('MATCHING_CAMPAIGN_ROW_CHANGED')
    await checked({list_path})
    await browser.navigate(links[0])
    return await read_detail(matched)
