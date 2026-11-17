# Candidate strategies

Baseline: baseline-v1. The reviewed security procedure is in
../modules/login/skill/SKILL.md. No Teacher proposal has been promoted.

Each recorded iteration contains the observed failure, one proposed change, baseline
trace evidence, evaluation result and acceptance decision. Acceptance applies to local
fixture testing only. Candidate files are restricted data consumed by fresh Doers.

No real Netcore password hill-climbing is available through the learning runner.

## Iteration 1

Observed failure: UNEXPECTED_PAGE; alternate login entry observed.
Proposed change: recognize_alternate_entry.
Evidence: sanitized baseline run b94f57a7edbf40f6b14e728cbe4be3ab.
Reasoner: offline test double.
Evaluation: pending. Accepted: pending. Skill promotion: prohibited without explicit approval.

Evaluation result: rejected. Accepted: False.

## Iteration 2

Observed outcome: AUTHENTICATED_AND_VERIFIED; alternate login entry observed.
Proposed change: recognize_alternate_entry.
Evidence: sanitized baseline run 189e69a10ca74226b33c9b4e0acef55d.
Reasoner: offline test double.
Evaluation: pending. Accepted: pending. Skill promotion: prohibited without explicit approval.

Evaluation result: rejected. Accepted: False.

## Iteration 1

Observed outcome: UNEXPECTED_PAGE; alternate login entry observed.
Proposed change: recognize_alternate_entry.
Evidence: sanitized baseline run a832e0b05c5c44f4bde0db26e979f752.
Reasoner: offline test double.
Evaluation: pending. Accepted: pending. Skill promotion: prohibited without explicit approval.

Evaluation result: accepted for local testing. Accepted: True.
