"""Live-only tools: opaque role mapping, trusted submission, read-only verification."""
import json
from uuid import uuid4
from langchain_core.tools import tool
from app.safety import require_live_browser
from app.state import Result, Observation
from langchain.agents.middleware import AgentMiddleware
from modules.login.verifier import verify
from deep_agents.browser_tools import BrowserTools
from browser.live_dom import LIVE_DOM

class LoginCompletionGate(AgentMiddleware):
    def __init__(self,tools):self.login_tools=tools
    async def awrap_model_call(self,request,handler):
        if getattr(self.login_tools,'required_login_skill',None) and not self.login_tools.approved_login_skill_loaded:
            request=request.override(response_format=None,tool_choice='read_approved_skill')
        elif self.login_tools.submission_pending():
            # The actual Doer still supplies the observed opaque references. The
            # unchanged credential broker performs fresh validation before any write.
            self.login_tools.browser.trace.lifecycle('LOGIN',{'state':'LOGIN_ACTION_REQUIRED','code':'NORMAL_LOGIN_FORM_REQUIRES_SUBMISSION'})
            request=request.override(response_format=None,tool_choice='submit_login')
        return await handler(request)

class LiveBrowserTools(BrowserTools):
    def __init__(self,browser,broker):
        # The permit is checked again at launch; no fixture flag can authorize live.
        self.browser,self.broker=browser,broker
        self.entry_url=browser.config.login_url
        self.references={}
        self.recovery_used=False
        self.navigation_count=0
        self.terminal_error=False
        self.last_verification=Result.NOT_TESTED
        import asyncio
        self.lock=asyncio.Lock()
        from browser.diagnostics import ScreenshotDiagnostics
        self.screenshots=ScreenshotDiagnostics(browser)

    def completion_middleware(self):return LoginCompletionGate(self)
    def submission_pending(self):
        browser=self.browser
        if not self.navigation_count or self.terminal_error or browser.login_submissions or not browser.trace.current_observation:return False
        if not browser.guard or browser.guard.failed or not browser.guard.primary_observable() or browser.guard.blocking_hosts:return False
        observation=Observation.model_validate(browser.trace.current_observation)
        if not (observation.allowed and observation.login_entry and observation.credential_form):return False
        if any(getattr(observation,k) for k in ('challenge','rejected','expired','account_restricted')):return False
        roles=[e['key'] for e in self.references.values()]
        return len(roles)==4 and set(roles)=={'account','username','password','submit'}

    async def _inspect(self):
        require_live_browser(self.browser)
        observation=await self.browser.observe()
        self.last_verification=verify(observation)
        if not observation.allowed:
            self.browser.trace.add('observe',observation,self.last_verification)
            return self.fail('ORIGIN_POLICY_BLOCKED')
        self.browser.budget()
        prefix=uuid4().hex
        elements=await self.browser.page.evaluate(LIVE_DOM.replace('PREFIX',json.dumps(prefix)))
        self.references={f'{prefix}-{i}':item for i,item in enumerate(elements)}
        # Destination inspection can contain no login controls; retain prior role attribution.
        self.browser.trace.role_evidence.update({e['key']:e['evidence'] for e in elements})
        self.browser.trace.add('observe',observation,self.last_verification)
        if len([e for e in elements if e['key'] in {'account','username','password','submit'}])==4:
            self.browser.trace.fact('LOGIN_RECOGNIZED')
        return {'ok':True,'state':'observed','observation':observation.model_dump(),
            'verification':str(self.last_verification),'verification_details':self.browser.verification_details,
            'elements':[{'reference':ref,'role':e['role'],'label':e['label'],'kind':e['key'],'sensitive':e['sensitive']} for ref,e in self.references.items()]}

    async def submit(self,account_reference,username_reference,password_reference,submit_reference):
        async with self.lock:
            if self.terminal_error or self.browser.login_submissions:
                return self.fail('LOGIN_SUBMISSION_LIMIT')
            try:
                refs={'account':account_reference,'username':username_reference,'password':password_reference,'submit':submit_reference}
                # Validate all original opaque references before recovery can replace the snapshot.
                if any(ref not in self.references or self.references[ref]['key']!=kind for kind,ref in refs.items()):
                    return self.fail('UNOBSERVED_ELEMENT')
                mapping={}
                recovered=self.recovery_used
                for kind,reference in refs.items():
                    descriptor,error=await self._resolve(reference)
                    if error or descriptor['key']!=kind:
                        return self.fail(error or 'FIELD_ROLE_MISMATCH')
                    mapping[kind]=descriptor
                    if self.recovery_used!=recovered:
                        # One rediscovery rebinds every role together; old refs never resolve individually again.
                        mapping={}
                        for role in refs:
                            matches=[e for e in self.references.values() if e['key']==role]
                            if len(matches)!=1:
                                return self.fail('ELEMENT_NOT_FOUND')
                            mapping[role]=matches[0]
                        break
                rules=getattr(getattr(self,'strategy',None),'rules',())
                if 'reinspect_before_submit' in rules or 'wait_for_stable_controls' in rules:
                    first=sorted((e['key'],e['role'],e['label']) for e in mapping.values())
                    fresh=await self._inspect()
                    if not fresh['ok']:
                        return fresh
                    mapping={}
                    for role in refs:
                        matches=[e for e in self.references.values() if e['key']==role]
                        if len(matches)!=1:
                            return self.fail('ELEMENT_NOT_FOUND')
                        mapping[role]=matches[0]
                    self.browser.trace.fact('REINSPECTED')
                    if 'wait_for_stable_controls' in rules:
                        second=sorted((e['key'],e['role'],e['label']) for e in mapping.values())
                        if first!=second:
                            return self.fail('CONTROLS_NOT_STABLE')
                        self.browser.trace.fact('STABLE_CONTROLS')
                await self.broker.submit(mapping)
                if 'verify_after_action' in rules:
                    check=await self.browser.observe()
                    self.browser.trace.add('verify',check,verify(check))
                    self.browser.trace.fact('READ_ONLY_CHECK')
                self.references.clear()
                self.browser.trace.add('entry')
                return {'ok':True,'state':'acted','must_inspect':True,'login_submissions':self.browser.login_submissions}
            except PermissionError:
                return self.fail('CREDENTIAL_MAPPING_OR_SUBMISSION_BLOCKED')
            except Exception:
                return self.fail('SUBMISSION_FAILED_NO_RETRY')

    def registered_tools(self):
        @tool
        async def navigate_approved(url:str)->dict:
            """Navigate once to the approved Netcore login URL; arbitrary URLs are forbidden."""
            return await self.navigate(url)
        @tool
        async def inspect_page()->dict:
            """Inspect canonical visible login field roles and sanitized verification information."""
            return await self.inspect()
        @tool
        async def submit_login(account_reference:str,username_reference:str,password_reference:str,submit_reference:str)->dict:
            """Submit once using observed opaque field references. Never accepts credential values."""
            return await self.submit(account_reference,username_reference,password_reference,submit_reference)
        @tool
        async def verify_dashboard()->dict:
            """Independently verify reviewed dashboard, expected account and authenticated read-only evidence."""
            result=await self.verify()
            result['verification_details']=self.browser.verification_details
            return result
        return [navigate_approved,inspect_page,submit_login,verify_dashboard]
