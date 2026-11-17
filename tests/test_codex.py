import asyncio
import json
from pathlib import Path
import pytest
from app.codex_reasoner import CodexReasoner
from app.config import ConfigurationError
from app.model import Decision
from app.state import Observation

@pytest.fixture
def reasoner(tmp_path):
    executable=tmp_path/'codex.exe'
    executable.write_bytes(b'synthetic executable placeholder')
    return CodexReasoner(executable=str(executable))

def test_missing_codex_fails_clearly(tmp_path):
    with pytest.raises(ConfigurationError,match='Codex CLI is missing'):
        CodexReasoner(executable=str(tmp_path/'missing.exe'))

def test_child_environment_excludes_api_keys_and_parent_session(reasoner,monkeypatch):
    for name in ('OPENAI_API_KEY','CODEX_API_KEY','ANTHROPIC_API_KEY','CODEX_THREAD_ID'):
        monkeypatch.setenv(name,'synthetic-sensitive')
    env=reasoner._environment()
    assert not any(name in env for name in ('OPENAI_API_KEY','CODEX_API_KEY','ANTHROPIC_API_KEY','CODEX_THREAD_ID'))
    assert 'synthetic-sensitive' not in env.values()

async def test_chatgpt_status_on_stderr_is_supported(reasoner,monkeypatch):
    async def run(*args,**kwargs):
        return b'',b'Logged in using ChatGPT\n'
    monkeypatch.setattr(reasoner,'_run',run)
    await reasoner.validate_model()

async def test_api_key_status_is_rejected(reasoner,monkeypatch):
    async def run(*args,**kwargs):
        return b'Logged in using an API key',b''
    monkeypatch.setattr(reasoner,'_run',run)
    with pytest.raises(ConfigurationError,match='ChatGPT sign-in'):
        await reasoner.validate_model()

async def test_codex_structured_decision_and_restricted_process(reasoner,monkeypatch):
    captured={}
    async def run(args,prompt=None,cwd=None):
        captured.update(args=args,prompt=prompt,cwd=cwd)
        schema=json.loads(Path(args[args.index('--output-schema')+1]).read_text())
        assert schema['additionalProperties'] is False
        Path(args[args.index('--output-last-message')+1]).write_text('{"action":"handoff"}')
        return b'{"type":"item.completed","item":{"type":"agent_message"}}\n{"type":"turn.completed"}\n',b''
    monkeypatch.setattr(reasoner,'_run',run)
    assert await reasoner.decide(Observation(credential_form=True),False)=='handoff'
    assert reasoner.calls==1
    assert captured['args'][captured['args'].index('--sandbox')+1]=='read-only'
    assert '--ignore-user-config' in captured['args']
    assert '--ephemeral' in captured['args']
    assert 'shell_tool' in captured['args']
    assert 'multi_agent' in captured['args']
    payload=json.loads(captured['prompt'].decode().split('INPUT:\n',1)[1])
    assert all(isinstance(v,bool) for v in payload['observation'].values())
    assert not captured['cwd'].exists()

async def test_codex_tool_use_is_rejected(reasoner,monkeypatch):
    async def run(args,**kwargs):
        Path(args[args.index('--output-last-message')+1]).write_text('{"action":"handoff"}')
        return b'{"type":"item.started","item":{"type":"command_execution"}}\n',b''
    monkeypatch.setattr(reasoner,'_run',run)
    with pytest.raises(RuntimeError,match='unsupported tool'):
        await reasoner._ask(Decision,'instructions',{})

async def test_codex_failure_cannot_be_replaced_by_valid_answer(reasoner,monkeypatch):
    async def run(args,**kwargs):
        Path(args[args.index('--output-last-message')+1]).write_text('{"action":"handoff"}')
        return b'{"type":"turn.failed"}\n',b''
    monkeypatch.setattr(reasoner,'_run',run)
    with pytest.raises(RuntimeError,match='reasoning failed'):
        await reasoner._ask(Decision,'instructions',{})

async def test_codex_timeout_terminates_owned_process(reasoner,monkeypatch):
    class Process:
        returncode=None
        killed=False
        async def communicate(self,prompt):
            await asyncio.sleep(1)
        def kill(self):
            self.killed=True
            self.returncode=-1
        async def wait(self):
            return self.returncode
    process=Process()
    async def create(*args,**kwargs):
        assert kwargs['stdin']==asyncio.subprocess.PIPE
        return process
    monkeypatch.setattr(asyncio,'create_subprocess_exec',create)
    reasoner.timeout_seconds=.001
    with pytest.raises(TimeoutError):
        await reasoner._run(['exec'],prompt=b'synthetic')
    assert process.killed
