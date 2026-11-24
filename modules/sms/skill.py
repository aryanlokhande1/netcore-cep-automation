"""Load an immutable baseline or an explicitly pinned SMS candidate."""

import hashlib
import json
import re

from app.config import ROOT
from learning.skill_storage.backend import SkillSnapshot
from modules.whatsapp.contract import V2Stop


TASK_PROMPT = 'Create the SMS campaign from the approved payload and stop at Preview.'
SKILL_PATH = ROOT / 'skills' / 'sms' / 'SKILL.md'
CANDIDATES = ROOT / 'outputs' / 'skill_history' / 'sms' / 'candidates'


def load_skill(*, version=None, expected_digest=None, task_digest=None, candidates=CANDIDATES):
    try:
        if version is None:
            raw = SKILL_PATH.read_bytes()
            metadata = {'version': 'baseline', 'source': 'BASELINE'}
        else:
            if not expected_digest or not re.fullmatch(r'[0-9a-f]{64}', version):
                raise ValueError
            root = candidates.resolve()
            folder = (root / version).resolve()
            if folder.parent != root:
                raise ValueError
            metadata = json.loads((folder / 'version.json').read_bytes())
            raw = (folder / 'SKILL.md').read_bytes()
            if (metadata.get('version') != version or metadata.get('task_sha256') != task_digest
                    or metadata.get('approved') is not False or not metadata.get('lessons')
                    or metadata.get('validation') != 'VERIFIED_ACTION_READBACK'):
                raise ValueError
            metadata = {**metadata, 'source': 'TEACHER_CANDIDATE'}
        digest = hashlib.sha256(raw).hexdigest()
        if ((expected_digest and digest != expected_digest.lower())
                or (version and metadata.get('content_sha256') != digest)):
            raise ValueError
        return SkillSnapshot(raw.decode('utf-8'), digest, metadata, False)
    except (OSError, ValueError, TypeError, AttributeError):
        raise V2Stop('SMS_SKILL_HASH_OR_PROVENANCE_UNVERIFIED', owner='authorization') from None
