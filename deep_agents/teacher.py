from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.structured_output import ToolStrategy,ProviderStrategy
from langgraph.checkpoint.memory import InMemorySaver
from deep_agents.permissions import restrict_harness, PermissionMiddleware
from deep_agents.schemas import TeacherProposal
from deep_agents.review_contract import ReviewDecision


def create_teacher(model,trace,budget,offline=False,*,schema=None,prompt_override=None,observation_tools=(),
                   retain_diagnosis=None,first_observation='inspect_ga_setup',vision_observation='view_ga_setup'):
    restrict_harness(model)
    permissions=PermissionMiddleware({schema.__name__ if schema else 'TeacherProposal',
        *(tool.name for tool in observation_tools)},trace,budget,offline,role='teacher')
    from deep_agents.prompt_builder import teacher_prompt
    prompt=prompt_override or teacher_prompt()
    middleware=[permissions]
    if observation_tools:
        model.allow_teacher_observations(tool.name for tool in observation_tools)
        from langchain.agents.middleware import AgentMiddleware

        class ObserveFirst(AgentMiddleware):
            async def awrap_model_call(self,request,handler):
                returned = {getattr(message,'name',None) for message in request.messages}
                if first_observation and first_observation not in returned:
                    request=request.override(response_format=None,tool_choice=first_observation)
                elif vision_observation in returned:
                    request=request.override(tools=[])
                response = await handler(request)
                if retain_diagnosis is not None and response.structured_response is not None:
                    retain_diagnosis(response.structured_response)
                return response

        middleware.append(ObserveFirst())
    agent=create_deep_agent(model=model,tools=list(observation_tools),system_prompt=prompt,
        backend=StateBackend(),subagents=[],middleware=middleware,
        # V2 validates the same Pydantic schema at its existing controller
        # boundary, where safe field-level errors can be retained. Native strict
        # JSON transport still enforces its unchanged JSON schema.
        response_format=(ProviderStrategy(schema.model_json_schema(),strict=True) if schema else
            ToolStrategy(TeacherProposal,handle_errors=False) if offline else ProviderStrategy(ReviewDecision,strict=True)),
        checkpointer=InMemorySaver(),name='login_teacher')
    return agent,permissions
