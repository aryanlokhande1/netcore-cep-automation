# Human-Assisted Login and WhatsApp Diagnostic Implementation Plan

> **For agentic workers:** Execute task-by-task in this session using the approved native approach. Each task uses checkbox tracking and TDD. Do not commit unless the user separately requests it.

**Goal:** Add a one-session, headed, human-assisted Login calibration and a strictly read-only WhatsApp Doer diagnostic while preserving the existing Login verifier and all production write restrictions.

**Architecture:** Reuse the owned Stagehand/Chromium, CredentialBroker, passive discovery/evidence review, protected navigation guard, existing V2 browser tools, LangGraph and Deep Agents. Add a dedicated payload-free read-only diagnostic graph branch: independent Login proof remains authoritative, and an unverified route is separately consented, isolated and never reports production authentication success.

**Tech Stack:** Existing Python, Stagehand/Chromium, LangGraph, Deep Agents, Pydantic and pytest. No dependency changes.

**Spec:** User-approved inline design in this session (2026-11-18).

## Global Constraints

- One owned headed Chromium session; no live retry; at most one Login credential submission.
- Preserve the existing five independent Login predicates and all negative/security stops.
- Operator confirmation is not authentication proof; no production route or predicate is bypassed.
- Diagnostic browser actions are read-only and use only current observed references and permitted same-origin WhatsApp navigation links.
- No arbitrary URL, selector, script, free-form terminal command, campaign value change, campaign creation, or forbidden transition.
- Do not load the campaign payload or expired schedule for this read-only diagnostic.
- The one-run unapproved WhatsApp baseline evaluation was explicitly approved; do not promote or overwrite it.
- Finite caps: Login at most 6, task Doer at most 4, Observer at most 2, total at most 12 dispatch-ready requests, respecting lower configured caps. Teacher inference is disabled.
- No screenshots unless separately approved and validated masking is available.
- Do not persist credentials, cookies, JWTs, storage artifacts, raw response bodies, raw account identifiers or raw customer information.
- Preserve all pre-existing worktree edits. No stash, reset, checkout, staging or commit.
- Save `source-changes.diff` relative to the captured pre-change worktree state, excluding pre-existing edits.

## Review Focus

- A stale, hidden, ambiguous or unowned DOM/AX reference must be rejected before interaction; test current-reference binding and stale-reference rejection in Task 1.
- Human consent on an unverified page must not set authentication verified or enter the production success route; test consent and refusal in Task 3.
- Unsupported/read-only action combinations must be rejected before browser interaction; test the complete allowed/rejected diagnostic action boundary in Task 2.
- Any tenant, origin, protected tab, document, authentication challenge, guard or transport change must stop immediately; test a current-document/origin change in Tasks 2 and 3.
- Logs and artifacts must remain redacted even on malformed inputs and failures; test candidate/value redaction in Tasks 1 and 4.

---

### Task 1: Operator Console and Evidence Selection

**Files:**
- Create: `browser/operator_console.py`
- Modify: `browser/evidence_review.py`, `browser/discovery.py`, `browser/evidence_diagnostics.py`
- Test: `tests/test_human_assisted_operator.py`

**Interfaces:**
- Consumes: Existing `ReadOnlyDiscovery`, `EvidenceReview`, protected document identity and `review_event`.
- Produces: `OperatorConsole.choose(prompt_id, choices, timeout_seconds, maximum_seconds) -> str`; a numbered, enum-only choice result; and a sanitized timestamped decision event.

- [ ] **Step 1: Write failing tests** for unlisted input rejection, active-input timeout extension with a hard cap, stale candidate rejection, current visible AX/DOM candidate binding, and absence of raw candidate text/value in decision logs.
- [ ] **Step 2: Run the tests and confirm they fail** because the numbered console and current AX/DOM candidate-selection boundary do not exist.
- [ ] **Step 3: Implement the bounded console and observed-candidate mapping** by extending existing evidence review/discovery; never expose selectors or arbitrary browser commands to terminal input.
- [ ] **Step 4: Run `.\.venv\Scripts\python.exe -m pytest tests\test_human_assisted_operator.py -q`** and confirm the new cases pass.

### Task 2: Payload-Free Read-Only WhatsApp Tools

**Files:**
- Modify: `modules/whatsapp/contract.py`, `modules/whatsapp/tools.py`, `modules/whatsapp/observation.py`, `modules/whatsapp/agent.py`
- Test: `tests/test_human_assisted_tools.py`

**Interfaces:**
- Consumes: Task 1's current observed references and protected session context.
- Produces: A diagnostic-only `V2BrowserTools` mode that accepts no campaign payload, returns redacted visible control state, and exposes only observation/inspection/readback/scroll/fresh observed-link navigation.

