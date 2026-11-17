from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.structured_output import ToolStrategy,ProviderStrategy
from langgraph.checkpoint.memory import InMemorySaver
from deep_agents.permissions import restrict_harness, PermissionMiddleware
from deep_agents.schemas import TeacherProposal
from deep_agents.review_contract import ReviewDecision


def create_teacher(model,trace,budget,offline=False,*,schema=None,prompt_override=None):
    restrict_harness(model)
    permissions=PermissionMiddleware({schema.__name__ if schema else 'TeacherProposal'},trace,budget,offline,role='teacher')
    from deep_agents.prompt_builder import teacher_prompt
    prompt=prompt_override or teacher_prompt()
    agent=create_deep_agent(model=model,tools=[],system_prompt=prompt,
        backend=StateBackend(),subagents=[],middleware=[permissions],
        # V2 validates the same Pydantic schema at its existing controller
        # boundary, where safe field-level errors can be retained. Native strict
        # JSON transport still enforces its unchanged JSON schema.
        response_format=(ProviderStrategy(schema.model_json_schema(),strict=True) if schema else
            ToolStrategy(TeacherProposal,handle_errors=False) if offline else ProviderStrategy(ReviewDecision,strict=True)),
        checkpointer=InMemorySaver(),name='login_teacher')
    return agent,permissions
