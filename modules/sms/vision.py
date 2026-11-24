"""Focused SMS geometry screenshot using the existing privacy inventory and masks."""

import base64
import io
import json
import math

from PIL import Image, ImageDraw

from browser.diagnostics import _MASK_PROBE
from browser.metadata_channel import metadata_channel
from browser.observed_control import observed_node
from browser.shadow_inventory import shadow_inventory


REGION = r'''(() => {
 const roots=[...document.querySelectorAll('main,[role=main]')].filter(n=>n.getClientRects().length &&
   getComputedStyle(n).visibility==='visible');
 if(roots.length!==1) return null;
 const b=roots[0].getBoundingClientRect();
 const x=Math.max(0,Math.ceil(b.left)), y=Math.max(0,Math.ceil(b.top));
 const w=Math.min(720,Math.floor(Math.min(innerWidth,b.right))-x);
 const h=Math.min(420,Math.floor(Math.min(innerHeight,b.bottom))-y);
 return w>32&&h>32 ? [x,y,w,h,devicePixelRatio] : null;
})()'''

TAG_CROP = r'''(selector => {
 const node=document.querySelector(selector), b=node?.getBoundingClientRect();
 if(!b||b.width<=0||b.height<=0||b.bottom<0||b.top>innerHeight||b.right<0||b.left>innerWidth) return null;
 const x=Math.max(0,Math.floor(b.left-24)),y=Math.max(0,Math.floor(b.top-28));
 const w=Math.min(720,Math.ceil(Math.min(innerWidth,b.right+28))-x);
 const h=Math.min(300,Math.ceil(Math.min(innerHeight,b.bottom+85))-y);
 return w>32&&h>32 ? [x,y,w,h,devicePixelRatio] : null;
})(SELECTOR)'''


async def sms_screenshot(observer):
    browser = observer.browser
    document = await observer.protect()
    session = document[1]
    tag_selector = getattr(observer, 'tag_region', None)
    region_probe = REGION
    if tag_selector:
        identity = await observed_node(browser, tag_selector)
        await browser.guard.send('DOM.scrollIntoViewIfNeeded', {'backendNodeId': identity[-1]}, session)
        if await observer.protect() != document or await observed_node(browser, tag_selector) != identity:
            return None
        region_probe = TAG_CROP.replace('SELECTOR', json.dumps(tag_selector))
    async with metadata_channel(browser, session) as reader:
        coverage = await shadow_inventory(reader, session, browser.policy.origins, primary_frame=document[4])
    if (coverage.get('inventory_complete') is not True or coverage.get('inventory_limit_reached')
            or coverage.get('inventory_unavailable') or coverage.get('unknown_shadow_roots')
            or coverage.get('inaccessible_shadow_roots')):
        browser.trace.agent_event('sms_teacher_screenshot_unavailable', role='teacher', code='SMS_PIXEL_SCOPE_UNVERIFIED',
                                  complete=coverage.get('inventory_complete'), unavailable=coverage.get('inventory_unavailable'),
                                  unknown_roots=coverage.get('unknown_shadow_roots'), denied_roots=coverage.get('inaccessible_shadow_roots'))
        return None
    script = _MASK_PROBE.replace('SETTINGS', json.dumps({'origins': sorted(browser.policy.origins)}))
    region = await browser.page.evaluate(region_probe)
    mask = await browser.page.evaluate(script)
    if (not region or not mask.get('complete') or mask.get('animations')
            or mask.get('credential') or mask.get('unsupported') or await observer.protect() != document):
        browser.trace.agent_event('sms_teacher_screenshot_unavailable', role='teacher', code='SMS_CROP_PRIVACY_UNVERIFIED',
                                  region_visible=bool(region), **{name: mask.get(name) for name in
                                    ('complete', 'animations', 'credential', 'unsupported')})
        return None
    x, y, width, height, device_scale = region
    clip = {'x': mask['scrollX'] + x, 'y': mask['scrollY'] + y, 'width': width, 'height': height, 'scale': 1}
    raw = await browser.guard.send('Page.captureScreenshot', {'format': 'png', 'clip': clip,
                                    'captureBeyondViewport': False}, session)
    if (await browser.page.evaluate(region_probe) != region or await browser.page.evaluate(script) != mask
            or await observer.protect() != document or tag_selector and await observed_node(browser, tag_selector) != identity):
        browser.trace.agent_event('sms_teacher_screenshot_unavailable', role='teacher', code='SMS_CROP_CONTEXT_CHANGED')
        return None
    with Image.open(io.BytesIO(base64.b64decode(raw['data'], validate=True))) as source:
        matches_css = source.size == (width, height)
        matches_device = (1 <= device_scale <= 3 and abs(source.width - width * device_scale) <= 1
                          and abs(source.height - height * device_scale) <= 1)
        if (not matches_css and not matches_device) or source.width * source.height > 2800000:
            browser.trace.agent_event('sms_teacher_screenshot_unavailable', role='teacher', code='SMS_CROP_DIMENSION_MISMATCH')
            return None
        safe = source.convert('RGB')
        draw = ImageDraw.Draw(safe)
        sx, sy = source.width / width, source.height / height
        for left, top, w, h in mask['masks']:
            draw.rectangle((math.floor((left-x)*sx)-6, math.floor((top-y)*sy)-6,
                            math.ceil((left+w-x)*sx)+6, math.ceil((top+h-y)*sy)+6), fill='black')
        data = io.BytesIO()
        safe.save(data, format='PNG')
    return data.getvalue()
