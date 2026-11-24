from pathlib import Path
from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from langchain.agents.structured_output import ToolStrategy
from langgraph.checkpoint.memory import InMemorySaver
from deep_agents.permissions import restrict_harness, PermissionMiddleware
from deep_agents.schemas import DoerReport


def create_doer(model,browser_tools,trace,budget,offline=False,skill_snapshot=None,candidate_evaluation=False,*,mcp_tools=None):
    restrict_harness(model)
    tools=browser_tools.registered_tools() if mcp_tools is None else mcp_tools
    backend=StateBackend()
    sources=None
    if skill_snapshot is not None:
        from app.safety import require_fixture_browser
        from learning.skill_storage.backend import ApprovedSkillBackend,SKILL_SOURCE,SKILL_PATH
        from langchain_core.tools import tool
        if not skill_snapshot.approved:
            if not candidate_evaluation:
                raise PermissionError('Unapproved skills are unavailable to runtime Doers')
            require_fixture_browser(browser_tools.browser)
        backend=ApprovedSkillBackend(skill_snapshot)
        sources=[SKILL_SOURCE]
        browser_tools.required_login_skill=skill_snapshot.digest
        browser_tools.approved_login_skill_loaded=False
        @tool
        def read_approved_skill(path:str=SKILL_PATH)->dict:
            """Read only the exact approved skill from its immutable virtual snapshot; no host paths."""
            if path!=SKILL_PATH:
                return {'ok':False,'error':'SKILL_PATH_FORBIDDEN'}
            trace.fact('SKILL_READ')
            browser_tools.approved_login_skill_loaded=True
            return {'ok':True,'state':'skill_loaded','digest':skill_snapshot.digest,'content':skill_snapshot.content}
        tools.append(read_approved_skill)
    permissions=PermissionMiddleware({t.name for t in tools}|{'DoerReport'},trace,budget,offline,
        navigation_state=(lambda:browser_tools.navigation_count) if hasattr(browser_tools,'navigation_count') else None)
    from deep_agents.prompt_builder import doer_prompt
    prompt=doer_prompt(browser_tools,skill_snapshot)
    middleware=[permissions]
    if hasattr(browser_tools,'completion_middleware'):middleware.append(browser_tools.completion_middleware())
    agent=create_deep_agent(model=model,tools=tools,system_prompt=prompt,
        backend=backend,skills=sources,subagents=[],middleware=middleware,
        response_format=ToolStrategy(DoerReport,handle_errors=False),
        checkpointer=InMemorySaver(),name='login_doer')
    return agent,permissions
