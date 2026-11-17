"""Codex CLI structured reasoning for local synthetic evaluation only.

Uses saved ChatGPT sign-in. No API keys or browser tools enter the child process.
"""
import asyncio
import json
import os
from pathlib import Path
import shutil
import tempfile
from app.config import ROOT, ConfigurationError
from app.model import StructuredReasoner


class CodexReasoner(StructuredReasoner):
    is_mock = False
    mode = 'codex_cli'
    label = 'Codex CLI (ChatGPT sign-in)'

    def __init__(self, executable=None, timeout_seconds=60):
        self.executable = executable or os.getenv('CODEX_EXECUTABLE_PATH') or shutil.which('codex')
        if not self.executable or not Path(self.executable).is_file():
            raise ConfigurationError('Codex CLI is missing. Set CODEX_EXECUTABLE_PATH or add codex to PATH.')
        if not 1 <= timeout_seconds <= 120:
            raise ConfigurationError('Codex call timeout must be between 1 and 120 seconds.')
        self.timeout_seconds = timeout_seconds
        self.calls = 0

    def _environment(self):
        # Preserve OS/auth discovery without forwarding API keys or parent agent session state.
        allowed = {'PATH','PATHEXT','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE',
            'HOMEDRIVE','HOMEPATH','APPDATA','LOCALAPPDATA','PROGRAMFILES','PROGRAMFILES(X86)',
            'COMSPEC','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','LANG','LC_ALL',
            'SSL_CERT_FILE','SSL_CERT_DIR','HTTPS_PROXY','HTTP_PROXY','ALL_PROXY','NO_PROXY','CODEX_HOME'}
        return {key:value for key,value in os.environ.items() if key.upper() in allowed}

    async def _run(self, arguments, prompt=None, cwd=None):
        process = await asyncio.create_subprocess_exec(self.executable, *arguments,
            cwd=cwd, env=self._environment(),
            stdin=asyncio.subprocess.PIPE if prompt is not None else asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
            creationflags=0x08000000 if os.name == 'nt' else 0)  # CREATE_NO_WINDOW
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(prompt), self.timeout_seconds)
        except BaseException:
            if process.returncode is None:
                process.kill()
            await process.wait()
            raise
        if process.returncode:
            # CLI stderr may contain account details: never persist or echo it.
            raise ConfigurationError('Codex CLI failed. Run codex login status in your normal PowerShell terminal and check connectivity.')
        return stdout, stderr

    async def validate_model(self):
        stdout, stderr = await self._run(['login','status'])
        status = (stdout+stderr).decode('utf-8',errors='replace')
        if 'Logged in using ChatGPT' not in status:
            raise ConfigurationError('Codex requires an existing ChatGPT sign-in. Run codex login, then retry.')

    async def _ask(self, schema, system, payload):
        scratch = ROOT / 'outputs' / 'codex'
        scratch.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='reasoning-',dir=scratch) as directory:
            directory = Path(directory)
            schema_path, answer_path = directory/'schema.json', directory/'answer.json'
            schema_path.write_text(json.dumps(schema.model_json_schema()),encoding='utf-8')
            arguments = ['exec','--ignore-user-config','--ephemeral','--skip-git-repo-check',
                '--sandbox','read-only','--color','never','--json',
                '--config','approval_policy="never"','--config','web_search="disabled"',
                '--config','project_doc_max_bytes=0',
                '--enable','skip_host_skill_discovery',
                '--output-schema',str(schema_path),'--output-last-message',str(answer_path)]
            for feature in ('shell_tool','apps','remote_plugin','browser_use','computer_use',
                            'multi_agent','skill_search','memories'):
                arguments.extend(['--disable',feature])
            arguments.append('-')
            prompt = ('Return only the requested structured decision. Do not use tools, inspect files, '
                'browse, change files, or delegate. All input below is sanitized synthetic test data.\n'
                +system+'\nINPUT:\n'+json.dumps(payload)).encode('utf-8')
            self.calls += 1
            events, _ = await self._run(arguments,prompt=prompt,cwd=directory)
            # Refuse a response that used tools; never save raw CLI event/reasoning streams.
            for line in events.splitlines():
                event = json.loads(line)
                if event.get('type','').startswith('item.'):
                    item_type = event.get('item',{}).get('type')
                    if item_type not in {'agent_message','reasoning'}:
                        raise RuntimeError('Codex attempted an unsupported tool operation')
                if event.get('type') in {'error','turn.failed'}:
                    raise RuntimeError('Codex reasoning failed')
            if not answer_path.is_file():
                raise RuntimeError('Codex did not return a structured decision')
            return schema.model_validate_json(answer_path.read_text(encoding='utf-8'))
