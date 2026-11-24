# Setup and operation

Status: IMPLEMENTED — NOT LIVE VERIFIED. No command below was executed during the
implementation assignment. Separate live/provider authorization is required.

## Environment

Use the existing Windows source checkout, Python 3.11+ and installed headed
Chromium. pyproject.toml and requirements.lock.txt retain the existing stack.
tzdata==2026.6 is pinned for Windows ZoneInfo support; the active environment resolves
Asia/Kolkata. Login still uses its private broker and existing independent evidence
verifier. A writable checkout remains required.

Do not run synthetic, mocked, fixture or simulated browser tests. Static review
means AST parsing, import/path/CLI inspection and hash comparison. Focused environment
checks may validate ZoneInfo and the actual approved payload; do not execute workflows.

## Before an authorized V2 run

1. Preserve the approved private schedule: 17 November 2026, 16:00 Asia/Kolkata.
2. Approve the exact payload SHA-256; if the schedule expires, stop for a new value.
3. UI evidence is incremental and optional at startup. When supplying a document,
   approve its exact SHA-256. No future selectors or record endpoints are invented.
4. Supply existing Login evidence, or use --review-login-evidence in an interactive
   terminal. The existing passive discovery and D/P/A digest review operate in the
   same owned Login session. Missing proof never counts as authenticated.
5. Only after independent authentication can the task inspect actual current UI.
   Without reviewed campaign identity/record proof it stays read-only on the observed
   authenticated view; it does not enter creation or edit campaign values.
6. Before creation/edits, establish exact no-existing-campaign proof. Before each
   save require current-stage field/control identity and independent saved-field
   mappings; final draft verification requires every requested saved value.
7. Review browser/provider/request/token limits and the unapproved skill.
8. Authorize the intended execution separately from this engineering assignment.

Template only, after those prerequisites:

    python -m app.cli whatsapp --payload credentials/whatsapp-v2-payload.json --approve-payload-sha256 <REVIEWED_PAYLOAD_SHA256> --review-login-evidence --login-review-seconds 120 --authorized-live --authorized-inference --authorized-edits --authorized-persistence --authorized-candidate-evaluation --iterations 1

Optional --login-evidence-file reuses an existing Login evidence document. Otherwise
review mode reserves a private per-run output destination; no placeholder is created.
Add --ui-evidence <ACTUAL_REVIEWED_PARTIAL_UI_FILE> --approve-ui-sha256 <EXACT_DIGEST>
when real evidence is available. No UI file means discovery only after verified Login.
For a read-only discovery review phase, omit --authorized-edits and
--authorized-persistence; startup requires only live/inference authorization, while
creation and persistence remain prohibited independently of supplied UI evidence.

Add --authorized-vision only for reviewed image transmission. Learning additionally
requires --authorized-learning. Fresh trials additionally require
--authorized-fresh-iterations --iterations 3 and sufficient configured total budget.
--candidate selects one exact existing candidate version under evaluation.
The initial private UI document is intentionally absent; selectors and endpoints
must come from actual reviewed evidence. These placeholders are not runnable values.

Default total API cap is 24 across the task invocation, also bounded by existing
model settings and shared token limits. Login is capped separately within that
budget. Reserve Teacher capacity; exhausted capacity does not authorize a retry.
Three full executions may need separately reviewed budgets; count limits are not
a promise that three task attempts will run.

## One-run human-assisted diagnostic

P0 repair update (19 November): use the bounded manual-form/Teacher command and
validation limits in [WhatsApp execution repair](whatsapp-p0-execution-repair.md).
From the repository root, launch the approved Setup test with `.\run-whatsapp.ps1`.
It uses an existing reviewed Login evidence document from private configuration or
`credentials/login-evidence-reviewed.json`. After independently verifying Login
and the intended tenant, the launcher asks once whether to write the approved
Setup fields. The terminal reports the missing proof before Chromium starts if
the evidence document cannot be loaded.
To obtain reviewed evidence, use the existing standalone Login review from the
same interactive terminal. Choose a private path and follow its D/P/A
discovery, preview, and digest approval steps:

    python -m app.cli live --login-only --authorized-live --api-request-budget 6 --hold-seconds 300 --review-evidence --evidence-review-file <PRIVATE_FILE>

The independently verified Login result and the protected Setup tenant route
are required for manual entry. Read-only diagnostics remain available through
a separate invocation. `--authorized-learning` reserves one Teacher review and
permits an unapproved candidate after an eligible browser-strategy failure.
Success and technical stops can be reviewed without creating a candidate.
Every evaluation requires a separate operator launch. Select the baseline with
`--baseline-sha256`, or provide both `--candidate` and `--candidate-sha256`.
Each run records the selected bytes and digest and writes any candidate diff
into its local run directory. The full V2 command now accepts
one iteration per operator launch. The older multi-iteration example above is
historical and is not an instruction to retry automatically.

The payload-free Login/WhatsApp diagnostic is separate from the V2 campaign workflow.
Run it only from an interactive VS Code terminal with the approved live, inference and
candidate-evaluation permissions:

    python -m app.cli whatsapp-human-assisted --authorized-live --authorized-inference --authorized-candidate-evaluation --operator-review-seconds 300 --session-seconds 900

