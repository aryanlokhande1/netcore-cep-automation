"""Native Deep Agents restrictions plus an independent application tool gate."""
from dataclasses import dataclass,field
from time import monotonic
from deepagents import HarnessProfile, GeneralPurposeSubagentProfile, register_harness_profile
from langchain.agents.middleware import AgentMiddleware
from langchain_core.messages import ToolMessage
from app.config import ConfigurationError

EXCLUDED_TOOLS = frozenset({'ls','read_file','write_file','edit_file','glob','grep','execute','task',
    'write_todos','task_status','task_update','task_cancel'})


def restrict_harness(model):
    provider=model._get_ls_params().get('ls_provider')
    identifier=getattr(model,'model_name',None)
    if provider not in {'openai','pass1_offline'} or not identifier:
        raise ConfigurationError('Only explicit OpenAI or labeled offline models are supported')
    register_harness_profile(f'{provider}:{identifier}',HarnessProfile(
        excluded_tools=EXCLUDED_TOOLS,
        excluded_middleware=frozenset({'SummarizationMiddleware'}),
        general_purpose_subagent=GeneralPurposeSubagentProfile(enabled=False)))

@dataclass
class TokenPool:
    input_limit:int=240000
    output_limit:int=21000
    input_reserved:int=0
    output_reserved:int=0
    def reserve(self,inputs,outputs):
        if self.input_reserved+inputs>self.input_limit or self.output_reserved+outputs>self.output_limit:
            raise RuntimeError('TOTAL_TOKEN_BUDGET_EXHAUSTED')
        self.input_reserved+=inputs;self.output_reserved+=outputs

@dataclass
class InferenceBudget:
    max_requests: int = 8
    requests: int = 0
    reserve_teacher: bool = False
    teacher_request_reserve: int = 1
    role_limits: dict | None = None
    role_requests: dict = field(default_factory=lambda:{'doer':0,'teacher':0})

    max_input_tokens:int=240000
    max_output_tokens:int=21000
    reserved_input:int=0
    reserved_output:int=0
    token_pool:TokenPool|None=None

    teacher_reserve_input:int=0
    teacher_reserve_output:int=0

    def token_operands(self, input_bound, output_cap, *, role):
        pool = self.token_pool
        hold = bool(pool and role != 'teacher' and not self.role_requests.get('teacher'))
        reserved_input = pool.input_reserved if pool else self.reserved_input
        reserved_output = pool.output_reserved if pool else self.reserved_output
        input_limit = pool.input_limit if pool else self.max_input_tokens
        output_limit = pool.output_limit if pool else self.max_output_tokens
        teacher_input = self.teacher_reserve_input if hold else 0
        teacher_output = self.teacher_reserve_output if hold else 0
        return {'prior_input_reserved': reserved_input, 'prior_output_reserved': reserved_output,
                'input_bound_with_margin': input_bound, 'max_output_tokens': output_cap,
                'teacher_input_reserve': teacher_input, 'teacher_output_reserve': teacher_output,
                'input_limit': input_limit, 'output_limit': output_limit,
                'budget_input_reserved': self.reserved_input, 'budget_output_reserved': self.reserved_output,
                'budget_input_limit': self.max_input_tokens, 'budget_output_limit': self.max_output_tokens,
                'budget_input_over_limit': self.reserved_input + input_bound > self.max_input_tokens,
                'budget_output_over_limit': self.reserved_output + output_cap > self.max_output_tokens,
                'input_over_limit': reserved_input + input_bound + teacher_input > input_limit,
                'output_over_limit': reserved_output + output_cap + teacher_output > output_limit}

    def reserve_tokens(self,input_bound,output_cap,*,role='doer'):
        operands = self.token_operands(input_bound, output_cap, role=role)
        if (operands['teacher_input_reserve'] or operands['teacher_output_reserve']) and (
                operands['input_over_limit'] or operands['output_over_limit']):
            raise RuntimeError('FINAL_TEACHER_TOKEN_RESERVE')
        if self.reserved_input+input_bound>self.max_input_tokens or self.reserved_output+output_cap>self.max_output_tokens:
            raise RuntimeError('TOTAL_TOKEN_BUDGET_EXHAUSTED')
        if self.token_pool:self.token_pool.reserve(input_bound,output_cap)
        self.reserved_input+=input_bound;self.reserved_output+=output_cap

    def claim(self,role='doer'):
        if role not in {'doer','teacher','observer'}:raise ValueError('Unknown model role')
        self.role_requests.setdefault(role,0)
        if self.role_limits and self.role_requests[role]>=self.role_limits[role]:
            raise RuntimeError('MODEL_BUDGET_EXHAUSTED')
        limit=self.max_requests-(self.teacher_request_reserve if self.reserve_teacher and role in {'doer','observer'} else 0)
        if self.requests >= limit:
            raise RuntimeError('MODEL_BUDGET_EXHAUSTED')
        self.requests += 1
        self.role_requests[role] += 1

