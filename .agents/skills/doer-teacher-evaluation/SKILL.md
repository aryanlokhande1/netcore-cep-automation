---
name: doer-teacher-evaluation
description: Use when evaluating Netcore Doer or Teacher outcomes, failure ownership, candidate skill reloads, session token usage, or claims of improvement across attempts.
---

V2 source-map update: the only active task path is app.cli whatsapp -> modules/whatsapp/workflow.py run_v2. Audit V2BrowserTools in tools.py, V2Action/V2_CAPABILITIES in contract.py and independent observer.py. Old Page 1 factories/schedulers below are explicitly retired. Candidate storage uses the existing registry WhatsApp namespace. Dependency initialization details below are historical; current legacy helper validates before opening. No browser, test or provider execution is authorized by this reference.


# Doerâ€“Teacher evaluation

This Codex reference locates evaluation evidence and its consumers. It is not a
runtime instruction or a replacement for AGENTS.md and Superpowers verification.
Resolve file paths from the project root.

## Source map

| Question | Source |
| --- | --- |
| Which attempt/strategy actually ran? | `learning/browser_runtime.py`: `run_task`, candidate read/write, journal and `usage` |
| What was exposed and what failed? | `modules/whatsapp/learning_loop.py`: `BrowserTools.invoke`, `Correction`, `run_loop` adapter |
| Was this a separate login/legacy learning path? | `deep_agents/controller.py`, `learning/autonomous.py`, `learning/three_run.py` |
| How are real provider calls counted? | `deep_agents/telemetry.py`, `deep_agents/permissions.py`, `app/trace.py` |
| How are complete session roles split? | `app/whatsapp_baseline.py`: `session_usage_roles` |
| What is approved, versioned or merely proposed? | `learning/skill_storage/registry.py`, `learning/skill_storage/backend.py`, `learning/skill_storage/procedure.py` |
| How are older Teacher contracts validated? | `deep_agents/review_contract.py`, `deep_agents/review.py` |

## Evidence retrieval

Identify the entry point and runtime before interpreting a record. The baseline
parent result, shared-loop `result.json`/`journal.jsonl`, and older login trace
formats differ. Read only authorized sanitized artifacts; keep private discovery
inventories, raw page values and authentication artifacts out of reports.

Build an ordered event table: iteration, loaded hash, model/tool invocation,
arguments classification, actual result, failed predicate, owner, verified fields
and retry safety. No model request is not a reasoning failure. Discovery candidates
are not authentication proof; operator C remains distinct from independent gates.

Trace deterministic failures to source before accepting Teacher attribution. For
example, a schema admitting Campaign scroll while dispatch uses a remaining-field
inspector is a tool-contract defect even if Teacher labels it unknown. Check
`CODE_FIX_REQUIRED` and terminal guards before concluding that a candidate can run.
An analyzed proposal, generated candidate, loaded candidate, validated improvement
and approved runtime skill are five different states.

For reload evidence, compare saved candidate bytes/hash with next attempt's loaded
bytes/hash and prompt-loading path. A declared version or an existing SKILL.md
alone does not prove use. Inspect candidate validation status and approval digest;
never retrofit historical failures or promote their prose.

## Usage and comparison

Read actual provider usage, role request counts and per-attempt deltas. Separate
login Doer from task Doer when cumulative counters share the `doer` role. Keep
unavailable usage null; cached/reasoning tokens are included subsets of totals.
Configured/reserved budgets are not actual spend. Report elapsed time, browser
actions, verified fields and cleanup separately. Compare optimizations only from
equivalent starting states; an already-filled page is not a fresh trial.

## Verification references

`tests/test_browser_learning_loop.py` covers real harness/local-double learning;
`tests/test_real_teacher_reliability.py` covers saved-evidence contracts;
`tests/test_three_run.py` covers progression; `tests/test_final_readiness.py` covers
bundle provenance. Some tests launch browsers only behind explicit flags. Select
read-only collection or focused non-browser tests for tooling-only work.

Common mistakes: summing cumulative snapshots, treating model-double requests as
paid calls, equating full-frame-redacted images with visual evidence, silently
rebasing historical hash manifests, and claiming future adapters already exist.
