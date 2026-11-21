"""Fixture capture and explicitly authorized privacy-filtered rendering comparisons."""
from app.config import ROOT
import asyncio
import base64
import hashlib
import io
import json
import math
import re
from datetime import datetime,timezone

from modules.login.discovery import DOCUMENT_HELPERS,OPEN_ROOT_HELPERS,VISIBILITY_HELPERS


def owned_window_observation(browser):
    """OS visibility of our exact owned browser PID; no titles, handles or pixels."""
    import sys
    result={'status':'UNAVAILABLE','headed_configured':not browser.config.headless,
        'visible_owned_window_count':None,'captured_at':datetime.now(timezone.utc).isoformat()}
    if sys.platform!='win32' or not getattr(browser,'process',None):return result
    try:
        import ctypes
        from ctypes import wintypes
        api=ctypes.WinDLL('user32',use_last_error=True)
        callback_type=ctypes.WINFUNCTYPE(wintypes.BOOL,wintypes.HWND,wintypes.LPARAM)
        api.EnumWindows.argtypes=[callback_type,wintypes.LPARAM]
        api.GetWindowThreadProcessId.argtypes=[wintypes.HWND,ctypes.POINTER(wintypes.DWORD)]
        api.IsWindowVisible.argtypes=[wintypes.HWND]
        visible=[]
        @callback_type
        def visit(window,unused):
            process=wintypes.DWORD()
            api.GetWindowThreadProcessId(window,ctypes.byref(process))
            if process.value==browser.process.pid and api.IsWindowVisible(window):visible.append(True)
            return True
        if not api.EnumWindows(visit,0):return result
        result.update(status='OBSERVED',visible_owned_window_count=len(visible))
    except (OSError,AttributeError):pass
    return result

_MASK_PROBE = r'''(settings=>{''' + DOCUMENT_HELPERS + OPEN_ROOT_HELPERS + r'''
 const masks=[];let limited=false,unsupported=false,credential=false;
 const add=r=>{if(!r||r.width<=0||r.height<=0)return;
   if(masks.length>=20000){limited=true;return;}
   masks.push([r.x,r.y,r.width,r.height].map(v=>Math.round(v*10)/10));};
 for(const {doc,path,nodes} of scopes){
   if(path.length)continue; // Every frame viewport is masked as a whole below.
   for(const e of nodes){
     if(!visible(e))continue;
     const style=getComputedStyle(e);
     if((style.filter&&style.filter!=='none')||(style.textShadow&&style.textShadow!=='none'))unsupported=true;
     const pseudo=['::before','::after'].some(p=>{const c=getComputedStyle(e,p).content;return c&&!['none','normal','""',"''"].includes(c)&&c.replace(/[\s"']/g,'')!=='';});
     if(pseudo)unsupported=true;
     if(e.matches('input[type=password],input[autocomplete=one-time-code]'))credential=true;
     if(e.matches('input,textarea,select,img,svg,canvas,video,object,embed,iframe,frame,[contenteditable]')||style.backgroundImage.includes('url('))add(e.getBoundingClientRect());
     for(const n of e.childNodes){if(n.nodeType!==3)continue;
       const range=e.ownerDocument.createRange();range.selectNodeContents(n);
       for(const rect of range.getClientRects())add(rect);range.detach();}
   }
 }
 const animations=typeof document.getAnimations==='function'?document.getAnimations().some(a=>a.playState==='running'):true;
 return {masks,width:innerWidth,height:innerHeight,scrollX,scrollY,animations,credential,unsupported,
   complete:!limited&&!coverage.node_limit_reached&&!coverage.shadow_limit_reached&&!coverage.shadow_roots_uninspected&&
     !inventory.permitted_frames_unavailable&&!inventory.document_limit_reached};
})(SETTINGS)'''

_NODE_RESOLVER = r'''(settings=>{
 let doc=document;
 for(const selector of settings.frame_selectors){const m=doc.querySelectorAll(selector);if(m.length!==1)return null;
   const next=m[0].contentDocument;if(!next)return null;
   if(!settings.origins.includes(new URL(['about:blank','about:srcdoc'].includes(next.URL)?doc.URL:next.URL).origin))return null;doc=next;}
 let root=doc;for(const selector of settings.shadow_selectors){const m=root.querySelectorAll(selector);if(m.length!==1||!m[0].shadowRoot)return null;root=m[0].shadowRoot;}
 const matches=root.querySelectorAll(settings.selector);return matches.length===1?matches[0]:null;
})(SETTINGS)'''