def record_provider_messages(trace,diagnostic,messages):
    if diagnostic.get('usage_recorded'):return
    capture=getattr(trace,'agent_trace_enabled',False)
    if capture and diagnostic.get('usage_processed'):return
    diagnostic['status']='RETURNED'
    found=False
    for message in messages:
        metadata=getattr(message,'response_metadata',None) or {}
        finish=metadata.get('finish_reason') or metadata.get('status')
        if finish in {'stop','length','tool_calls','completed','incomplete','failed','content_filter'}:diagnostic['finish_status']=finish
        incomplete=metadata.get('incomplete_details') or {}
        if isinstance(incomplete,dict) and incomplete.get('reason') in {'max_output_tokens','content_filter'}:diagnostic['incomplete_reason']=incomplete['reason']
        usage=getattr(message,'usage_metadata',None) or {}
        if usage:
            found=True;trace.token_usage_available=True;trace.role_usage[diagnostic['role']]['usage_available']=True
        detail=usage.get('output_token_details') or {}
        if isinstance(detail.get('reasoning'),int):diagnostic['reasoning_tokens']=detail['reasoning']
        cached=usage.get('input_token_details') or {}
        if isinstance(cached.get('cache_read'),int):diagnostic['cached_input_tokens']=cached['cache_read']
        for key in ('input_tokens','output_tokens','total_tokens'):
            value=usage.get(key) if capture else usage.get(key,0)
            if isinstance(value,int) and value>=0:
                trace.token_usage[key]+=value;trace.role_usage[diagnostic['role']][key]+=value
                diagnostic[key]=diagnostic.get(key,0)+value
    diagnostic['usage_recorded']=found and (not capture or all(type(diagnostic.get(key)) is int for key in ('input_tokens','output_tokens','total_tokens')))
    if capture:diagnostic['usage_processed']=True


