"""Compose bounded phases over the existing owned Stagehand browser lifecycle."""
import asyncio
import sys
from time import monotonic
from browser.stagehand_client import StagehandClient
from deep_agents.permissions import InferenceBudget
from modules.whatsapp.contract import SETUP_URL, SetupStop
from modules.login.verifier import verify
from app.state import Result
from modules.whatsapp.destination import validate_setup_destination

class Phase1Budget(InferenceBudget):
    def __init__(self,limits):
        super().__init__(limits.total_requests,role_limits={'doer':limits.login_requests+limits.setup_requests,'teacher':limits.teacher_requests})
        self.phase='login';self.stage_caps={'login':limits.login_requests,'setup':limits.setup_requests}
        self.stage_requests={'login':0,'setup':0}
    def claim(self,role='doer'):
        if role=='doer' and self.stage_requests[self.phase]>=self.stage_caps[self.phase]:raise RuntimeError('MODEL_BUDGET_EXHAUSTED')
        super().claim(role)
        if role=='doer':self.stage_requests[self.phase]+=1

class TaskSession(StagehandClient):
    """No launch/close override: process, context, profile and ownership stay identical."""
    def __init__(self,*args,login_seconds=180,**kwargs):
        super().__init__(*args,**kwargs)
        self.task_phase='login';self.phase_deadline=monotonic()+login_seconds
        self.phase_action_end=30;self.setup_navigation_count=0
        self.setup_tab_attempted=False;self.setup_binding=None;self.login_page=None
        self.setup_page=None
    def begin_phase(self,phase,seconds,actions):
        expected={'login':'checkpoint','checkpoint':'setup','setup':'inspection','inspection':'done'}
        if expected.get(self.task_phase)!=phase or not 1<=seconds<=310 or not 0<=actions<=36:raise SetupStop('INVALID_PHASE_TRANSITION')
        self.task_phase=phase;self.phase_deadline=monotonic()+seconds
        self.phase_action_end=self.trace.actions+actions
        self.trace.lifecycle('WHATSAPP',{'state':'PHASE_STARTED','stage':phase})
    def budget(self):
        if hasattr(self,'phase_deadline') and (monotonic()>self.phase_deadline or self.trace.actions>=self.phase_action_end):raise SetupStop('PHASE_BUDGET_EXHAUSTED')
        super().budget()
        if self.setup_binding is not None:
            from browser.protected_tabs import bound_tab_observable
            if not bound_tab_observable(self.guard,self.setup_binding):
                self.guard.fail_closed('SETUP_TAB_OBSERVATION',code='DOCUMENT_PROTECTION_UNCERTAIN')
                raise SetupStop('PROTECTED_SETUP_TARGET_UNAVAILABLE')

    async def open_setup_tab(self,grant):
        from browser.protected_tabs import create_protected_tab,TabProtectionError
        if (self.task_phase!='setup' or self.setup_tab_attempted or grant.browser is not self or not grant.route
            or self.page is not grant.source_page or self.login_submissions!=grant.login_submissions
            or source_signature(self)!=grant.source_signature or await self.page.url()!=grant.source_url):raise SetupStop('SETUP_TAB_GRANT_INVALID')
        self.setup_tab_attempted=True;self.budget();self.login_page=self.page
        try:
            page,binding=await create_protected_tab(self)
            self.setup_binding=binding;self.page=page;self.setup_page=page
            self.trace.lifecycle('WHATSAPP',{'state':'SETUP_TAB_PROTECTED','code':'PROTECTED_SAME_CONTEXT_TAB'})
            print('SETUP_TAB_PROTECTED',flush=True)
            self.trace.fact('WHATSAPP_TAB_PROTECTED')
        except Exception as error:
            self.guard.fail_closed('SETUP_TAB_BINDING',code='DOCUMENT_PROTECTION_UNCERTAIN')
            raise SetupStop(error.code if isinstance(error,TabProtectionError) else 'PROTECTED_SETUP_TAB_UNAVAILABLE') from None
    def claim_login_submission(self):
        if self.task_phase!='login':raise PermissionError('LOGIN_PHASE_CLOSED')
        super().claim_login_submission()
    async def navigate(self,url):
        if self.task_phase=='login':
            if url!=self.config.login_url:raise SetupStop('DESTINATION_NOT_AUTHORIZED')
        elif self.task_phase=='setup':
            validate_setup_destination(url)
            if self.setup_navigation_count or self.setup_binding is None:raise SetupStop('DESTINATION_NOT_AUTHORIZED')
            owner=self.guard.session_states.get(self.setup_binding.session)
            if (not owner or (owner.document_generation,owner.frame_id,owner.loader_id)!=(self.setup_binding.blank_document_generation,self.setup_binding.blank_frame_id,self.setup_binding.blank_loader_id)
                or await self.page.url()!='about:blank'):raise SetupStop('SETUP_BLANK_DOCUMENT_CHANGED')
            self.setup_navigation_count+=1
            self.trace.lifecycle('TAB_BINDING',{'state':'SETUP_NAVIGATION_STARTED','code':'AUTHORIZED_SETUP_NAVIGATION'})
            self.guard.notify_navigation('REQUESTED_SETUP_NAVIGATION',self.setup_binding.session,url)
        else:raise SetupStop('NAVIGATION_PHASE_CLOSED')
        await super().navigate(url)
        if self.task_phase=='setup':
            self.trace.lifecycle('WHATSAPP',{'state':'SETUP_NAVIGATION_RPC_COMPLETED','destination_ready':False})

