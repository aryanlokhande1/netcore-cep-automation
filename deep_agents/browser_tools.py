"""Model-facing Stagehand tools. No shell, filesystem, arbitrary JS, URLs or values."""
import asyncio
import json
from typing import Literal
from uuid import uuid4
from langchain_core.tools import tool
from app.safety import require_fixture_browser, require_fixture_origin
from app.state import Result
from modules.login.verifier import verify
from deep_agents.dom import SANITIZED_DOM

TOOL_NAMES = {'navigate_approved','inspect_page','interact_observed','verify_dashboard'}

class ScreenshotDiagnostics:
    def request(self):
        return {'ok':False,'error':'SCREENSHOT_DISABLED','captured':False}

class BrowserTools:
    def __init__(self,browser,entry_url,strategy=None):
        from urllib.parse import urlsplit
        require_fixture_browser(browser)
        origin=next(iter(browser.policy.origins))
        require_fixture_origin(origin)
        if not browser.policy.permits(entry_url) or urlsplit(entry_url).path != '/start':
            raise PermissionError('SYNTHETIC_ONLY')
        self.browser,self.entry_url=browser,entry_url
        from learning.policies import Strategy
        self.strategy=strategy or Strategy()
        self.references={}
        self.recovery_used=False
        self.navigation_count=0
        self.terminal_error=False
        self.last_verification=Result.NOT_TESTED
        self.lock=asyncio.Lock()
        self.screenshots=ScreenshotDiagnostics()

    def fail(self,code,terminal=True):
        self.terminal_error |= terminal
        self.browser.trace.add('error',error='POLICY' if code in {'POLICY','ORIGIN_POLICY_BLOCKED','UNOBSERVED_ELEMENT','LOGIN_SUBMISSION_LIMIT'} else 'BROWSER')
        # Codes originate in trusted application branches; no exception text or tool arguments.
        if not code or any(c not in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ_' for c in code):
            raise ValueError('Invalid application stop code')
        self.browser.trace.events[-1]['stop_code']=code
        return {'ok':False,'error':code,'terminal':terminal}

    async def navigate(self,url):
        async with self.lock:
            if self.terminal_error or url != self.entry_url or self.navigation_count:
                return self.fail('POLICY')
            self.navigation_count += 1
            try:
                await self.browser.navigate(url)
                self.references.clear()
                return {'ok':True,'state':'navigated','must_inspect':True}
            except Exception:
                return self.fail('NAVIGATION_FAILED')

    async def _inspect(self):
        observation=await self.browser.observe()
        self.last_verification=verify(observation)
        if not observation.allowed:
            return self.fail('POLICY')
        self.browser.budget()
        prefix=uuid4().hex
        semantic=[]
        if 'prefer_semantic_roles' in self.strategy.rules:
            from browser.live_dom import LIVE_DOM
            source=LIVE_DOM.replace("log\\s*in|sign\\s*in", "log\\s*in|sign\\s*in|continue securely|enter cep")
            semantic=await self.browser.page.evaluate(source.replace('PREFIX',json.dumps(prefix+'-roles')))
        elements=await self.browser.page.evaluate(SANITIZED_DOM.replace('PREFIX',json.dumps(prefix)))
        known={e['key'] for e in elements}
        elements += [e for e in semantic if e['key'] not in known]
        self.browser.trace.role_evidence.update({e['key']:e.get('evidence',['VISIBLE','FIXTURE_KNOWN_LABEL']) for e in elements if e['key'] in {'account','username','password','submit'}})
        if observation.credential_form and not any(e['key']=='submit' for e in elements):
            observation=observation.model_copy(update={'role_labels_changed':True})
        self.references={f'{prefix}-{i}':item for i,item in enumerate(elements)}
        if {'account','username','password','submit'} <= {e['key'] for e in elements}:
            self.browser.trace.fact('LOGIN_RECOGNIZED')
        self.browser.trace.add('observe',observation,self.last_verification)
        return {'ok':True,'state':'observed','observation':observation.model_dump(),
            'verification':str(self.last_verification),'elements':[
                {'reference':ref,'role':item['role'],'label':item['label'],'sensitive':item['sensitive'],
                 'kind':item['key']} for ref,item in self.references.items()]}

    async def inspect(self):
        async with self.lock:
            if not self.navigation_count:
                return self.fail('INSPECT_BEFORE_NAVIGATION',terminal=False)
            try:
                return await self._inspect()
            except Exception:
                return self.fail('OBSERVATION_FAILED')

    async def _resolve(self,reference):
        descriptor=self.references.get(reference)
        if descriptor is None:
            return None,'UNOBSERVED_ELEMENT'
        self.browser.budget()
        if await self.browser.page.locator(descriptor['selector']).count():
            return descriptor,None
        if self.recovery_used:
            return None,'REDISCOVERY_EXHAUSTED'
        self.recovery_used=True
        self.browser.trace.add('recover')
        fresh=await self._inspect()
        if not fresh['ok']:
            return None,'REDISCOVERY_FAILED'
        matches=[item for item in self.references.values() if item['key']==descriptor['key'] and item['role']==descriptor['role']]
        if len(matches)!=1:
            return None,'ELEMENT_NOT_FOUND'
        return matches[0],None

    async def interact(self,reference,operation):
        async with self.lock:
            if self.terminal_error or self.last_verification in {Result.AUTH_FAILED,Result.SESSION_EXPIRED}:
                return self.fail('TERMINAL_STATE')
            try:
                descriptor,error=await self._resolve(reference)
                if error:
                    return self.fail(error)
                key=descriptor['key']
                if operation=='fill_fixture' and key in {'account','username','password'}:
                    # Fixed synthetic values stay inside the trusted boundary, never tool arguments.
                    value={'account':'synthetic','username':'tester','password':'fixture-only'}[key]
                    self.browser.budget()
                    await self.browser.page.locator(descriptor['selector']).fill(value)
                    return {'ok':True,'state':'field_filled','must_inspect':False}
                if operation=='submit_fixture' and key=='submit':
                    if 'reinspect_before_submit' in self.strategy.rules:
                        await self._inspect()
                        matches=[e for e in self.references.values() if e['key']=='submit']
                        if len(matches)!=1:
                            return self.fail('ELEMENT_NOT_FOUND')
                        descriptor=matches[0]
                        self.browser.trace.fact('REINSPECTED')
                    if 'wait_for_stable_controls' in self.strategy.rules:
                        await self.browser.page.wait_for_load_state('domcontentloaded',timeout=2000)
                        await self._inspect()
                        first=sorted((e['key'],e['role'],e['label']) for e in self.references.values())
                        await self._inspect()
                        second=sorted((e['key'],e['role'],e['label']) for e in self.references.values())
                        if first!=second:
                            return self.fail('CONTROLS_NOT_STABLE')
                        descriptor=next(e for e in self.references.values() if e['key']=='submit')
                        self.browser.trace.fact('STABLE_CONTROLS')
                    # Ensure the form's fields were all observed. Never infer credentials from prompts.
                    fields={item['key']:item for item in self.references.values() if item['key'] in {'account','username','password'}}
                    if len(fields)!=3:
                        return self.fail('UNOBSERVED_FORM')
                    self.browser.claim_login_submission()
                    self.browser.trace.fact('FIELD_ROLES_VALIDATED')
                    for name,value in [('account','synthetic'),('username','tester'),('password','fixture-only')]:
                        self.browser.budget()
                        await self.browser.page.locator(fields[name]['selector']).fill(value)
                    self.browser.budget()
                    await self.browser.page.locator(descriptor['selector']).send_click_event()
                    await self.browser.settle('#dashboard,#challenge,#rejected')
                    self.browser.trace.fact('SUBMITTED_ONCE')
                elif operation=='activate' and key in {'entry','challenge'}:
                    self.browser.budget()
                    await self.browser.page.locator(descriptor['selector']).send_click_event()
                    await self.browser.settle('input[type=password]' if key=='entry' else '#dashboard')
                else:
                    return self.fail('OPERATION_NOT_PERMITTED')
                self.references.clear()
                self.browser.trace.add('entry')
                if 'verify_after_action' in self.strategy.rules:
                    check=await self.browser.observe()
                    self.browser.trace.add('verify',check,verify(check))
                    self.browser.trace.fact('READ_ONLY_CHECK')
                return {'ok':True,'state':'acted','must_inspect':True}
            except PermissionError:
                return self.fail('LOGIN_SUBMISSION_LIMIT')
            except Exception:
                return self.fail('INTERACTION_FAILED')

    async def verify(self):
        async with self.lock:
            try:
                observation=await self.browser.observe()
                self.last_verification=verify(observation)
                self.browser.trace.add('verify',observation,self.last_verification)
                if self.last_verification==Result.AUTHENTICATED_AND_VERIFIED:
                    self.browser.trace.fact('DESTINATION_VERIFIED')
                return {'ok':True,'state':'verified','verification':str(self.last_verification)}
            except Exception:
                return self.fail('VERIFICATION_FAILED')

    def registered_tools(self):
        @tool
        async def navigate_approved(url: str) -> dict:
            """Navigate once to the exact controller-approved synthetic entry URL."""
            return await self.navigate(url)
        @tool
        async def inspect_page() -> dict:
            """Inspect sanitized DOM, accessible labels, current element references and page state."""
            return await self.inspect()
        @tool
        async def interact_observed(reference: str, operation: Literal['activate','fill_fixture','submit_fixture']) -> dict:
            """Act on a previously observed permitted element. Credential values are never accepted."""
            return await self.interact(reference,operation)
        @tool
        async def verify_dashboard() -> dict:
            """Independently verify authenticated dashboard and expected synthetic tenant access."""
            return await self.verify()
        return [navigate_approved,inspect_page,interact_observed,verify_dashboard]