def model_failure_details(error,diagnostic=None):
    """Safe model/parser diagnostics: never exception text or validation inputs."""
    import hashlib,json
    safe_fields={'owner','finding','evidence_refs','code_fix_required','safe_next_iteration',
        'instructions','change_summary','recommended_code_repairs','execution_facts',
        'browser_creation_observed','login_tool_calls','task_tool_calls',
        'credential_submissions_observed','observation_records','goal','when','action','field'}
    internal={'OUTPUT_LIMIT_INCOMPLETE','PROVIDER_RESPONSE_INCOMPLETE','MISSING_STRUCTURED_REPORT',
        'APPROVED_SKILL_NOT_LOADED','MODEL_BUDGET_EXHAUSTED','TOTAL_TOKEN_BUDGET_EXHAUSTED',
        'FINAL_TEACHER_TOKEN_RESERVE','DOER_TOOL_CALL_BUDGET_EXHAUSTED',
        'EVIDENCE_CONTEXT_BUDGET_EXCEEDED','COMPARISON_CONTEXT_BUDGET_EXCEEDED',
        'INVALID_FACT_REFERENCE','INVALID_CANONICAL_REFERENCE','NON_CANONICAL_EVIDENCE',
        'RETRIEVAL_REFERENCE_BUDGET','UNKNOWN_CANONICAL_REFERENCE','RETRIEVAL_TOKEN_BUDGET',
        'MODEL_BOUNDARY_CONTEXT_BUDGET_OR_HISTORY','MISSING_OUTPUT_LIMIT',
        'TEACHER_INPUT_BOUND_EXCEEDS_CAP','TEACHER_OTHER_HISTORY_PRESENT',
        'TEACHER_REQUIRES_ONE_USER_MESSAGE','TEACHER_CONVERSATION_CONTINUATION'}
    details={'exception_class':type(error).__name__,'code':'MODEL_OR_CONTROLLER_EXCEPTION','validation_errors':[],'cause_classes':[]}
    if len(getattr(error,'args',()))==1 and isinstance(error.args[0],str) and error.args[0] in internal:details['code']=error.args[0]
    current=error;seen=set()
    for _ in range(8):
        if current is None or id(current) in seen:break
        seen.add(id(current))
        details['cause_classes'].append(type(current).__name__)
        args=getattr(current,'args',())
        if details['code']=='MODEL_OR_CONTROLLER_EXCEPTION' and len(args)==1 and isinstance(args[0],str) and args[0] in internal:
            details['code']=args[0]
        # Only the known compiler failure may supply these bounded numeric facts.
        if len(args)==1 and args[0]=='EVIDENCE_CONTEXT_BUDGET_EXCEEDED':
            for key in ('evidence_tokens','evidence_token_limit'):
                value=getattr(current,key,None)
                if type(value) is int and 0<=value<=1000000000:details[key]=value
        if callable(getattr(current,'errors',None)):
            try:
                for item in current.errors(include_input=False,include_context=False,include_url=False)[:16]:
                    details['validation_errors'].append({'location':[v if type(v) is int or v in safe_fields else '[field redacted]' for v in item['loc']],
                        'type':item['type']})
            except TypeError:pass
        current=getattr(current,'source',None) or getattr(current,'__cause__',None)
    message=getattr(error,'ai_message',None)
    if message is not None:
        details['response_sha256']=hashlib.sha256(json.dumps(message.content,sort_keys=True).encode()).hexdigest()
    if diagnostic:
        details.update({k:diagnostic[k] for k in ('finish_status','incomplete_reason','input_tokens','output_tokens','total_tokens','reasoning_tokens') if k in diagnostic})
    return details

