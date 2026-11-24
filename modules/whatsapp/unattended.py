"""Operator-owned launch input and durable, fail-closed save claim."""
import hashlib
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from modules.whatsapp.contract import SETUP_URL, V2Stop


class DraftLaunch(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, frozen=True)

    origin: str
    tenant: str
    payload_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    action: Literal['READ_DRAFT', 'SAVE_AND_PREVIEW']
    campaign_id: str | None = Field(default=None, pattern=r'^[0-9]{1,30}$')

    @model_validator(mode='after')
    def require_action_inputs(self):
        if bool(self.campaign_id) != (self.action == 'READ_DRAFT'):
            raise ValueError('READ_DRAFT requires an exact campaign ID')
        return self

    @classmethod
    def load(cls, path):
        try:
            grant = cls.model_validate_json(Path(path).read_bytes())
        except (OSError, ValueError):
            raise V2Stop('UNATTENDED_APPROVAL_CONFIGURATION_INVALID', owner='authorization') from None
        setup = urlsplit(SETUP_URL)
        if (grant.origin != f'https://{setup.netloc}' or grant.tenant != setup.path.split('/')[1]):
            raise V2Stop('UNATTENDED_TENANT_SCOPE_MISMATCH', owner='authorization')
        return grant


def save_claim_path(outputs, launch, payload):
    key = hashlib.sha256('\0'.join((launch.origin, launch.tenant, payload.campaign_name)).encode()).hexdigest()
    return Path(outputs) / 'whatsapp-v2' / 'draft-attempts' / (key + '.json')


def claim_save(outputs, launch, payload, run_id):
    path = save_claim_path(outputs, launch, payload)
    claim_write(path, {'run_id': run_id, 'payload_sha256': launch.payload_sha256,
                       'action': launch.action, 'status': 'SAVE_ATTEMPT_CLAIMED'},
                'PRIOR_DRAFT_SAVE_ATTEMPT_REQUIRES_READ_ONLY_RECONCILIATION')


def claim_write(path, record, conflict):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(record, stream)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        raise V2Stop(conflict, owner='uncertain_write', unsafe=True) from None
