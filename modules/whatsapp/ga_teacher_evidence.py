"""Read-only diagnostic view of the exact Setup document held at STUCK."""
import base64
import hashlib
import io
import json
from typing import Literal

from langchain_core.tools import tool

from browser.observed_control import observed_node
from modules.whatsapp.contract import V2Stop


_LABELS = {'advanced', 'advanced settings', 'advanced options', 'google analytics tracking',
           'enable google analytics tracking', 'ga tracking', 'source', 'medium', 'campaign', 'content',
           'term', 'campaign name', 'campaign id', 'campaign source', 'campaign medium',
           'campaign content', 'campaign term', 'utm source', 'utm medium', 'utm campaign',
           'utm content', 'utm term', 'utm_source', 'utm_medium', 'utm_campaign', 'utm_content', 'utm_term'}


def scoped_ga_switch_observed(report):
    return report.get('status') == 'OBSERVED' and sum(
        row.get('visible') is True and row.get('disabled') is False and row.get('inside_nearby_group') is True
        and (row.get('role') in {'switch','checkbox'} or row.get('type') == 'checkbox')
        for row in report.get('controls', ())) == 1


def adjacent_utms_observed(report):
    from modules.whatsapp.contract import UTM_FIELDS

    if report.get('status') != 'OBSERVED' or not report.get('surrounding_group_tag'):
        return False
    rows = report.get('controls', ())
    switch = [row for row in rows if row.get('role') == 'switch' and row.get('visible')
              and row.get('inside_nearby_group')]
    return len(switch) == 1 and all(sum(
        row.get('tag') == 'INPUT' and row.get('type') == 'text' and row.get('visible') is True
        and row.get('disabled') is False and row.get('inside_nearby_group') is False
        and row.get('label_links', {}).get('native') == 1
        and any(label.get('visible') is True for label in row.get('linked_labels', ()))
        and {'id': key, 'formcontrolname': key}.items() <= dict(row.get('names', ())).items()
        for row in rows) == 1 for key in UTM_FIELDS)