class PocGrant:
    def __init__(self,browser):
        self.browser=browser;self.run_id=browser.trace.run_id
        self.source_page=browser.page;self.login_submissions=browser.login_submissions;self.route=None
        self.source_signature=None;self.source_url=None


def source_signature(browser):
    owner=browser.guard.session_states.get(browser.guard.primary_session)
    if not owner or not browser.guard.primary_observable():raise SetupStop('SOURCE_TARGET_UNAVAILABLE')
    return (id(browser.page),owner.target,owner.session,owner.generation,owner.document_generation)

def healthy_observation(browser,observation):
    if (browser.guard.failed or not browser.guard.primary_observable() or browser.guard.blocking_hosts or not observation.allowed or not observation.expected_host
        or any(getattr(observation,k) for k in ('credential_form','challenge','rejected','expired','account_restricted','login_entry'))):
        raise SetupStop('AUTHENTICATION_OR_SECURITY_STOP')
    submitted=any(f['kind']=='SUBMITTED_ONCE' for f in browser.trace.step_facts)
    if browser.login_submissions==1 and submitted:return 'SUBMITTED_ONCE_POC'
    if browser.login_submissions==0 and not submitted:
        if verify(observation)==Result.AUTHENTICATED_AND_VERIFIED and browser.evidence is not None and browser.evidence.reviewed:
            return 'EXISTING_SESSION_VERIFIED'
        raise SetupStop('EXISTING_SESSION_EVIDENCE_REQUIRED')
    raise SetupStop('SINGLE_COMPLETED_LOGIN_REQUIRED')

async def confirm_poc(browser,seconds):
    if not sys.stdin.isatty():raise SetupStop('INTERACTIVE_CHECKPOINT_REQUIRED')
    print('POC authentication checkpoint: inspect the authenticated account context in the SAME Chromium. Press C to confirm only for this Setup POC; Enter cancels. This does not certify independent authentication.',flush=True)
    async with asyncio.timeout(seconds):
        while True:
            key=None
            if sys.platform=='win32':
                import msvcrt
                if msvcrt.kbhit():key=msvcrt.getwch().lower()
            else:
                import select
                if select.select([sys.stdin],[],[],0)[0]:key=sys.stdin.readline().strip().lower() or '\n'
            if key=='c':return PocGrant(browser)
            if key in ('\n','\r'):raise SetupStop('POC_CHECKPOINT_DECLINED')
            if key=='\x03':raise KeyboardInterrupt
            await asyncio.sleep(.05)
