import hashlib
import json
from pathlib import Path

import pytest

from app.config import ROOT
from deep_agents import prompt_builder
from learning.skill_storage import registry
from learning.skill_storage.registry import SkillStore, module_baseline


def test_canonical_skill_files_match_runtime_metadata():
    for channel in ('login', 'whatsapp'):
        directory = ROOT / 'skills' / channel
        assert {path.name for path in directory.iterdir() if path.is_file()} == {'SKILL.md', 'version.json'}
        content = (directory / 'SKILL.md').read_bytes()
        metadata = json.loads((directory / 'version.json').read_text(encoding='utf-8'))
        assert hashlib.sha256(content).hexdigest() == metadata['content_sha256']
        if channel == 'whatsapp':
            baseline = module_baseline(channel)
            assert baseline.content.encode('utf-8') == content
            assert baseline.digest == metadata['content_sha256']

def test_history_migration_preserves_every_original_bundle_byte():
    history = ROOT / 'outputs/skill_history/login'
    manifest = json.loads((history / 'manifest.json').read_text(encoding='utf-8'))
    files = {Path(name.removeprefix('skills/netcore-login/')): digest
             for name, digest in manifest['files'].items()}
    candidates = {path.parts[1] for path in files if path.parts[0] == 'candidates'}
    assert len(candidates) == 13  # Migration inventory, independent of later additions.
    for path, digest in files.items():
        assert hashlib.sha256((history / path).read_bytes()).hexdigest() == digest, path


@pytest.fixture
def inactive_login_store(tmp_path, monkeypatch):
    directory = tmp_path / 'skills/login'
    directory.mkdir(parents=True)
    (directory / 'version.json').write_text(json.dumps({'approved': False}), encoding='utf-8')
    monkeypatch.setattr(registry, 'ROOT', tmp_path)
    monkeypatch.setattr(prompt_builder, 'ROOT', tmp_path)
    monkeypatch.delenv('NETCORE_SKILL_HISTORY_DIR', raising=False)
    return SkillStore()


def test_offline_prompt_matches_shared_builders_and_never_selects_unverified_skill(inactive_login_store):
    from deep_agents.prompt_builder import doer_prompt,teacher_prompt
    from types import SimpleNamespace
    assert inactive_login_store.snapshot() is None
    text=prompt_builder.preview()
    from learning.policies import Strategy
    assert doer_prompt(SimpleNamespace(strategy=Strategy())) in text and teacher_prompt() in text
    assert 'NONE; unverified canonical content is NOT loaded' in text
    assert 'OFFLINE PREVIEW' in text
    for value in ('1730519403808933','agent_test','8422973254'):assert value not in text
