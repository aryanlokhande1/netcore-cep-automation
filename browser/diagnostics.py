"""Never captures private/live pages. Optional evidence only for static safe fixtures."""
from app.config import ROOT

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
