"""A deterministic chat-model double running through the real Deep Agents harness.

It emits tool calls; it cannot access the browser itself and is never labeled inference.
"""
import json
from typing import Literal
from uuid import uuid4
from pydantic import Field
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

class SyntheticChatModel(BaseChatModel):
    role: Literal['doer','teacher']='doer'
    model_name: str='scripted-synthetic'
    bound_tool_names: list[str]=Field(default_factory=list)
    calls: int=0
    loaded_skill_digest: str | None=None
    loaded_skill_content: str | None=None

    @property
    def _llm_type(self):
        return 'pass1-offline'

    def _get_ls_params(self,**kwargs):
        return {'ls_provider':'pass1_offline','ls_model_name':self.model_name,'ls_model_type':'chat'}

    def bind_tools(self,tools,**kwargs):
        self.bound_tool_names=[t.name if hasattr(t,'name') else t.get('name',t.get('function',{}).get('name')) for t in tools]
        return self

    def _generate(self,messages,stop=None,run_manager=None,**kwargs):
        self.calls += 1
        if self.role=='teacher':
            data=json.loads(next(m.content for m in reversed(messages) if isinstance(m,HumanMessage)))
            outcome=data['outcome']
            failed=outcome!='success'
            cards=data.get('evidence',[])
            legacy_events=data.get('events',[])
            changed_labels=any(c.get('data',{}).get('flags',{}).get('role_labels_changed') for c in cards) if cards else any((e.get('observation') or {}).get('role_labels_changed') for e in legacy_events)
            references=sorted({i for c in cards for i in c.get('events',[])}) if cards else list(range(len(legacy_events)))
            name,args='TeacherProposal',dict(outcome=outcome,change_required=failed,
                finding='Verified dashboard access.' if not failed else 'Execution stopped without verified access.',
                proposed_change=('Prefer accessible roles when field labels change.' if changed_labels else 'Inspect current elements before choosing an action.') if failed else '',
                strategy_rule=('prefer_semantic_roles' if changed_labels else 'reinspect_before_submit') if failed else None,
                evidence_events=[references[-1]] if references else [])
            gap=data.get('reason_code')=='AUTHENTICATED_STATE_NOT_INDEPENDENTLY_VERIFIED'
            if gap:
                args.update(change_required=False,proposed_change='',strategy_rule=None,analysis_kind='evidence_gap',
                    finding='Submission completed; independent destination evidence is unresolved.')
            elif not failed:
                args['analysis_kind']='no_change'
            args['worked']='Supported steps are retained in the cited canonical facts.'
            args['inefficiency']='Compare request, inspection and recovery counts; timing alone does not justify a change.'
            if data.get('comparison_history'):
                args['comparison']='Prior completed runs and evaluated strategy decisions were compared; retain correctness before cost.'
        else:
            tool_messages=[m for m in messages if isinstance(m,ToolMessage)]
            if 'read_approved_skill' in self.bound_tool_names and not any(m.name=='read_approved_skill' for m in tool_messages):
                name,args='read_approved_skill',{}
            elif not tool_messages or tool_messages[-1].name=='read_approved_skill':
                if tool_messages:
                    loaded=json.loads(tool_messages[-1].content)
                    self.loaded_skill_digest=loaded.get('digest')
                    self.loaded_skill_content=loaded.get('content')
                content=next(m.content for m in messages if isinstance(m,HumanMessage))
                context=json.loads(content.split('APPROVED_SYNTHETIC_CONTEXT\n',1)[1])
                name,args='navigate_approved',{'url':context['entry_url']}
            else:
                last=tool_messages[-1]
                data=json.loads(last.content)
                if not data.get('ok'):
                    name,args='DoerReport',{'status':'blocked','reason':'tool_error'}
                elif data.get('must_inspect') or data.get('state')=='field_filled':
                    name,args='inspect_page',{}
                elif last.name=='inspect_page':
                    elements=data['elements']
                    target=next((e for e in elements if e['kind'] in {'entry','submit','challenge'}),None)
                    if target:
                        name,args='interact_observed',{'reference':target['reference'],
                            'operation':'submit_fixture' if target['kind']=='submit' else 'activate'}
                    else:
                        name,args='verify_dashboard',{}
                elif last.name=='verify_dashboard':
                    status=data['verification']
                    mapping={'AUTHENTICATED_AND_VERIFIED':('success','verified'),
                        'AUTH_FAILED':('failure','authentication_rejected'),
                        'SESSION_EXPIRED':('failure','session_expired'),
                        'UNEXPECTED_PAGE':('failure','wrong_context')}
                    kind,reason=mapping.get(status,('blocked','unrecognized_page'))
                    name,args='DoerReport',{'status':kind,'reason':reason}
                else:
                    name,args='DoerReport',{'status':'blocked','reason':'tool_error'}
        response=AIMessage(content='',tool_calls=[{'name':name,'args':args,'id':uuid4().hex,'type':'tool_call'}])
        return ChatResult(generations=[ChatGeneration(message=response)])

    async def _agenerate(self,messages,stop=None,run_manager=None,**kwargs):
        return self._generate(messages,stop=stop,run_manager=run_manager,**kwargs)
