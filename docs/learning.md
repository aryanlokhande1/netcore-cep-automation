# WhatsApp learning and skill approval

Status: source-level implementation only. No V2 learning iteration has run.

LangGraph in modules/whatsapp/workflow.py owns all WhatsApp iteration decisions.
Ordinary recovery stays in the current session. A fresh learning iteration creates
a new Trace/session ID, owned Chromium/profile and existing Login execution, then
requires independent authentication/tenant verification before task actions.
One credential submission per fresh session remains enforced by the existing broker.

## Recovery and review

The normal STUCK threshold is two materially different failed methods/preparations,
recorded from actual tool events and Observer findings. Repeating a click is not a
new method. Security, transport, authorization, code defects and uncertain writes
can stop earlier. A bounded turn/request/token deadline also stops execution.
Teacher reviews completed outcomes and failures even without a natural-language
STUCK declaration. Cancellation defers inference and proceeds to owned cleanup.

Teacher is a real create_deep_agent with structured V2Lesson output and no browser,
filesystem or source-edit tools. It receives goal/history, Observer evidence,
failure ownership, skill/candidate context and actual citation IDs. It can propose
typed conditional procedural steps beyond the former three ordering templates.
Only supported stage/condition/action/field combinations render into candidate
instructions. Free-form findings do not become executable instructions.

Code defects are CODE_FIX_REQUIRED and never become prompting corrections.
Candidate guidance cannot change tools, values, authorization or retry policy.
Duplicate candidates are suppressed across history. Source, base and parent hashes,
task digest, iteration ID, evidence references and change summary are retained.

## Storage

Existing learning/skill_storage/registry.py and ApprovedSkillBackend are reused.
Login defaults and its existing namespace remain separate.

WhatsApp candidate history: outputs/skill_history/whatsapp/candidates/<version>/.
Each version contains immutable SKILL.md and provenance.json. Per-iteration results
and selected skill bytes are under outputs/whatsapp-v2/<session ID>/.
The result distinguishes selected bytes from task skill actually loaded.
Candidate generation is not evidence of improvement. Only a subsequent authorized
execution can establish consumption and verified outcome.

skills/whatsapp is an UNAPPROVED_V2_CANDIDATE, not an approved/graduated skill.
Until promotion, execution requires explicit candidate-evaluation authorization.
The workflow never calls the promotion function.

## Graduation

approve_whatsapp_candidate requires explicit exact-digest approval and at least
three original real-run records with distinct session IDs, the same loaded candidate,
verified authentication, Observer draft verification, Teacher review, confirmed
cleanup and comparable starting state. Synthetic counters/results cannot qualify.

Fresh browser is not fresh server state. Existing drafts block subsequent creation.
This implementation does not automatically reset or resume a persisted campaign.
A self-contained three-success trial for the fixed name is therefore blocked until
the operator approves a safe, comparable server-side starting-state procedure.
The fresh-session lifecycle is implemented; that missing policy is not fabricated.
No graduation is claimed or performed.

## Accounting

V2 execution evidence is rendered by the existing Trace class into the root output
agent-trace JSON and agent-report Markdown. The chronological timeline is based on
actual middleware/tool calls and controller persistence calls, not reconstructed
model reasoning. Candidate selection and immutable prompt loading are separate
events; subsequent dispatch/model result records establish actual consumption
attempts. A newly generated candidate cannot prove its own later loading. Inspect
the later run's version/hash/loading events and original result, never infer it
from a generated file or rewrite historical provenance to assert improvement.

The existing meter tracks Doer, Observer and Teacher. Login and task Doer usage are
separated in iteration results. Provider dispatch counts are distinct from requested
model turns. Dispatch-ready payload counts do not establish HTTP transport counts;
actual provider request counts remain null without independent transport evidence.
Partial or missing provider token usage remains null. Cached/reasoning
tokens are included subsets, never added to totals. Teacher request/token capacity
is reserved within the finite shared budget.

## References

- https://github.com/shubh24/ai-engineer-autobrowse
- https://github.com/steel-dev/awesome-web-agents
- https://www.stagehand.dev/
- https://ai.engineer/talks/p7gaxmCUiIU-hill-climbing-skills-improve-agents-without#talk-resources

These are architectural/learning references, not proof of Netcore compatibility.
They were not fetched during this implementation assignment.
