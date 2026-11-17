# Architecture

Status: IMPLEMENTED — NOT LIVE VERIFIED. Static validation is not compatibility proof.

## Canonical task path

app.cli whatsapp -> modules/whatsapp/workflow.py -> one LangGraph task lifecycle.
The graph owns start/Login, Doer, Observer, advance, Teacher, candidate and cleanup.
Fresh iterations route back to start only after safe review and confirmed cleanup.

Existing Login uses deep_agents/controller._execute_doer, HandoffLoginTools and
CredentialBroker, without changing their default execution semantics. Independent
Login/tenant proof is required before V2 task actions. No URL or operator checkpoint
is substituted for that proof. Browser ownership remains StagehandClient,
NavigationGuard, protected_tabs and close_owned_session.

V2 can opt into the existing passive ReadOnlyDiscovery and interactive EvidenceReview
in that same Login session. Operator digest approval still requires all independent
authentication/tenant gates; there is no POC bypass. Missing record evidence selects
a restricted read-only discovery branch on the actually observed authenticated view.
It cannot navigate to creation, write values or advance. ReviewedUI is incremental;
field identities are discovered/revalidated now, saved-record mappings are required
where their operations occur. Doer, independent Observer and Teacher share the same
graph, browser interface and existing trace in both branches.

## Responsibilities

Login verification retains the same five independent predicates and negative-state
stops. Current SDK/CDP document context is recorded without URLs/identifiers.
Unknown custom-element shadow scope requires the existing two-pass protected CDP
inventory, not tag-name assumptions. Scope certificates never supply dashboard,
tenant or authenticated-response proof. Operator-reviewed actual evidence remains
required. Alert/transient headings cannot certify a dashboard.

V2 retains actual Login results before its authentication gate. Teacher receives
the existing canonical evidence compiler's Login tool results/observations/review
facts with original event indices, plus WhatsApp events where available. A required
validated factual summary and Login citations prevent the known no-browser denial;
rejected model output is preserved without becoming executable guidance. Grouped
source evidence and input hashes are saved in each future run's existing directory.

- contract.py: immutable payload, stage fields, permissions, action and evidence types.
- tools.py V2BrowserTools: one tool interface; protected node identities; native
  locator/coordinate operations; ephemeral scoped images; persistent-write journal.
- observation.py: current task-scoped control descriptions, not completion proof.
- observer.py: separately queried properties/selected states and reviewed saved
  record evidence; bounded LLM interpretation; no action tools.
- agent.py create_v2_doer: genuine Deep Agent with immutable skill bytes and tools.
- deep_agents/teacher.py: reused Deep Agent factory with opt-in V2 schema/prompt.
- learning/skill_storage/registry.py: version/hash lineage, typed procedural
  candidates, duplicate/scope checks and explicit promotion gates.
- app/trace.py, permissions.py, telemetry.py: existing accounting extended for Observer.

Stage advancement requires fresh deterministic checks after Observer. The actual
save call consumes a one-use controller certificate and writes a pending journal
before the SDK action. Read-only record verification is required afterward.
Pending/uncertain writes block repetition across sessions.

## Preserved and retired code

Proven Campaign fill/readback is reused through BrowserBaseline with injected
approved task values and a V2 guard; its default legacy behavior is unchanged.
Other baseline/widget helpers remain importable as historical/internal components.

app.whatsapp_baseline, run_phase1, continue_setup, learning_loop.run_loop and
browser_runtime.run_task are explicitly inactive for task execution. Their bodies
remain behind retirement guards to preserve import/provenance compatibility.
No new manager agent, browser adapter or telemetry framework was added.

## Storage and privacy

Private payload/UI/auth evidence stays under ignored credentials paths. Images
require separate vision permission and a reviewed stage region, never Login chrome.
No cookies, JWTs or storage artifacts are exported. Original output paths remain.
Doer/Observer/Teacher messages are not persisted as raw transcripts. Results retain
structural events, predicates, hashes, usage and cleanup confirmation.

Current request roles are Doer, Observer and Teacher. Login defaults still create
the original Doer/Teacher accounting pair. Historical dependency_map.json is a
pre-V2 snapshot, not the current import graph.

## Architectural references

See learning.md for the four supplied project references. They are inspiration,
not Netcore execution evidence.