class PermissionMiddleware(AgentMiddleware):
    def __init__(self, allowed, trace, budget, offline,role='doer',navigation_state=None,max_tool_calls=20,*,tool_budget_error=None):
        self.allowed=frozenset(allowed)
        self.role=role
        self.trace,self.budget,self.offline=trace,budget,offline
        self.registered_names=set()
        self.tool_calls=0
        self.navigation_state=navigation_state
        self.max_tool_calls=max_tool_calls
        if tool_budget_error not in {None,'DOER_TOOL_CALL_BUDGET_EXHAUSTED'}:
            raise ValueError('Unsupported tool budget error code')
        self.tool_budget_error=tool_budget_error

    async def awrap_model_call(self,request,handler):
        # Framework profiles remove built-ins; this additional allowlist fails closed
        # if future framework versions inject a new tool.
        permitted=[]
        for item in request.tools:
            name=item.name if hasattr(item,'name') else item.get('name',item.get('function',{}).get('name'))
            if name in self.allowed:
                if self.navigation_state is not None and not self.navigation_state() and name not in {'navigate_approved','read_approved_skill','DoerReport'}:
                    continue
                permitted.append(item)
                self.registered_names.add(name)
        self.budget.claim(self.role)
        self.trace.reasoning_turns += 1
        self.trace.role_usage[self.role]['requests'] += 1
        if not self.offline:
            self.trace.model_calls += 1
        diagnostic={'role':self.role,'stage':'MODEL_CALL','status':'STARTED','request':self.budget.role_requests[self.role]}
        self.trace.model_diagnostics.append(diagnostic)
        started=monotonic()
        capture=getattr(self.trace,'agent_trace_enabled',False)
        invocation=self.trace.agent_event('model_invocation',role=self.role,
            request=diagnostic['request'],status='STARTED') if capture else None
        if capture:diagnostic.update(invocation_id=invocation,task_stage=self.trace.agent_stage,phase=self.trace.agent_phase)
        try:response=await handler(request.override(tools=permitted))
        except BaseException as error:
            diagnostic['status']='FAILED'
            diagnostic['failure']=model_failure_details(error,diagnostic)
            if capture:self.trace.agent_event('model_result',role=self.role,invocation_id=invocation,
                status='FAILED',error=type(error).__name__,failure=diagnostic['failure'],duration_ms=round((monotonic()-started)*1000,3))
            raise
        record_provider_messages(self.trace,diagnostic,getattr(response,'result',[]))
        code=('OUTPUT_LIMIT_INCOMPLETE' if diagnostic.get('incomplete_reason')=='max_output_tokens' or diagnostic.get('finish_status')=='length'
              else 'PROVIDER_RESPONSE_INCOMPLETE' if diagnostic.get('finish_status') in {'incomplete','failed','content_filter'} else None)
        if code:
            diagnostic['status']='FAILED';diagnostic['failure']=model_failure_details(RuntimeError(code),diagnostic)
            if capture:self.trace.agent_event('model_result',role=self.role,invocation_id=invocation,
                status='FAILED',error=code,failure=diagnostic['failure'],duration_ms=round((monotonic()-started)*1000,3))
            raise RuntimeError(code)
        if capture:self.trace.agent_event('model_result',role=self.role,invocation_id=invocation,
            status='RETURNED',duration_ms=round((monotonic()-started)*1000,3),usage={k:diagnostic.get(k) for k in
                ('input_tokens','output_tokens','total_tokens','cached_input_tokens','reasoning_tokens')})
        for message in getattr(response,'result',[]):
            for call in getattr(message,'tool_calls',[]) or []:self.trace.tool(call.get('name'),self.role,'requested')
        return response

    async def awrap_tool_call(self,request,handler):
        self.tool_calls += 1
        capture=getattr(self.trace,'agent_trace_enabled',False);started=monotonic()
        invocation=self.trace.agent_event('tool_invocation',role=self.role,tool=request.tool_call['name'],
            arguments=self.trace.safe_tool_arguments(request.tool_call['name'],request.tool_call.get('args')),
            status='REQUESTED') if capture else None
        if request.tool_call['name'] in self.allowed and self.tool_calls>self.max_tool_calls and self.tool_budget_error:
            self.trace.tool(request.tool_call['name'],self.role,'denied')
            if capture:self.trace.agent_event('tool_result',role=self.role,invocation_id=invocation,
                tool=request.tool_call['name'],status='DENIED',error=self.tool_budget_error,
                tool_calls_used=self.tool_calls,tool_calls_limit=self.max_tool_calls,
                duration_ms=round((monotonic()-started)*1000,3))
            raise RuntimeError(self.tool_budget_error)
        if request.tool_call['name'] not in self.allowed or self.tool_calls > self.max_tool_calls:
            self.trace.tool(request.tool_call['name'],self.role,'denied')
            self.trace.add('error',error='POLICY')
            if capture:self.trace.agent_event('tool_result',role=self.role,invocation_id=invocation,
                tool=request.tool_call['name'],status='DENIED',error='TOOL_FORBIDDEN',duration_ms=round((monotonic()-started)*1000,3))
            return ToolMessage(content='{"ok":false,"error":"TOOL_FORBIDDEN"}',tool_call_id=request.tool_call['id'])
        self.trace.tool(request.tool_call['name'],self.role,'started')
        try:
            response=await handler(request)
        except BaseException as error:
            self.trace.tool(request.tool_call['name'],self.role,'failed')
            if capture:self.trace.agent_event('tool_result',role=self.role,invocation_id=invocation,
                tool=request.tool_call['name'],status='FAILED',error=type(error).__name__,duration_ms=round((monotonic()-started)*1000,3))
            raise
        self.trace.tool(request.tool_call['name'],self.role,'returned')
        if capture:self.trace.agent_event('tool_result',role=self.role,invocation_id=invocation,
            tool=request.tool_call['name'],status='RETURNED',duration_ms=round((monotonic()-started)*1000,3),
            result={'browser_result_recorded_separately':True} if request.tool_call['name'] in
                {'browser_action','inspect_ga_setup','view_ga_setup'}
                else self.trace.safe_tool_result(response))
        return response
