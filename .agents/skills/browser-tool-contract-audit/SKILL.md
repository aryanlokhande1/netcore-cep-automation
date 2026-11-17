---
name: browser-tool-contract-audit
description: Use when auditing Netcore browser-tool schemas, unsupported field/action combinations, target-resolution errors, or mismatches between advertised actions and deterministic executors.
---

V2 source-map update: the only active task path is app.cli whatsapp -> modules/whatsapp/workflow.py run_v2. Audit V2BrowserTools in tools.py, V2Action/V2_CAPABILITIES in contract.py and independent observer.py. Old Page 1 factories/schedulers below are explicitly retired. Candidate storage uses the existing registry WhatsApp namespace. Dependency initialization details below are historical; current legacy helper validates before opening. No browser, test or provider execution is authorized by this reference.


# Browser tool contract audit

This is a Codex source-map reference. It is not a Doer runtime skill. Follow the
root AGENTS.md and use superpowers:systematic-debugging for the debugging process.

## Source map

Resolve paths from the repository root, not this skill directory.

| Boundary | Existing implementation |
| --- | --- |
| Agent-facing fields and schemas | `modules/whatsapp/learning_loop.py`: `FieldName`, `FieldArgs`, `SelectionArgs`, `BrowserTools.registered` |
| Model tools and permissions | `modules/whatsapp/agent.py`, `deep_agents/permissions.py` |
| Shared agent construction and authorization | `learning/browser_runtime.py`: `run_task`, `agent` |
| Shared loop adapter dispatch | `modules/whatsapp/learning_loop.py`: `BrowserTools.invoke`, `verify` |
| Fixed values and older seven-tool path | `modules/whatsapp/contract.py`, `modules/whatsapp/tools.py`, `modules/whatsapp/observation.py` |
| Campaign executor and identity/readback | `modules/whatsapp/browser_baseline.py`: `_target`, `_identity`, `fill_campaign` |
| Remaining widgets and option ownership | `modules/whatsapp/interactive_widgets.py`: `inspect`, `open`, `complete`, `readback` |
| Protected document/tab binding | `browser/observed_control.py`, `browser/protected_tabs.py`, `browser/navigation_guard.py` |

Read both execution paths before calling either obsolete. The authoritative
capability registry required by AGENTS.md is a target policy: verify whether it
actually exists. Do not assume distributed field literals constitute one registry.

## Audit artifact

Produce a matrix with tool, field, accepted arguments/flags, schema acceptance,
dispatch branch, actual executor, authorization/identity prerequisites and readback.
Compare the advertised set against implemented and verified sets. Include explicit
unsupported combinations. Use actual source locations and sanitized failing events.

Example retrieval: for `open_dropdown(campaign_name)`, trace schema acceptance to
`invoke`, then check the widget inspector's supported fields. A schema accepting
the request is not proof that a native select or dropdown exists. For
`initialize_dependency`, check which field can consume it and whether rejection
occurs before any opening/selection event. The current widget implementation
checks dependency justification after `open`; do not mistake that order for an
authoritative action-boundary policy.

## Verification references

- `tests/test_browser_learning_loop.py`: real harness, Campaign scroll, candidate reload.
- `tests/test_interactive_widgets.py`: scoped options, unowned portal/create-only refusal.
- `tests/test_remaining_field_mapping.py`: field eligibility and historical integrity.
- `tests/test_browser_baseline.py`, `tests/test_baseline_login_handoff.py`: boundaries.

Select focused tests from the matrix; browser tests require separate authorization
and explicit flags. Documentation/tooling tasks do not authorize launching them.
Consult Context7 for framework API semantics; installed Stagehand wrappers must
also be checked locally. `select_option` applies to native selects, not arbitrary
ARIA comboboxes. Read-only page observation must remain independent of the matrix's
field-action prerequisites.

Common mistakes: fixing only the failing branch, treating typed search text as
selection, mistaking required policy for implemented capability, and claiming
fixture coverage establishes actual tenant compatibility.
