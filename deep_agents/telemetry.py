"""Count/hash actual provider request components without persisting their content."""
from pydantic import PrivateAttr
from langchain_core.callbacks import AsyncCallbackHandler
from langchain_openai import ChatOpenAI
from deep_agents.evidence import canonical,tokens,digest
from deep_agents.review_contract import EvidenceError

class UsageBoundary(AsyncCallbackHandler):
    def __init__(self,model):self.model=model
    async def on_llm_end(self,response,**kwargs):
        model=self.model;trace=model._trace
        calls=[d for d in trace.model_diagnostics if d['role']==model._role]
        if not calls:return
        from deep_agents.permissions import record_provider_messages
        messages=[g.message for batch in response.generations for g in batch if hasattr(g,'message')]
        record_provider_messages(trace,calls[-1],messages)

class MeteredChatOpenAI(ChatOpenAI):
    _trace=PrivateAttr(default=None)
    _budget=PrivateAttr(default=None)
    _role=PrivateAttr(default='teacher')
    _input_cap=PrivateAttr(default=8000)
    _authored_prompt=PrivateAttr(default='')
    def bind_meter(self,trace,budget,role):
        self._trace=trace;self._budget=budget;self._role=role
        self.callbacks=[UsageBoundary(self)]
        trace.model_limits[role]=self.max_tokens
        return self
    def _get_request_payload(self,input_,*,stop=None,**kwargs):
        payload=super()._get_request_payload(input_,stop=stop,**kwargs)
        if self._trace is None:return payload
        instructions=payload.get('instructions','')
        evidence=[];system=[];other=[]
        for message in payload.get('input',[]):
            role=message.get('role') if isinstance(message,dict) else None
            if role in {'system','developer'}:system.append(message)
            elif role=='user':evidence.append(message)
            else:other.append(message)
        fmt=payload.get('text',{}).get('format',{})
        components={'system_and_middleware':tokens(system)+tokens(instructions),
            'evidence_messages':tokens(evidence),'schema_and_tools':tokens(fmt)+tokens(payload.get('tools',[])),
            'other_message_history':tokens(other)}
        measured=sum(components.values());bound=measured+512
        cap=payload.get('max_output_tokens',self.max_tokens)
        failures = []
        if self._role == 'teacher':
            if bound > self._input_cap:
                failures.append('TEACHER_INPUT_BOUND_EXCEEDS_CAP')
            if other:
                failures.append('TEACHER_OTHER_HISTORY_PRESENT')
            if len(evidence) != 1:
                failures.append('TEACHER_REQUIRES_ONE_USER_MESSAGE')
            if payload.get('previous_response_id') or payload.get('conversation'):
                failures.append('TEACHER_CONVERSATION_CONTINUATION')
        if not isinstance(cap, int) or cap < 1:
            failures.append('MISSING_OUTPUT_LIMIT')
        if failures:
            self._trace.agent_event('model_boundary_rejected', role=self._role,
                code=failures[0], failed_predicates=failures, components=components,
                input_bound_with_margin=bound, input_limit=self._input_cap,
                user_message_count=len(evidence), other_message_count=len(other))
            raise EvidenceError(failures[0])
        try:
            self._budget.reserve_tokens(bound,cap,role=self._role)
        except RuntimeError as error:
            self._trace.agent_event('model_boundary_rejected', role=self._role,
                code=str(error), components=components,
                **self._budget.token_operands(bound, cap, role=self._role))
            raise
        record={'role':self._role,'tokenizer':'o200k_base','attribution':'LOCAL_ESTIMATE_PROVIDER_TOTAL_SEPARATE',
            'components':components,'estimated_input_tokens':measured,'input_bound_with_margin':bound,
            'serialized_input_bytes':len(canonical(payload.get('input',[])).encode()),
            'schema_bytes':len(canonical(fmt).encode()),'payload_digest':digest(payload),
            'max_output_tokens':cap,'reasoning_effort':(payload.get('reasoning') or {}).get('effort'),
            'provider_native_schema':fmt.get('type')=='json_schema','previous_response_id':bool(payload.get('previous_response_id')),
            'sdk_retries':self.max_retries}
        self._trace.openai_api_calls+=1  # dispatch-ready, after all context/token checks
        self._trace.boundary_diagnostics.append(record)
        if getattr(self._trace,'agent_trace_enabled',False):
            pending=[d for d in self._trace.model_diagnostics if d.get('role')==self._role]
            record.update(invocation_id=pending[-1].get('invocation_id') if pending else None,
                task_stage=self._trace.agent_stage,phase=self._trace.agent_phase)
            self._trace.agent_event('provider_dispatch_ready',**record)
        return payload