- [ ] **Step 1: Write failing tests** for payload-free observation, redacted current-state readback, observed same-origin navigation, and rejection of click/type/search/select/advance/persist actions before any browser call.
- [ ] **Step 2: Run the tests and confirm the expected failures** arise from the current payload requirement and task action schema.
- [ ] **Step 3: Add the explicit diagnostic mode** without changing the existing production V2 behavior; reject incompatible authorization/arguments at the action boundary.
- [ ] **Step 4: Run `.\.venv\Scripts\python.exe -m pytest tests\test_human_assisted_tools.py -q`** and confirm all allowed/rejected combinations pass.

### Task 3: CLI and LangGraph Lifecycle

**Files:**
- Modify: `app/cli.py`, `modules/whatsapp/workflow.py`, `modules/whatsapp/agent.py`
- Test: `tests/test_human_assisted_graph.py`

**Interfaces:**
- Consumes: Tasks 1 and 2.
- Produces: `run_human_assisted(args)` and an explicitly authorized one-run CLI command with independent Login status, separate diagnostic consent, and fixed finite budgets.

- [ ] **Step 1: Write failing tests** for missing live/inference/candidate-evaluation permission; single credential submission; all five Login predicates; default-NO refusal; consented unverified diagnostics staying outside the production success route; verified Login routing; and no campaign-payload/schedule dependency.
- [ ] **Step 2: Run the tests and confirm the intended missing-route failures.**
- [ ] **Step 3: Implement the one-session graph branch** using the existing broker/verifier and actual read-only WhatsApp Doer; preserve `authentication_verified=False` on the unverified branch; stop on unsafe page/session changes; do not invoke Teacher.
- [ ] **Step 4: Run `.\.venv\Scripts\python.exe -m pytest tests\test_human_assisted_graph.py -q`** and verify no route invokes campaign writes or a second credential submission.

### Task 4: Evidence Artifacts and Documentation

**Files:**
- Create: `modules/whatsapp/diagnostic_artifacts.py`
- Modify: `docs/whatsapp.md`, `docs/operations.md`, `docs/known_limitations.md`
- Test: `tests/test_human_assisted_artifacts.py`

**Interfaces:**
- Consumes: Sanitized Login, operator, tool, graph, skill and usage events from Tasks 1–3.
- Produces: The exact requested `outputs/human-assisted/login-whatsapp-<run-id>/` artifact set and final status report.

- [ ] **Step 1: Write failing tests** for exact artifact names, explicit NOT_CAPTURED/NOT_INVOKED fields, redaction of private values, null/unavailable provider-usage semantics, and exact final outcome classification.
- [ ] **Step 2: Run the tests and confirm they fail** because the artifact writer is absent.
- [ ] **Step 3: Implement atomic, sanitized artifact writing**; `teacher-review.json` is `NOT_INVOKED_DIAGNOSTIC_ONLY`, screenshots default to `NOT_CAPTURED`, and only task-authored source changes enter `source-changes.diff`.
- [ ] **Step 4: Run `.\.venv\Scripts\python.exe -m pytest tests\test_human_assisted_artifacts.py -q`** and review each generated artifact for privacy and honest missing-evidence reporting.

### Task 5: Validate, Run Once, and Repair Only Observed Defects

**Files:**
- Modify only source files implicated by the one live run.
- Test: `tests/test_human_assisted_operator.py`, `tests/test_human_assisted_tools.py`, `tests/test_human_assisted_graph.py`, `tests/test_human_assisted_artifacts.py`, plus any focused regression added from live evidence.

**Interfaces:**
- Consumes: The complete diagnostic command, tests and artifact writer from Tasks 1–4.
- Produces: One sanitized, persistent live-session evidence directory; evidence-backed corrections if the run identifies a deterministic defect; and a verified final outcome.

- [ ] **Step 1: Run focused offline tests and the local Chromium safety check** before the live session. Capture the pre-change worktree state without altering it.
- [ ] **Step 2: Enable and use the actual interactive VS Code terminal.** Run exactly one fresh headed Netcore session; wait for and consume operator choices. Do not auto-approve, retry Login, or continue if terminal/session isolation is unavailable.
- [ ] **Step 3: Analyze actual events and compare operator-selected references to discovery evidence.** Change code only for confirmed selector/visibility/ownership/dispatch defects; do not infer a fix from a historical run alone.
- [ ] **Step 4: Add a failing focused regression before each live-evidence correction, then run it RED→GREEN.**
- [ ] **Step 5: Run `.\.venv\Scripts\python.exe -m pytest tests\test_human_assisted_operator.py tests\test_human_assisted_tools.py tests\test_human_assisted_graph.py tests\test_human_assisted_artifacts.py -q`** and the actual local Chromium safety check after corrections. Do not restart Netcore.
- [ ] **Step 6: Verify the exact artifact manifest, source-only diff, final status, usage availability and owned-browser cleanup.** Report unknown or unconfirmed states explicitly.
