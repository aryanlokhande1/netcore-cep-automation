"""Application-controlled prompts; offline inspection never loads secrets/providers."""
import hashlib,json
from pathlib import Path
from app.config import ROOT

def doer_prompt(browser_tools,skill_snapshot=None):
    text=(Path(__file__).parent/'prompts/doer.md').read_text(encoding='utf-8-sig')
    text+=getattr(browser_tools,'handoff_instructions','')
    if skill_snapshot is not None:
        text+='\nAn approved skill is listed in the native skills catalog. Read it with read_approved_skill using its exact catalog path before acting. Generic read_file is unavailable. Follow its bounded guidance. Never read other files.\n'
    if hasattr(browser_tools,'strategy'):text+='\nApplication-validated strategy:\n'+browser_tools.strategy.instructions()
    return text

def teacher_prompt():
    return (Path(__file__).parent/'prompts/teacher.md').read_text(encoding='utf-8-sig')

def preview():
    from types import SimpleNamespace
    from learning.skill_storage.registry import SkillStore
    snapshot=SkillStore().snapshot()
    from deep_agents.schemas import DEFAULT_TASK
    from learning.policies import Strategy
    metadata=json.loads((ROOT/'skills/login/version.json').read_text())
    return '\n'.join(['OFFLINE PREVIEW: application-controlled login context only.',
        'No credentials, provider/framework hidden instructions, live DOM or runtime user data captured.',
        'Canonical skill: skills/login/SKILL.md',json.dumps(metadata,indent=2),
        'Selected approved skill: '+(snapshot.digest if snapshot else 'NONE; unverified canonical content is NOT loaded'),
        'DOER INSTRUCTIONS',doer_prompt(SimpleNamespace(strategy=Strategy.model_validate(snapshot.metadata['strategy']) if snapshot else Strategy()),snapshot),'TEACHER INSTRUCTIONS',teacher_prompt(),
        'RUNTIME TASK INPUT: '+DEFAULT_TASK,
        'Runtime-specific handoff/strategy additions omitted from this generic preview.'])

if __name__=='__main__':
    (ROOT/'prompt.txt').write_text(preview(),encoding='utf-8')