_DOM = r'''(s=>{
 const safe=new Set(s.labels), markers=new Set(['google analytics tracking','enable google analytics tracking','ga tracking']);
 const text=x=>String(x??'').trim().toLowerCase().replace(/\s+/g,' ').replace(/[:*]+$/,'').trim();
 const visible=e=>!!e&&e.getClientRects().length>0&&getComputedStyle(e).visibility==='visible'&&
   !e.closest('[hidden],[aria-hidden=true],[inert]');
 const forms=document.querySelectorAll(s.scope), form=forms.length===1?forms[0]:null;
 if(!visible(form))return {status:'SETUP_SCOPE_NOT_VISIBLE'};
 const leaves=(root)=>[...root.querySelectorAll('label,h2,h3,h4,h5,mat-label,legend,div,p,span,strong,[role=heading]')]
   .filter(e=>e.children.length===0&&safe.has(text(e.textContent)));
 const anchors=leaves(form).filter(e=>visible(e)&&markers.has(text(e.textContent)));
 if(anchors.length!==1)return {status:'GA_HEADING_NOT_UNIQUE',heading_count:anchors.length};
 const heading=anchors[0], near=heading.parentElement;
 const valid=e=>e&&e!==form&&form.contains(e);
 const card=heading.closest('mat-card,section,fieldset,[role=group],mat-expansion-panel');
 const root=[card,near?.parentElement,near].find(valid);
 if(!root)return {status:'GA_SURROUNDINGS_UNBOUND'};
 const outside=root.parentElement!==form&&form.contains(root.parentElement)?root.parentElement:null;
 const selectors='input,textarea,[role=switch],[role=checkbox]';
 const container=outside||root;
 const controlNodes=[...container.querySelectorAll(selectors)].filter(e=>!e.matches('input[type=password],input[type=file]'));
 const css=e=>{const parts=[];for(let n=e;n&&parts.length<30;n=n.parentElement){
   const siblings=n.parentElement?[...n.parentElement.children].filter(x=>x.tagName===n.tagName):[n];
   parts.unshift(n.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(n)+1)+')');
   const q=parts.join(' > ');if(document.querySelectorAll(q).length===1)return q;}return null;};
 const safeLabel=value=>safe.has(text(value))?text(value):null;
 const safeKey=value=>/^(?:utm|campaign)[_\-]?(source|medium|campaign|content|term|name|id)$/i.test(value??'')?value:null;
 const controls=controlNodes.slice(0,20).map(e=>{
   const own=[...(e.labels||[])];
   const refs=(e.getAttribute('aria-labelledby')||'').split(/\s+/).map(id=>document.getElementById(id)).filter(Boolean);
   const adjoining=leaves(e.parentElement).filter(n=>n!==e);
   const linked=[...own,...refs];
   return {inside_nearby_group:root.contains(e), tag:e.tagName, type:['text','checkbox','hidden','search','url','button','submit'].includes(e.type)?e.type:null,
     role:['switch','checkbox','textbox','searchbox'].includes(e.getAttribute('role'))?e.getAttribute('role'):null,
     visible:visible(e),disabled:e.disabled===true||e.getAttribute('aria-disabled')==='true',
     names:['name','id','formcontrolname','data-utm-key'].map(key=>[key,safeKey(e.getAttribute(key))]).filter(x=>x[1]),
     linked_labels:linked.map(n=>({text:safeLabel(n.textContent),visible:visible(n)})).slice(0,5),
     label_links:{native:own.length,aria_labelledby:refs.length,aria_label:safeLabel(e.getAttribute('aria-label'))},
     nearby_labels:adjoining.map(n=>safeLabel(n.textContent)).filter(Boolean).slice(0,6),
     parents:[e.parentElement?.tagName,e.parentElement?.parentElement?.tagName],selector:css(e)};
 });
 const switchNodes=controlNodes.filter(e=>root.contains(e)&&visible(e)&&
   e.matches('input[type=checkbox],[role=switch],[role=checkbox]'));
 const toggle=switchNodes.length===1?switchNodes[0]:null;
 const viewport=e=>{
   const r=e.getBoundingClientRect(), x=(r.left+r.right)/2, y=(r.top+r.bottom)/2;
   const outside=r.right<=0||r.bottom<=0||r.x>=innerWidth||r.y>=innerHeight;
   const clipped=[r.left+1,x,r.right-1].some(a=>[r.top+1,y,r.bottom-1].some(b=>
       !e.contains(document.elementFromPoint(a,b))));
   return {outside_viewport:outside, clipped:!outside&&clipped,
     in_view:!outside&&!clipped, bounds:[r.x,r.y,r.width,r.height].map(Math.round)};
 };
 const region={heading:viewport(heading),switch:toggle?viewport(toggle):null,
   scrollable_ancestors:0, viewport_size:[innerWidth,innerHeight]};
 for(let n=heading.parentElement;n;n=n.parentElement)
   if(/auto|scroll/.test(getComputedStyle(n).overflowY)&&n.scrollHeight>n.clientHeight)region.scrollable_ancestors++;
 const switchesInView=toggle&&region.switch.in_view&&region.heading.in_view;
 const rect=switchesInView?[heading,toggle].map(e=>e.getBoundingClientRect()):[];
 const x=rect.length?Math.floor(Math.min(...rect.map(r=>r.left))):0;
 const y=rect.length?Math.floor(Math.min(...rect.map(r=>r.top))):0;
 const right=rect.length?Math.ceil(Math.max(...rect.map(r=>r.right))):0;
 const bottom=rect.length?Math.ceil(Math.max(...rect.map(r=>r.bottom))):0;
 const clip=right>x&&bottom>y&&x>=0&&y>=0&&right<=innerWidth&&bottom<=innerHeight?
   [x,y,right-x,bottom-y]:null;
 // Keep approved heading pixels and a control with no unapproved text or media.
 const cleanBackground=e=>{
   for(let n=e;n;n=n.parentElement)if(getComputedStyle(n).backgroundImage!=='none')return false;
   return true;
 };
 const safePixels=e=>[e,...e.querySelectorAll('*')].every(n=>
   !n.matches('img,picture,canvas,video,svg,iframe,[contenteditable]')&&
   ![n,'::before','::after'].some(p=>getComputedStyle(n,p===n?null:p).backgroundImage!=='none')&&
   ['::before','::after'].every(p=>['none','normal','""'].includes(getComputedStyle(n,p).content)));
 const headingMask=cleanBackground(heading)&&safePixels(heading)&&region.heading.in_view;
 const switchMask=!!(toggle&&cleanBackground(toggle)&&safePixels(toggle)&&region.switch.in_view&&
   (!text(toggle.innerText)||safe.has(text(toggle.innerText))));
 const masks=[headingMask?heading:null,switchMask?toggle:null].filter(Boolean).map(e=>{
   const r=e.getBoundingClientRect();
   return [Math.ceil(r.left),Math.ceil(r.top),Math.floor(r.right),Math.floor(r.bottom)];
 });
 return {status:'OBSERVED',heading:text(heading.textContent),group_tag:root.tagName,
   surrounding_group_tag:outside?.tagName||null,controls,control_count:controlNodes.length,
   visible_labels:leaves(container).filter(visible).map(e=>text(e.textContent)).slice(0,30),
   viewport:region, heading_selector:css(heading),
   switch_selector:toggle?css(toggle):null,
   image:{clip,masks,heading_mask:headingMask,switch_mask:switchMask,scroll:[scrollX,scrollY],scale:devicePixelRatio}};
})(SETTINGS)'''


