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
        if self._role=='teacher' and (bound>self._input_cap or other or len(evidence)!=1 or payload.get('previous_response_id') or payload.get('conversation')):
            raise EvidenceError('MODEL_BOUNDARY_CONTEXT_BUDGET_OR_HISTORY')
        cap=payload.get('max_output_tokens',self.max_tokens)
        if not isinstance(cap,int) or cap<1:raise EvidenceError('MISSING_OUTPUT_LIMIT')
        self._budget.reserve_tokens(bound,cap,role=self._role)
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
