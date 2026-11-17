"""Explicit model construction. No Codex fallback and no default provider model."""
import os
from langchain_openai import ChatOpenAI
from app.config import ConfigurationError

RUNTIME_MODEL='gpt-6-luna'
MAX_OUTPUT_TOKENS=500


def openai_model(role: str,settings=None,trace=None,budget=None,max_output_tokens=None,*,login=False):
    from app.model_settings import ModelSettings
    from deep_agents.telemetry import MeteredChatOpenAI
    if role not in {'doer','teacher','observer'}:raise ConfigurationError('Unknown agent role')
    settings=settings or ModelSettings.load()
    if not settings.api_key:raise ConfigurationError('OPENAI_API_KEY is missing; actual inference blocked')
    if login and role!='doer':raise ConfigurationError('Login model must use the Doer role')
    output=max_output_tokens or (settings.login_output if login else settings.teacher_output if role=='teacher' else settings.doer_output)
    if login and not 1024<=output<=4096:raise ConfigurationError('Invalid bounded Login output allowance')
    if role=='teacher' and not 1<=output<=2000:raise ConfigurationError('Teacher output allowance exceeds approved maximum')
    model=MeteredChatOpenAI(model=RUNTIME_MODEL,api_key=settings.api_key,base_url='https://api.openai.com/v1',
        use_responses_api=True,store=False,max_tokens=output,max_retries=0,timeout=60 if role=='teacher' else 30,
        reasoning={'effort':'low' if login else settings.teacher_reasoning} if login or role=='teacher' else None,
        model_kwargs={'parallel_tool_calls':False},stream_usage=False,verbose=False)
    model._input_cap=settings.teacher_input
    if trace is not None:model.bind_meter(trace,budget,role)
    return model
