# WhatsApp V2

P0 routing correction: successful dropdown preparation does not count as a failed
approach. Only supported, safely failed distinct approaches on the same unresolved
field since verified progress support STUCK. Unsafe continuation remains BLOCKED.
The independent Observer is connected through the graph's observer node after Doer
turns and after persistence/navigation. Deterministic readbacks cover ordinary
interactions; independent model interpretation covers milestones, selection,
ambiguous/failing interactions and final draft verification.

Real executions write `outputs/<run-id>-agent-trace.json` and
`outputs/<run-id>-agent-report.md` through app.trace.Trace. They include actual model
and tool invocations, sanitized arguments/results, durations, structural observation
references, independent checks/verdicts, route reasons, Teacher assessment, candidate
hashes/loading and separate role usage. Images remain transient; no raw DOM, prompts,
credential values or private configuration are exported. Missing events/usage stay
unavailable. No such execution artifacts were fabricated during implementation.

Status: IMPLEMENTED — NOT LIVE VERIFIED. Static checks do not establish browser,
provider, tenant widget or saved-draft compatibility.

The only supported task path is `app.cli whatsapp` ->
`modules/whatsapp/workflow.py`. LangGraph owns Login, Doer, Observer, advancement,
Teacher, candidate creation, cleanup and fresh-session boundaries. There is no
second active WhatsApp learning scheduler.

## Payload and authorization

The initial payload is private: `credentials/whatsapp-v2-payload.json`, excluded by
the existing credentials ignore rule. It is separate from SKILL.md. The supplied
schedule is 17 November 2026, 16:00 Asia/Kolkata. Missing/invalid/expired time blocks before Login.
Startup requires explicit live/inference permission and exact SHA-256 approval of
the payload and any supplied incremental UI evidence. Creation/value writes additionally
require edit/persistence permission; without them the branch is read-only. Vision,
learning, candidate evaluation and fresh iterations are separately enabled.
No implementation-session authorization is carried into future execution.

## Goals

| Goal | Required evidence |
| --- | --- |
| Login | Existing independent verifier, expected tenant and reviewed resource evidence |
| Setup | Exact Campaign, explicit WABA opening/selection, Business, existing Tag, GA state |
| Audience | Exact Lists/Segments mode and inclusion/exclusion selected states |
| Content | Exact existing template and a separately reviewed rendered preview |
| Schedule | Send Later, approved local date/time/timezone, cap and limit states |
| Preview | Visible reviewed Preview plus one authenticated persisted draft record with all expected fields |

Next Step and Save and Preview are writes. Before them, fresh deterministic stage
checks must agree with Observer completion. A pending journal is written first;
only read-only record verification follows. An uncertain result blocks retries.
Published, activated or non-draft status never satisfies draft verification.

## Browser tools

One structured `browser_action` interface exposes observe/dom, inspect, screenshot,
click, type, scroll, options, search, select and verify. The graph owns advance.
The active kind/part/action matrix is in contract.py, not model prose.
Values and selectors are never model tool arguments. Dropdown search readback is
distinct from selected-state proof. Existing options require current ownership.

Coordinates are screenshot-relative pixels, expire after eight seconds and require
an observed reference, protected document, unchanged scrolling and current hit
target. Images are transient and only a separately reviewed active-stage region
may be transmitted. They are not saved to historical output directories.
Native Stagehand locator, page.click and page.scroll primitives are used. Native
Stagehand observe/act inference is not exposed: it would introduce another model
authorization/metering boundary. DOM then reviewed vision are the implemented paths.

## Reviewed UI evidence

ReviewedUI accepts partial evidence. Browser startup does not require every stage's
selectors or a draft-record endpoint. Existing Login passive discovery and explicit
operator digest review may establish authentication in the same owned session;
the independent verifier must still pass before any task discovery.

The existing explorer dynamically reads actual labelled controls, current native
options/selected states and owned popup evidence. Unambiguous observed bindings are
revalidated by document/control/scope identity and accessible label/type. Ambiguous
custom controls remain unresolved. Observed candidates are private and unreviewed;
they never invent/approve record resources or tenant identity. Discovery exposes only
the read-only subset of browser_action, and missing stage recognition remains visible.
Missing reviewed duplicate-record proof keeps the run on the actual authenticated
view and stops after bounded discovery/Observer/Teacher review. No creation route,
field edit, save or automatic fresh iteration is allowed in that branch.

Current field evidence gates edits; reviewed identity records gate creation and
value writes. Next Step/Save require current-stage fields, actual exact save control
and independent saved-field mapping. Only final Preview requires all saved-field
mappings. A currently observed unique Preview heading is a UI cue, never persistence
or authentication proof; final completion still requires exact independent draft data.

No tenant selectors or read-only endpoints were invented. Any supplied private JSON
must satisfy `ReviewedUI` in contract.py and its exact digest must be approved.
It supplies active-stage panel selectors, field controls/kinds, current selected
value selectors, existing-option/search ownership, Content preview, advance/save
controls, an optional reviewed visual region, and the reviewed read-only record
resource/key mapping. It includes reviewed origin, tenant and provenance reference.

Selected-state selectors must enumerate the actual selected values, not only the
desired value, so additional selections cannot be hidden. Split date/time bindings
use reviewed child inputs and explicit formatting in the payload timezone.
The record resource must be a reviewed side-effect-free same-origin GET exposing
authentication, tenant, intended campaign identity, draft status and saved fields.
No resource is selected automatically. Missing proof stays UNKNOWN/BLOCKED.

## Retired paths

whatsapp-setup/doer-only is absent from the normal parser. Direct Phase 1,
baseline and old scheduler entrypoints fail with RETIRED_WHATSAPP errors. Historical
bodies remain for import/provenance compatibility; they are not alternate live
workflows. Original source is also in the implementation snapshot.

## Limits

Unknown tenant widgets can be inspected and visually investigated, but writes still
require reviewed field ownership, current identity and security scope. This is not
an unrestricted computer-use agent. Existing drafts block automatic creation or
resume. Therefore repeated successful trials with one fixed campaign identity need
a separately approved server-side starting-state policy. V2 never renames, deletes
or blindly resaves drafts to manufacture three successes.
