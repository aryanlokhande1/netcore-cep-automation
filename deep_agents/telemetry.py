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
    _teacher_observation_tools=PrivateAttr(default=frozenset())
    _authored_prompt=PrivateAttr(default='')
    _instruction_capture=PrivateAttr(default=None)

    def set_instruction_capture(self, capture):
        self._instruction_capture = capture
    def bind_meter(self,trace,budget,role):
        self._trace=trace;self._budget=budget;self._role=role
        self.callbacks=[UsageBoundary(self)]
        trace.model_limits[role]=self.max_tokens
        return self
    def allow_teacher_observations(self, names):
        names = frozenset(names)
        allowed = ({'inspect_ga_setup', 'view_ga_setup'}, {'view_sms_stage'},
                   {'read_sms_review_evidence', 'view_sms_stage'})
        if self._role != 'teacher' or names not in allowed:
            raise ValueError('Unexpected Teacher observation capability')
        self._teacher_observation_tools=names

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
        observation_calls = [item for item in other if item.get('type') == 'function_call']
        observation_outputs = [item for item in other if item.get('type') == 'function_call_output']
        observation_history = bool(self._teacher_observation_tools) and (
            len(observation_calls) == len(observation_outputs) <= 2
            and len({call.get('name') for call in observation_calls}) == len(observation_calls)
            and all(call.get('name') in self._teacher_observation_tools for call in observation_calls)
            and {call.get('call_id') for call in observation_calls} == {out.get('call_id') for out in observation_outputs}
            and all(item.get('type') in {'function_call','function_call_output','reasoning'} for item in other))
        history = other
        image_tokens = 0
        if self._role == 'teacher' and observation_history:
            history = []
            for item in other:
                output = item.get('output')
                if isinstance(output, list):
                    output = [dict(block) for block in output]
                    for block in output:
                        if block.get('type') == 'input_image' and block.get('detail') == 'low':
                            image_tokens += 1024  # Bounded, low-detail crop. Base64 is transport, not text tokens.
                            block['image_url']='[bounded low-detail PNG]'
                    item = {**item,'output':output}
                history.append(item)
        components={'system_and_middleware':tokens(system)+tokens(instructions),
            'evidence_messages':tokens(evidence),'schema_and_tools':tokens(fmt)+tokens(payload.get('tools',[])),
            'other_message_history':tokens(history)+image_tokens}
        measured=sum(components.values());bound=measured+512
        cap=payload.get('max_output_tokens',self.max_tokens)
        failures = []
        if self._role == 'teacher':
            if bound > self._input_cap:
                failures.append('TEACHER_INPUT_BOUND_EXCEEDS_CAP')
            if other and not observation_history:
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
        if self._teacher_observation_tools:
            record['teacher_image_inputs'] = image_tokens // 1024
        self._trace.openai_api_calls+=1  # dispatch-ready, after all context/token checks
        self._trace.boundary_diagnostics.append(record)
        if getattr(self._trace,'agent_trace_enabled',False):
            pending=[d for d in self._trace.model_diagnostics if d.get('role')==self._role]
            record.update(invocation_id=pending[-1].get('invocation_id') if pending else None,
                task_stage=self._trace.agent_stage,phase=self._trace.agent_phase)
            self._trace.agent_event('provider_dispatch_ready',**record)
        if self._instruction_capture is not None:
            self._instruction_capture(instructions, system, evidence, record['payload_digest'])
        return payload