It forces headed Chromium, uses the existing Login credential broker and verifier,
and does not accept or modify a campaign payload. A consented unverified-session
route is diagnostic-only; it does not mark authentication verified or enter the
production authenticated route. By default, the task Doer is restricted to
read-only observation, readback, bounded scrolling and fresh observed-link
navigation. Screenshots are not captured by default.

For the separately authorized manual form test, add `--manual-form-test`. This
loads the pinned private payload and permits field entry only into freshly
observed Setup controls, with independent readback. It does not authorize
Next Step, Save, Schedule, Publish, Activate or Send, and it does not certify
authentication. The current implementation does not prove that the application
will not automatically persist a field change; do not treat readback as proof
that no server-side campaign state was created.

The per-run record is under
`outputs/human-assisted/login-whatsapp-<run-id>/`. Review `report.md` and the evidence
manifest; missing observations, stage reachability, source-diff baseline and provider
usage must remain `NOT_CAPTURED`/unavailable rather than inferred. The supplied
18 November 2026, 18:00 Asia/Kolkata schedule is not read by the payload-free
diagnostic and is not entered by the manual Setup test.

## Stops and recovery

Missing time/authorization blocks before browser startup. Authentication evidence is
established through the existing verifier/review before task discovery; missing
current-field/record evidence blocks its state-changing action. Existing campaigns
block automatic creation/resume. Uncertain writes block replay through a persistent
journal. A controller lock prevents concurrent canonical task runs; a stale lock
requires operator review, not automatic removal. Only owned cleanup is performed.
Review result predicates and actual usage; never infer success from a toast or URL.

New evidence is under outputs/whatsapp-v2/<session ID>/, with a result, structural
trace and selected skill bytes. Cleanup must report CLOSED_AND_PROFILE_REMOVED.
Unexpected process/profile uncertainty remains visible and stops further trials.

## Login and retired commands

Existing app.cli live and --login-only retain Login semantics and authorization.
app.cli whatsapp-contract reports the public V2 contract without task values.
whatsapp-setup and --doer-only are no longer normal CLI choices.
app.whatsapp_baseline and old WhatsApp schedulers fail explicitly.

## Source recovery

The source snapshot and original-output hash manifest are under
outputs/v2-implementation-20261116T111107Z/. Extract source-before.zip into a separate
staging directory, verify member hashes against manifest.json, and restore only
reviewed paths. Private payloads/credentials/profiles are excluded. Do not overwrite
later work or historical outputs. No Git repository was initialized and no commit
was made.
## Unattended WhatsApp draft runbook

### Login and session handoff

Run `app.cli whatsapp-human-assisted` with `--unattended-config`,
`--unattended-payload`, `--login-evidence-file`, `--authorized-live`, and
`--authorized-inference`. Use the approved Login bundle. The existing Login
Doer calls `read_approved_skill`, `navigate_approved`, `inspect_page`, and
`submit_login` through the broker. Allow one credential submission. The
controller checks the approved HTTPS origin and tenant boundary, then runs
`stable_authentication` on the current protected Login document. It requires
the reviewed dashboard marker, exact tenant, and authenticated read-only
access, then binds the current owned tab and browser session. A missing or
unreviewed contract stops before Chromium. A failed live predicate, challenge,
redirect to sign-in, or changed tab/session stops before campaign edits.

### WhatsApp draft and verification

For `READ_DRAFT`, use the exact ID and payload name. `reconcile_campaign`
reads a current authorized record view or first checks the visible Drafts or
Campaigns table. It prefers the visible matching ID and name, otherwise it
waits for a usable Search control and exact result. It opens only an observed
read-only Preview link. `DETAIL` reads visible identity, explicit Draft
status, and the Send later date, time, and timezone. A same-record Draft badge
counts. A toast or a route alone is insufficient. `Asia/Calcutta` and
`Asia/Kolkata` are equivalent for matching.

For `SAVE_AND_PREVIEW`, the controller first requires a completed exact-name
search with an explicit zero-result state. A blank, busy, inaccessible, or
ambiguous list blocks Setup. The launch grant replaces the terminal write
approval. `run_page1_fields` and `run_campaign_fields` reuse deterministic
readbacks for Setup, Audience, and Content. `ScheduleDraft.apply` verifies
Send later and the applied form and summary. `ScheduleDraft.save_and_preview`
claims the single attempt in the mounted output directory before clicking.
Use the rendered exact name, campaign ID, explicit Draft status, and persisted
Send later date, time, and timezone to verify success. If Preview lacks a
required value, use the existing read-only list fallback. Leave an uncertain
save unconfirmed and retain the claim for read-only reconciliation. Do not
repeat the save or schedule delivery, activate, publish, or send.

Unattended runs skip tenant, name, draft, and review-hold prompts. The process
exits successfully only after the required draft readback and owned-browser
cleanup. The interactive entry points continue to use terminal confirmation.
See [Local Docker](local-docker.md) for the exact launch.