async def closed_host_render_state(browser,backend_id):
    """Read the already-observed host only; never enter closed contents."""
    guard=browser.guard;owner=guard.session_states.get(guard.primary_session)
    if not owner or not guard.primary_observable() or guard.blocking_hosts:
        return {'status':'CONTEXT_UNAVAILABLE'}
    before=(owner.target,owner.session,owner.generation,owner.document_generation)
    group='login-closed-host-geometry';session=owner.session
    try:
        browser.budget()
        resolved=await guard.send('DOM.resolveNode',{'backendNodeId':backend_id,'objectGroup':group},session)
        obj=resolved.get('object',{}).get('objectId')
        if not obj:return {'status':'NODE_UNAVAILABLE'}
        state=await guard.send('Runtime.callFunctionOn',{'objectId':obj,'returnByValue':True,'silent':True,
            'functionDeclaration':'function(){'+VISIBILITY_HELPERS+
                'return {rendering:renderState(this),viewport:{width:this.ownerDocument.defaultView.innerWidth,height:this.ownerDocument.defaultView.innerHeight}};}'},session)
        current=guard.session_states.get(session)
        if not current or before!=(current.target,current.session,current.generation,current.document_generation):
            return {'status':'DOCUMENT_CHANGED'}
        return {'status':'OBSERVED',**state.get('result',{}).get('value',{})}
    except Exception as error:return {'status':'UNAVAILABLE','error_class':type(error).__name__}
    finally:
        try:await guard.send('Runtime.releaseObjectGroup',{'objectGroup':group},session)
        except Exception:pass

_SAFE_GATE='''(() => {
 if(document.querySelector('input,textarea,iframe,script,style,link,img,svg,canvas,video,[contenteditable],[style]'))return false;
 const main=document.querySelector('#dashboard');
 if(!main || document.body.children.length!==1 || main.children.length!==3 || document.body.querySelectorAll('*').length!==4)return false;
 const [heading,tenant,link]=main.children;
 if(heading.tagName!=='H1'||heading.textContent!=='Synthetic CEP dashboard'||tenant.textContent!=='synthetic-tenant'||link.textContent!=='Read-only context')return false;
 if(document.body.textContent!==heading.textContent+tenant.textContent+link.textContent)return false;
 return [...document.querySelectorAll('*')].every(e=>[...e.attributes].every(a=>
   (a.name==='id'&&['dashboard','tenant','readonly'].includes(a.value)) ||
   (e===link&&a.name==='href'&&a.value==='/context')));
})()'''