class GATeacherEvidence:
    def __init__(self, setup, document):
        self.setup = setup
        self.document = document
        self.page = setup.browser.page
        self.trace = setup.browser.trace
        self.refs = []
        self.dom = None
        self.vision_requested = False

    async def _require_current(self):
        if (self.setup.stage != 'setup' or self.setup.browser.page is not self.page
                or self.setup.browser.close_started or await self.setup.protect() != self.document):
            raise V2Stop('GA_TEACHER_SETUP_CONTEXT_CHANGED', owner='browser', unsafe=True)

    async def _sample(self):
        await self._require_current()
        self.setup.browser.budget()
        args = {'labels': sorted(_LABELS), 'scope': self.setup.ui.stage_selectors['setup']}
        result = await self.page.evaluate(_DOM.replace('SETTINGS', json.dumps(args)))
        await self._require_current()
        return result

    def _record(self, kind, **facts):
        ref = self.trace.agent_event(kind, role='teacher', **facts)
        self.refs.append(ref)
        return ref

    async def reveal_viewport(self):
        """Controller-only preparation using the existing CDP scoped scroll primitive."""
        before = await self._sample()
        view = before.get('viewport', {})
        target = next((before.get(name + '_selector') for name in ('heading', 'switch')
                       if view.get(name) and not view[name]['in_view']), None)
        if target:
            node = await observed_node(self.setup.browser, target)
            await self._require_current()
            if (await self._sample()).get('heading_selector') != before.get('heading_selector'):
                raise V2Stop('GA_SCROLL_TARGET_CHANGED', owner='recognition')
            await self.setup.browser.guard.send('DOM.scrollIntoViewIfNeeded',
                {'backendNodeId': node[-1]}, self.document[1])
        after = await self._sample() if target else before
        return {'before': view, 'after': after.get('viewport', {}), 'scroll_dispatched': bool(target),
                'status': after.get('status'), 'heading_count': after.get('heading_count')}

    async def inspect(self):
        if self.dom is not None:
            return {'status': 'DOM_INSPECTION_ALREADY_RETURNED'}
        report = await self._sample()
        for internal in ('image', 'heading_selector', 'switch_selector'):
            report.pop(internal, None)
        for index, row in enumerate(report.get('controls', [])):
            locator = row.pop('selector')
            if not locator or index >= 8:
                continue
            try:
                node = await observed_node(self.setup.browser, locator)
                tree = await self.setup.browser.guard.send('Accessibility.getPartialAXTree',
                    {'backendNodeId': node[-1], 'fetchRelatives': False}, self.document[1])
                ax = next(iter(tree.get('nodes', [])), {})
                name = (ax.get('name', {}).get('value') or '').strip().lower()
                role = ax.get('role', {}).get('value')
                row['ax'] = {'name': name if name in _LABELS else None, 'ignored': ax.get('ignored'),
                             'role': role if role in {'textbox', 'searchbox', 'switch', 'checkbox', 'generic'} else None}
            except (RuntimeError, ValueError, TimeoutError):
                row['ax'] = {'status': 'UNAVAILABLE'}
        await self._require_current()
        report['evidence_ref'] = self._record('ga_teacher_dom_observation', result=report)
        self.dom = report
        return report

    async def view(self, reason):
        if self.dom is None or self.vision_requested:
            return {'status': 'VISION_REQUIRES_DOM_AND_ONE_REQUEST'}, None
        self.vision_requested = True
        # A complete visible key census already settles the layout question.
        if sum(bool(row['visible'] and row['names']) for row in self.dom.get('controls', [])) >= 5:
            return {'status': 'VISION_UNNECESSARY_DOM_ESTABLISHED_INPUTS'}, None
        before = await self._sample()
        image = before.get('image', {})
        clip = image.get('clip')
        failed = []
        if not clip:
            failed.append('GA_CROP_REGION_ABSENT_OR_OUTSIDE_VIEWPORT')
        if (not image.get('heading_mask') or not image.get('switch_mask')
                or any(right <= left or bottom <= top for left, top, right, bottom in image.get('masks', ()))):
            failed.append('GA_CROP_HEADING_OR_SWITCH_PIXELS_NOT_PRIVACY_CERTIFIED')
        if image.get('scale') != 1:
            failed.append('GA_CROP_DEVICE_PIXEL_RATIO_UNSUPPORTED')
        if clip and clip[2] * clip[3] > 900000:
            failed.append('GA_CROP_AREA_LIMIT')
        if failed:
            status = 'SAFE_GA_CROP_UNAVAILABLE'
            return {'status': status, 'failed_predicates': failed, 'evidence_ref': self._record(
                'ga_teacher_vision', status=status, reason=reason, failed_predicates=failed)}, None
        x, y, w, h = clip
        sx, sy = image['scroll']
        raw = await self.setup.browser.guard.send('Page.captureScreenshot', {'format': 'png', 'clip': {
            'x': x+sx, 'y': y+sy, 'width': w, 'height': h, 'scale': 1}}, self.document[1])
        if await self._sample() != before:
            return {'status': 'GA_CROP_CHANGED_DURING_CAPTURE'}, None
        from PIL import Image

        with Image.open(io.BytesIO(base64.b64decode(raw['data']))) as source:
            if source.size != (w, h):
                return {'status': 'GA_CROP_DIMENSION_MISMATCH'}, None
            safe = Image.new('RGB', source.size, '#eeeeee')
            for left, top, right, bottom in image['masks']:
                box = (max(0, left-x), max(0, top-y), min(w, right-x), min(h, bottom-y))
                if box[0]<box[2] and box[1]<box[3]:
                    safe.paste(source.crop(box), box[:2])
            content = io.BytesIO()
            safe.save(content, 'PNG')
        await self._require_current()
        data = content.getvalue()
        ref = self._record('ga_teacher_vision', status='IMAGE_RETURNED', reason=reason,
                           image_sha256=hashlib.sha256(data).hexdigest(), retained_label_regions=len(image['masks']),
                           dom_crosscheck=self.dom['evidence_ref'], heading_and_switch_certified=True)
        return {'status': 'IMAGE_RETURNED', 'evidence_ref': ref, 'dom_crosscheck': self.dom['evidence_ref']}, base64.b64encode(data).decode()

    def registered(self):
        @tool
        async def inspect_ga_setup() -> dict:
            """Inspect bounded DOM and accessibility evidence around the held GA section without reading field values."""
            return await self.inspect()

        @tool(response_format='content_and_artifact')
        async def view_ga_setup(reason: Literal['layout_ambiguous', 'visibility_unresolved']) -> tuple:
            """Request one privacy-filtered image only when the DOM leaves layout or visibility unresolved."""
            result, data = await self.view(reason)
            blocks = [{'type': 'text', 'text': json.dumps(result)}]
            if data:
                blocks.append({'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,'+data, 'detail': 'low'}})
            return blocks, {'image_attached': bool(data)}

        return inspect_ga_setup, view_ga_setup
