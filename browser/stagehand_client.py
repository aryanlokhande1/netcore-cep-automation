import asyncio
import json
import shutil
from browser.owned_process import OwnedProcess
from time import monotonic
from urllib.parse import urlsplit
from stagehand import Stagehand, local_browser
from app.config import ROOT
from app.state import Observation
from browser.navigation_guard import NavigationGuard


class BrowserBudgetExceeded(TimeoutError):
    def __init__(self, budget_kind, *, actions_used, actions_limit):
        codes = {
            "ACTION_LIMIT": "BROWSER_ACTION_BUDGET_EXHAUSTED",
            "SESSION_DEADLINE": "BROWSER_SESSION_DEADLINE_EXCEEDED",
            "REVIEW_DEADLINE": "BROWSER_REVIEW_DEADLINE_EXCEEDED",
        }
        self.budget_kind = budget_kind
        self.code = codes[budget_kind]
        self.owner = "browser"
        self.actions_used = actions_used
        self.actions_limit = actions_limit
        super().__init__(self.code)


class StagehandClient:
    def __init__(self, config, policy, trace, fixture=False, evidence=None, live_permit=None):
        self.config, self.policy, self.trace = config, policy, trace
        self.fixture, self.evidence = fixture, evidence
        self.live_permit=live_permit
        self.expected_account=None
        self.verification_details=None
        self.browser = self.stagehand = self.guard = None
        self.started = monotonic()
        self.process = None
        self.login_submissions = 0
        self._close_task=None
        self.close_started=False;self.stagehand_close_started=False;self.browser_close_started=False
        self.review_deadline=None
        self.login_submitted_at=None
        self.login_readiness=None
        self.login_security_negatives={}
        self.profile = ROOT / "profiles" / trace.run_id

    def lifecycle_context(self):
        return {'process_state':self.process.status_now() if self.process and hasattr(self.process,'status_now') else 'UNKNOWN',
            'owned_close_started':self.close_started,'stagehand_close_started':self.stagehand_close_started,
            'browser_close_started':self.browser_close_started}

    def begin_readonly_review(self,seconds):
        if not 1<=seconds<=300 or self.review_deadline is not None:raise PermissionError('Bounded single review phase required')
        # Explicit operator hold, plus a short final independent read. This does not
        # extend Doer execution or CDP command deadlines; writes stay disabled.
        self.review_deadline=monotonic()+seconds+10

    def claim_login_submission(self):
        if self.review_deadline is not None:raise PermissionError('READ_ONLY_REVIEW_PHASE')
        from app.safety import require_fixture_browser
        if self.fixture:
            require_fixture_browser(self)
        else:
            from app.safety import require_live_browser
            require_live_browser(self)
        if self.login_submissions >= self.config.max_login_submissions:
            raise PermissionError("LOGIN_SUBMISSION_LIMIT")
        self.login_submissions += 1

    def budget(self):
        actions_used=self.trace.actions
        actions_limit=self.config.max_actions
        if actions_used>=actions_limit:
            self.trace.lifecycle('BROWSER',{'state':'BROWSER_BUDGET_EXHAUSTED',
                'budget_kind':'ACTION_LIMIT','actions_used':actions_used,'actions_limit':actions_limit})
            raise BrowserBudgetExceeded('ACTION_LIMIT',
                actions_used=actions_used,actions_limit=actions_limit)
        if self.review_deadline is not None and monotonic()>self.review_deadline:
            self.trace.lifecycle('BROWSER',{'state':'BROWSER_BUDGET_EXHAUSTED',
                'budget_kind':'REVIEW_DEADLINE','actions_used':actions_used,'actions_limit':actions_limit})
            raise BrowserBudgetExceeded('REVIEW_DEADLINE',
                actions_used=actions_used,actions_limit=actions_limit)
        if self.review_deadline is None and monotonic()-self.started>self.config.timeout_seconds:
            self.trace.lifecycle('BROWSER',{'state':'BROWSER_BUDGET_EXHAUSTED',
                'budget_kind':'SESSION_DEADLINE','actions_used':actions_used,'actions_limit':actions_limit})
            raise BrowserBudgetExceeded('SESSION_DEADLINE',
                actions_used=actions_used,actions_limit=actions_limit)
        if self.guard and self.guard.failed:
            raise PermissionError("Navigation guard failed closed")
        observable=getattr(self.guard,'primary_observable',None)
        if self.stagehand is not None and callable(observable) and not observable():
            raise PermissionError('Primary document protection unavailable')
        self.trace.actions += 1

    async def start(self):
        from app.safety import require_fixture_browser
        if self.fixture:
            require_fixture_browser(self)
        else:
            from app.safety import require_live_browser
            require_live_browser(self,starting=True)
        self.budget()
        self.profile.mkdir(parents=True, exist_ok=False)
        self.browser = await local_browser.launch(user_data_dir=self.profile,executable_path=self.config.executable,
            headless=self.config.headless, chromium_sandbox=True, ignore_https_errors=False,
            accept_downloads=False, preserve_user_data_dir=False)
        self.trace.lifecycle('BROWSER',{'state':'BROWSER_CREATED','fixture':self.fixture})
        self.guard = NavigationGuard(self.policy,diagnostic=self.trace.lifecycle)
        self.guard.context_snapshot=self.lifecycle_context
        await self.guard.start(self.browser.cdp_url)
        processes = await self.guard.send("SystemInfo.getProcessInfo")
        pid = next(int(p['id']) for p in processes['processInfo'] if p['type'] == 'browser')
        self.process = OwnedProcess(pid)
        self.stagehand = await Stagehand.create(browser=self.browser,
            logging={"level": "off"})
        self.page = (await self.browser.context.pages())[0]
        self.trace.add("initialize")

    async def navigate(self, url):
        if self.review_deadline is not None:raise PermissionError('READ_ONLY_REVIEW_PHASE')
        self.budget()
        if not self.policy.permits(url):
            raise PermissionError("Navigation requires approval")
        await asyncio.wait_for(self.page.goto(url), 20)
        self.trace.lifecycle('BROWSER',{'state':'NAVIGATION_COMPLETED'})
        self.trace.add("navigate")

    async def settle(self, selector):
        # Stagehand may report context destruction during cross-document navigation.
        # Retry read-only waits, never clicks or credential submissions.
        for retry in range(5):
            self.budget()
            try:
                if await self.page.wait_for_selector(selector, timeout=2000):
                    return
            except Exception:
                if retry == 4:
                    raise
            await asyncio.sleep(.1)
        raise TimeoutError("Expected fixture state did not appear")

    async def authentication_context(self):
        """Private context key, including SPA location; never exported raw."""
        from browser.discovery import login_page_context
        facts=await login_page_context(self)
        if not facts.get('primary_document_coherent'):return None
        owner=self.guard.session_states.get(self.guard.primary_session)
        return (owner.target,owner.session,owner.generation,owner.document_generation,
                owner.frame_id,owner.loader_id,await self.page.url(),
                getattr(getattr(self,'discovery',None),'navigation_epoch',0))

    async def readiness_sample(self):
        from modules.login.discovery import READINESS_DOM
        await self.guard.ensure_observable()
        self.budget()
        url=await self.page.url()
        if not self.policy.permits(url) or self.guard.blocking_hosts:
            raise PermissionError('POST_LOGIN_ORIGIN_BLOCKED')
        owner=self.guard.session_states.get(self.guard.primary_session)
        before=(owner.target,owner.session,owner.generation,owner.document_generation,
                owner.frame_id,owner.loader_id) if owner else None
        scan_context=(*before,url) if before else None
        if getattr(self,'readiness_scan_context',None)!=scan_context:
            self.readiness_scan_context=scan_context;self.readiness_scan_budget=512
        scan_budget=getattr(self,'readiness_scan_budget',512)
        result=await self.page.evaluate(READINESS_DOM.replace('const limit=512;',f'const limit={scan_budget};'))
        after=self.guard.session_states.get(self.guard.primary_session)
        identity=(after.target,after.session,after.generation,after.document_generation,
                  after.frame_id,after.loader_id) if after else None
        if before is None or before!=identity or url!=await self.page.url():
            self.readiness_scan_context=None
            return {'context':None,'expected_host':False,'loading':True,'scan_limited':True,
                'unknown_reason':'READINESS_DOCUMENT_CHANGED'}
        self.readiness_scan_budget=min(12000,scan_budget*2) if result.get('scan_limited') else scan_budget
        return {**result,'context':(*before,url),
                'scan_budget':scan_budget,'next_scan_budget':self.readiness_scan_budget,
                'expected_host':urlsplit(url).hostname==self.config.expected_host}

    async def wait_for_application_ready(self):
        """Local bounded post-submit state machine; no LLM, screenshots or writes."""
        start=self.login_submitted_at if self.login_submitted_at is not None else monotonic()
        outer=self.started+self.config.timeout_seconds
        deadline=min(start+self.config.login_ready_seconds,outer)
        ceiling=min(start+self.config.login_ready_max_seconds,outer)
        stable_since=None;stable_key=None;last_state=None;extended=False
        progress_seen=set();recent_progress=None;polls=0
        state='LOGIN_SUBMITTED';sample={}
        def record(state,**extra):
            nonlocal last_state
            value={'state':state,'polls':polls,'elapsed_ms':round((monotonic()-start)*1000),
                   'deadline_extended':extended,
                   'scan_limited':bool(sample.get('scan_limited')),
                   'shell_status':'OBSERVED' if sample.get('shell') else 'UNKNOWN' if sample.get('scan_limited') else 'NOT_OBSERVED',
                   'shell_landmarks':sample.get('shell_landmarks',[]),
                   'security_scope':'NOT_CERTIFIED_BY_READINESS',
                   'nodes_inspected':sample.get('nodes_inspected'),
                   'pending_nodes':sample.get('pending_nodes'),
                   'scan_budget':sample.get('scan_budget'),
                   'next_scan_budget':sample.get('next_scan_budget'),
                   'unknown_reason':sample.get('unknown_reason'),**extra}
            self.login_readiness=value
            if state!=last_state or extra.get('code'):
                self.trace.lifecycle('LOGIN_READINESS',value);last_state=state
            return value
        record(state)
        while monotonic()<deadline:
            remaining=deadline-monotonic()
            try:
                sample=await asyncio.wait_for(self.readiness_sample(),remaining)
            except PermissionError:
                record('UNEXPECTED_PAGE',code='POST_LOGIN_PROTECTED_CONTEXT_UNAVAILABLE')
                raise
            except TimeoutError:
                if self.trace.actions>=self.config.max_actions or monotonic()>=outer:
                    record(state,code='POST_LOGIN_BROWSER_BUDGET_EXHAUSTED')
                    raise
                sample={'context':None,'expected_host':False,'loading':True}
            except Exception:
                sample={'context':None,'expected_host':False,'loading':True}
            polls+=1;now=monotonic()
            for flag,negative_state in [('rejected','AUTH_FAILED'),('account_restricted','AUTH_FAILED'),
                    ('expired','SESSION_EXPIRED'),('challenge','HUMAN_ACTION_REQUIRED')]:
                if sample.get(flag):
                    self.login_security_negatives[flag]=True
                    return record(negative_state,code='POST_LOGIN_SECURITY_NEGATIVE')
            key=(sample.get('context'),sample.get('expected_host'),sample.get('shell'),
                 sample.get('loading'),sample.get('credential_form'))
            # Only new context/semantic milestones can justify an extension, not
            # animation, resource polling or repeating an identical observation.
            if sample.get('context') is not None and key not in progress_seen and len(progress_seen)<16:
                progress_seen.add(key);recent_progress=now
            ready=bool(sample.get('context') and sample.get('expected_host') and
                       sample.get('shell') and not sample.get('loading') and not sample.get('credential_form'))
            if ready:
                if key!=stable_key:stable_key=key;stable_since=now
                if now-stable_since>=self.config.login_stable_seconds:
                    return record('APPLICATION_READY_AUTH_UNVERIFIED')
                state='APPLICATION_LOADING'
            else:
                stable_since=stable_key=None
                state='APPLICATION_LOADING' if sample.get('expected_host') else 'TRANSITIONING'
            record(state)
            if not extended and now+self.config.login_poll_seconds>=deadline and recent_progress is not None and recent_progress>start and now-recent_progress<=2*self.config.login_poll_seconds:
                extended=ceiling>deadline;deadline=ceiling
            delay=min(self.config.login_poll_seconds,max(0,deadline-monotonic()))
            signal=getattr(getattr(self,'discovery',None),'context_changed',None)
            if signal is None:
                await asyncio.sleep(delay)
            else:
                # This event belongs to the existing passive observer, not the
                # guard's shared lifecycle event. Never clear guard-owned signals.
                try:await asyncio.wait_for(signal.wait(),delay)
                except TimeoutError:pass
                signal.clear()
        return record(state,code='POST_LOGIN_READINESS_SCOPE_INCOMPLETE' if sample.get('scan_limited') and not sample.get('shell') else 'POST_LOGIN_READINESS_TIMEOUT')

    async def observe(self):
        from stagehand.rpc_client import RPCError
        for retry in range(4):
            try:
                observation=await self._observe_once()
                observable=getattr(self.guard,'primary_observable',None)
                if self.guard and (self.guard.failed or (callable(observable) and not observable())):
                    raise PermissionError('Protection lost during independent observation')
                if self.guard and self.guard.blocking_hosts:return Observation(allowed=False)
                return observation
            except RPCError:
                if retry == 3:
                    raise
                await asyncio.sleep(.15)

    async def _observe_once(self):
        if self.guard and hasattr(self.guard,'ensure_observable'):
            await self.guard.ensure_observable()
        self.budget()
        if self.login_submitted_at is None:
            await self.page.wait_for_load_state("domcontentloaded", timeout=5000)
        url = await self.page.url()
        if not self.policy.permits(url) or self.guard.blocking_hosts:
            return Observation(allowed=False)
        if self.fixture:
            result = await self.page.evaluate('''(async () => {
                const has = s => !!document.querySelector(s);
                let readonly = false;
                if (has('#dashboard')) {
                    const r = await fetch('/context');
                    const data = r.ok ? await r.json() : {};
                    readonly = r.ok && data.authenticated === true && data.tenant === 'synthetic-tenant';
                }
                return {credential_form:has('input[type=password]'),challenge:has('#challenge'),
                    rejected:has('#rejected'),expired:has('#expired'),dashboard:has('#dashboard'),
                    tenant_matches:document.querySelector('#tenant')?.textContent === 'synthetic-tenant',
                    readonly_access:readonly,evidence_configured:true,standard_entry:has('#entry-standard'),
                    alternate_entry:has('#entry-alternate')};
            })()''')
        else:
            from modules.login.evidence import observe_live
            return await observe_live(self,url)
        return Observation(**result, expected_host=self.fixture or urlsplit(url).hostname == self.config.expected_host)

    async def entry(self, action):
        if self.review_deadline is not None:raise PermissionError('READ_ONLY_REVIEW_PHASE')
        # Only observed synthetic controls can be clicked by the agent. Live credentials belong to the human.
        if not self.fixture or action not in {"standard_entry", "alternate_entry"}:
            raise PermissionError("Unsupported entry action")
        self.budget()
        await self.page.locator('#entry-standard' if action == 'standard_entry' else '#entry-alternate').click()
        await self.settle('input[type=password]')

    async def close(self):
        self.close_started=True
        from browser.lifecycle import close_owned_session
        if self._close_task is None:
            self._close_task=asyncio.create_task(close_owned_session(self))
        try:
            return await asyncio.shield(self._close_task)
        except asyncio.CancelledError:
            # Complete the bounded owned-session cleanup before honoring cancellation.
            await asyncio.shield(self._close_task)
            raise