class ScreenshotDiagnostics:
    def __init__(self,browser):
        self.browser=browser
        self.used=False

    async def compare(self,dom,scope,enabled=False,*,campaign_tools=None):
        """Owned, read-only DOM/AX comparison; no names/text enter retained AX data.

        Live pixels are persisted only after complete stable redaction coverage.
        Unknown closed content cannot be safely masked and never becomes a fake
        fully-redacted visual proof. Existing fixture-only capture stays unchanged.
        """
        if not enabled:return {'status':'NOT_ENABLED','screenshot_status':'NOT_CAPTURED'}
        browser=self.browser
        result={'status':'PARTIAL','screenshot_status':'NOT_CAPTURED','targets':[],
            'captured_at':datetime.now(timezone.utc).isoformat(),
            'source_dom_sha256':hashlib.sha256(json.dumps(dom.get('dom_diagnostics',{}),sort_keys=True).encode()).hexdigest(),
            'security_effect':'DIAGNOSTIC_ONLY_NO_AUTHENTICATION_GRANT'}
        from browser.discovery import login_page_context
        from app.safety import require_fixture_browser,require_live_browser
        def identity():
            if campaign_tools is not None:
                from browser.observed_control import protected_document
                return protected_document(browser)
            g=browser.guard;o=g.session_states.get(g.primary_session)
            if not o or not g.primary_observable() or g.blocking_hosts:return None
            return (o.target,o.session,o.generation,o.document_generation,o.frame_id,o.loader_id)
        try:
            (require_fixture_browser if browser.fixture else require_live_browser)(browser)
            if campaign_tools is None:
                before=identity();context=await login_page_context(browser)
                result['page_context']=context
                if not before or not context.get('primary_document_coherent') or not context.get('expected_host'):
                    result['status']='CONTEXT_UNAVAILABLE';return result
            else:
                before=await campaign_tools.protect()
                result['page_context']={'owned_campaign_document': bool(before)}
                if before!=identity():
                    result['status']='CONTEXT_UNAVAILABLE';return result
            origins=sorted(browser.policy.origins);session=before[1]
            samples={s['element_ref']:s for s in dom.get('dom_diagnostics',{}).get('semantic_samples',[])}
            async with asyncio.timeout(15):
                for target in dom.get('_render_targets',[])[:12]:
                    browser.budget()
                    if identity()!=before:raise RuntimeError('RENDER_DOCUMENT_CHANGED')
                    group='login-render-diagnostic'
                    row={'element_ref':target['element_ref'],'collector':samples.get(target['element_ref']),
                        'accessibility':{'status':'UNAVAILABLE'}}
                    try:
                        evaluated=await browser.guard.send('Runtime.evaluate',{'expression':_NODE_RESOLVER.replace('SETTINGS',json.dumps({**target,'origins':origins})),
                            'objectGroup':group,'returnByValue':False,'generatePreview':False,'silent':True},session)
                        obj=evaluated.get('result',{}).get('objectId')
                        if obj:
                            ax=await browser.guard.send('Accessibility.getPartialAXTree',{'objectId':obj,'fetchRelatives':False},session)
                            values=[]
                            for node in ax.get('nodes',[])[:1]:
                                role=(node.get('role') or {}).get('value')
                                values.append({'ignored':node.get('ignored') is True,
                                    'role':role if role in {'main','navigation','heading','button','link','generic','banner','dialog','alert','StaticText','none'} else 'OTHER',
                                    'ignored_reasons':[v['name'] for v in node.get('ignoredReasons',[])[:12]
                                        if isinstance(v.get('name'),str) and re.fullmatch(r'[A-Za-z_]{1,64}',v['name'])]})
                                if type(node.get('backendDOMNodeId')) is int:
                                    row['stable_node_ref']='node-'+hashlib.sha256(json.dumps([before,node['backendDOMNodeId']]).encode()).hexdigest()[:16]
                            row['accessibility']={'status':'OBSERVED','nodes':values}
                            measured=await browser.guard.send('Runtime.callFunctionOn',{'objectId':obj,
                                'functionDeclaration':'function(){'+VISIBILITY_HELPERS+'return renderState(this);}',
                                'returnByValue':True,'silent':True},session)
                            row['current_rendering']=measured.get('result',{}).get('value')
                    except Exception as error:row['accessibility']['error_class']=type(error).__name__
                    finally:
                        try:await browser.guard.send('Runtime.releaseObjectGroup',{'objectGroup':group},session)
                        except Exception:pass
                    result['targets'].append(row)
                if identity()!=before:raise RuntimeError('RENDER_DOCUMENT_CHANGED')
                result['status']='DOM_AX_OBSERVED'
                if scope.get('inaccessible_shadow_roots') or scope.get('unknown_shadow_roots'):
                    result['screenshot_status']='SKIPPED_PRIVACY_INACCESSIBLE_SCOPE'
                else:
                    pixel_scope=scope
                    if pixel_scope.get('inventory_complete') is not True:
                        # A non-custom closed host is invisible to ordinary JS.
                        # Reuse the protected metadata inventory before pixels.
                        from browser.metadata_channel import metadata_channel
                        from browser.shadow_inventory import shadow_inventory
                        async with metadata_channel(browser,session) as reader:
                            pixel_scope=await shadow_inventory(reader,session,browser.policy.origins,primary_frame=before[4])
                    if (pixel_scope.get('inventory_complete') is not True or
                        pixel_scope.get('inventory_limit_reached') or pixel_scope.get('inventory_unavailable') or
                        pixel_scope.get('inaccessible_shadow_roots') or pixel_scope.get('unknown_shadow_roots')):
                        result['screenshot_status']='SKIPPED_PRIVACY_SCOPE_UNAVAILABLE'
                        raise RuntimeError('RENDER_PIXEL_SCOPE_UNAVAILABLE')
                    masks=await browser.page.evaluate(_MASK_PROBE.replace('SETTINGS',json.dumps({'origins':origins})))
                    result['privacy_coverage']={key:masks.get(key) for key in ('complete','animations','unsupported','credential')}
                    if not masks.get('complete') or masks.get('animations') or masks.get('unsupported') or masks.get('credential'):
                        result['screenshot_status']='SKIPPED_PRIVACY_UNSTABLE_OR_UNMODELED_RENDER'
                    else:
                        image=await browser.guard.send('Page.captureScreenshot',{'format':'png','captureBeyondViewport':False,
                            'clip':{'x':masks['scrollX'],'y':masks['scrollY'],'width':masks['width'],'height':masks['height'],'scale':1}},session)
                        after_masks=await browser.page.evaluate(_MASK_PROBE.replace('SETTINGS',json.dumps({'origins':origins})))
                        if masks!=after_masks or identity()!=before:raise RuntimeError('RENDER_CAPTURE_CHANGED')
                        from PIL import Image,ImageDraw
                        raw=base64.b64decode(image['data']);png=Image.open(io.BytesIO(raw))
                        if png.width*png.height>8000000:raise RuntimeError('RENDER_IMAGE_LIMIT')
                        safe=png.convert('RGB');draw=ImageDraw.Draw(safe)
                        sx=safe.width/masks['width'];sy=safe.height/masks['height']
                        for x,y,width,height in masks['masks']:
                            draw.rectangle((math.floor(x*sx)-6,math.floor(y*sy)-6,math.ceil((x+width)*sx)+6,math.ceil((y+height)*sy)+6),fill='black')
                        name='campaign-ui-redacted.png' if campaign_tools is not None else 'rendered-ui-redacted.png'
                        path=ROOT/'credentials'/'whatsapp-v2'/browser.trace.run_id/name
                        path.parent.mkdir(parents=True,exist_ok=True);safe.save(path,format='PNG')
                        del raw,image,png
                        result.update(screenshot_status='CAPTURED_REDACTED',screenshot_reference=path.relative_to(ROOT).as_posix(),
                            screenshot_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),redacted_regions=len(masks['masks']),
                            screenshot_meaning='Actual current viewport pixels; text, values and media masked. No identity proof.')
            result['targets_attempted']=len(result['targets'])
            result['targets_compared']=sum(row['accessibility'].get('status')=='OBSERVED' and isinstance(row.get('current_rendering'),dict) for row in result['targets'])
            result['ax_ignored']=sum(any(n['ignored'] for n in row['accessibility'].get('nodes',[])) for row in result['targets'])
            result['visible_but_excluded']=sum(bool((row.get('collector') or {}).get('visible')) and
                (row.get('collector') or {}).get('collector_gate')!='ELIGIBLE_PRECLASSIFICATION' for row in result['targets'])
        except Exception as error:
            result.update(status='COMPARISON_UNAVAILABLE',error_class=type(error).__name__)
        browser.trace.lifecycle('RENDER_COMPARISON',{k:v for k,v in result.items() if k!='targets'})
        return result

    async def capture(self,enabled=False):
        if not enabled or self.used or not self.browser.fixture:
            return {'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}
        self.used=True
        try:
            from app.safety import require_fixture_browser
            require_fixture_browser(self.browser)
            if not self.browser.policy.permits(await self.browser.page.url()) or not await self.browser.page.evaluate(_SAFE_GATE):
                return {'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}
            content=await self.browser.page.screenshot(type='png',animations='disabled',caret='hide',full_page=False)
            if not await self.browser.page.evaluate(_SAFE_GATE):
                return {'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}
            path=ROOT/'outputs'/f'{self.browser.trace.run_id}-safe-diagnostic.png'
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_bytes(content)
            return {'captured':True,'reason':'SAFE_STATIC_FIXTURE','artifact':path.name}
        except Exception:
            return {'captured':False,'reason':'SCREENSHOT_SKIPPED_PRIVACY'}
