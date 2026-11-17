import hashlib,json
from pathlib import Path
from app.config import ROOT
from learning.skill_storage.registry import SkillStore
from deep_agents.prompt_builder import preview

def test_active_skill_directory_is_content_only_and_unverified():
    files={str(p.relative_to(ROOT/'skills')) for p in (ROOT/'skills').rglob('*') if p.is_file()}
    assert {p.replace('\\','/') for p in files}=={
        'login/SKILL.md','login/version.json','whatsapp/SKILL.md','whatsapp/version.json'}
    meta=json.loads((ROOT/'skills/login/version.json').read_text())
    assert not meta['approved'] and meta['status']=='UNVERIFIED_INACTIVE'
    assert hashlib.sha256((ROOT/'skills/login/SKILL.md').read_bytes()).hexdigest()==meta['content_sha256']
    assert SkillStore().snapshot() is None

def test_history_migration_preserves_every_original_bundle_byte():
    history=ROOT/'outputs/skill_history/login';m=json.loads((history/'manifest.json').read_text())
    assert len(list((history/'candidates').iterdir()))==13
    for old,sha in m['files'].items():
        path=history/old.removeprefix('skills/netcore-login/')
        assert hashlib.sha256(path.read_bytes()).hexdigest()==sha

def test_offline_prompt_matches_shared_builders_and_never_selects_unverified_skill():
    from deep_agents.prompt_builder import doer_prompt,teacher_prompt
    from types import SimpleNamespace
    text=preview()
    from learning.policies import Strategy
    assert doer_prompt(SimpleNamespace(strategy=Strategy())) in text and teacher_prompt() in text
    assert 'NONE; unverified canonical content is NOT loaded' in text
    assert 'OFFLINE PREVIEW' in text
    for value in ('1730519403808933','agent_test','8422973254'):assert value not in text
