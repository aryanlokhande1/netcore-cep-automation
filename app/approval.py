"""Approval interface for future reviewed operations; no campaign executor exists."""
import hashlib
import json
from dataclasses import dataclass
from langgraph.types import interrupt

def configuration_digest(configuration: dict) -> str:
    return hashlib.sha256(json.dumps(configuration, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()

@dataclass(frozen=True)
class ReviewedApproval:
    digest: str
    approved: bool

    def permits(self, exact_configuration: dict) -> bool:
        return self.approved and self.digest == configuration_digest(exact_configuration)

def request_review(exact_configuration: dict) -> ReviewedApproval:
    digest = configuration_digest(exact_configuration)
    response = interrupt({'kind':'exact_configuration_review',
        'configuration':exact_configuration, 'digest':digest})
    approved = isinstance(response,dict) and response.get('approved') is True and response.get('digest') == digest
    return ReviewedApproval(digest, approved)
