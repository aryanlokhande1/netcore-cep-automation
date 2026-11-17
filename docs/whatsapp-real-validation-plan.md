# Bounded real Netcore validation plan — awaiting separate authorization

Target: one independently verified saved draft by 17 November 2026 (Asia/Kolkata).
This document is a reviewable proposal, not permission to run it or a readiness claim.
No browser, provider request, campaign action or test was executed to prepare it.

## Preconditions

- Preserve the approved 17 November 2026, 16:00 Asia/Kolkata private schedule and
  approve its exact payload digest. Stop if it expires; do not invent a replacement.
- UI/field evidence is incremental and optional before startup. Discover actual
  controls/states after independent authentication. Supply/review real record and
  current-stage evidence before the operations needing it, never guessed endpoints.
- Reuse independent Login evidence or the existing bounded interactive D/P/A passive
  discovery/review using --review-login-evidence --login-review-seconds 120. No task
  discovery before the existing verifier passes. Confirm owned Chromium, existing-only
  tags/template/audience values, intended account/tenant and no existing campaign.
- Review the unapproved task skill and explicitly authorize candidate evaluation.
- Separately authorize live browser, paid inference, fixed field edits, and each
  Next Step/Save and Preview persistence action for this exact payload. The standing
  no-publish/no-send/no-test-send boundary remains. Vision needs separate approval
  of the reviewed screenshot region and image transmission.
- Approve finite request/token/time caps before execution. Proposed first run:
  one iteration, Login <=6 requests, <=24 dispatch-ready requests overall, <=20 Doer
  turns, <=900 seconds. Existing settings may impose stricter limits. Teacher
  capacity is reserved inside those caps; exhaustion does not authorize another run.
  These are proposed caps, not a claim they suffice for the whole workflow.

## One stage-by-stage execution

Revised first review phase: launch only after separate authorization, authenticate
once, and use existing passive Login evidence review if needed. Without reviewed
campaign identity records, stay on the actually observed authenticated view, expose
only bounded read-only browser tools, invoke Observer/Teacher and stop with
DISCOVERY_REVIEW_REQUIRED. Review the captured real evidence before authorizing
creation/value edits. There is no automatic duplicate lookup bypass or continuation.
Future page selectors are collected only from actual reachable stages. The full
campaign phase below requires independent identity and persistence evidence at each
write boundary, rather than all page selectors before browser launch.

| Stage | Evidence required before continuing | Persistent operation |
| --- | --- | --- |
| Login | Independent authentication, tenant and session proof; one credential submission | None beyond authorized Login |
| Setup | Exact payload Campaign, explicitly selected WABA, Business, existing Tag, GA Off; Observer COMPLETE and fresh deterministic checks | One authorized Next Step, pending journal, independent draft/field readback |
| Audience | Exact Lists/Segments and inclusion/exclusion states; Observer milestone | One authorized Next Step plus independent saved-field readback |
| Content | Exact existing template and reviewed actual template preview; Observer milestone | One authorized Next Step plus independent saved-field readback |
| Schedule | Approved Send Later datetime/timezone, frequency cap enabled and contact limit disabled; Observer milestone | One authorized Save and Preview plus independent saved-field readback |
| Preview | Independently observed preview and exactly one authenticated draft record matching all requested fields | None; stop when independently verified |

The existing graph automatically invokes Observer after each persistence/navigation
operation before more Doer actions. Review chronological trace/checks stage by stage.
If predicates cannot be established, stop; do not infer success from a toast or URL.
All future discoveries/corrections stay within reviewed UI ownership and the existing
capability contract. A tool defect is CODE_FIX_REQUIRED and ends the attempt for
engineering correction; it is not permission for repeated prompting or another run.

## Stop and review rules

- Two distinct valid supported failed approaches to the same field without real
  progress produce STUCK and Teacher review. Successful opening/searching is progress,
  not completion. Identical failed approaches cannot replay.
- Authentication/security/authorization failure, changed identity, irrecoverable
  transport, cancellation or uncertain write stops immediately. Teacher cannot
  override it. After an uncertain save, only authorized read-only investigation is
  possible; never repeat the save, rename/delete a draft or create another campaign.
- Preserve the pending journal, original trace and hashes before any correction.
  If runtime evidence exposes a code defect, make the focused code correction, do
  static checks, and seek separate authorization for the next real attempt.
- Close only the owned browser/profile and confirm cleanup. No unrelated browser
  adoption or termination. Record unconfirmed cleanup honestly.

## Evidence to review after the run

`outputs/<run-id>-agent-trace.json` and `outputs/<run-id>-agent-report.md` must contain
actual invocations/tools, UTC timestamps/durations, safe arguments/results, DOM
structural references, transient screenshot references when authorized, deterministic
checks, Observer verdicts, graph routes/reasons, Teacher assessment/code-repair
recommendations, candidate/version/hash/prompt-loading events and role usage.
Original run result and legacy trace remain under outputs/whatsapp-v2/<run-id>/.
Missing events, tokens and transport counts remain unavailable, never reconstructed.
Screenshot bytes are transient, so references cannot support later image replay.

## Learning after the first saved draft

Only after one verified end-to-end draft, propose three separately authorized fresh
browser learning trials. Each needs independent Login, the exact selected candidate
bytes/hash, Observer feedback, Teacher review, finite caps and confirmed cleanup.
Approve a comparable server-side starting-state policy first: the fixed campaign
identity will already exist and the current implementation deliberately blocks
automatic resume/reset/rename/deletion. A fresh browser does not solve that collision.
Do not start three trials or claim improvement until that policy and each trial are
explicitly authorized. No automatic code edits or skill promotion.

Future command template is in docs/operations.md. It is not run by this task.
