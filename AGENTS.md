# Netcore Agent repository engineering rules

These instructions apply to all work under this project root. Preserve compatible
repository policies and more specific instructions. Follow the user's authorized
scope and higher-priority instructions. Do not replace established policies when
adding guidance for later sessions.

## Root-cause debugging

- Diagnose before editing. Trace the failure through the real call path or
  reproduce it with the smallest relevant local test. Separate confirmed causes
  from hypotheses; historical run IDs are regression evidence, not assumptions
  about future failures.
- Examine affected callers, declared tool schemas, dispatchers, deterministic
  executors and verification paths together. Do not make an isolated tool fix.
- For tool-contract defects, first report the existing inconsistencies and the
  minimal correction plan. Make the smallest architectural correction justified
  by the evidence. Freeze unrelated application behavior.
- Do not add duplicate browser adapters, classifiers, fallback frameworks,
  orchestration frameworks or global scanners. Reuse existing components.
- If Superpowers or Context7 is available and relevant, follow its applicable
  instructions. Otherwise apply systematic root-cause debugging and verification
  before completion without adding dependencies merely to obtain those tools.

## One consistent browser capability contract

- Maintain one authoritative field-to-capability registry. Derive exposed tool
  schemas and dispatcher validation from that contract; the executor and verifier
  must support exactly the same advertised field/action combinations.
- For each action, specify supported fields, allowed arguments, authorization,
  prerequisites, deterministic execution and independent verification. Scope
  special flags, including dependency initialization, to their supported actions
  and fields.
- Reject unsupported combinations explicitly at the action boundary, before
  invoking an unrelated executor or performing a browser interaction. Never
  advertise a combination that only fails later in another widget's implementation.
- Tool descriptions and observations must accurately describe capabilities,
  current evidence, unresolved predicates and permitted next operations.
- Keep read-only Doer observation independent of complete form recognition,
  legacy heading matches and global DOM/shadow inventory certification. An empty
  initial observation must remain observable and diagnosable; it must not silently
  prevent genuine Doer invocation.
- Read-only observation still requires authorized origin, correct protected
  session/tab and healthy browser transport. Apply field-specific restrictions at
  the action boundary rather than using them as global observation blockers.
- Before writes, retain required current-target identity, ownership, visibility,
  enabled-state, authorization and authentication/security checks. Verify actual
  entered/selected state independently; a click, typed search string or model
  success report is not sufficient.

## Ownership, authentication and authorized scope

- Preserve owned-browser lifecycle, session continuity, protected tabs, navigation
  guard, credential broker, one-credential-submission limit and established working
  Login/Campaign behavior. Do not repeat login between learning attempts.
- Never retry rejected credentials or uncertain writes. Security failures, lost
  authorization, uncertain target identity and irrecoverable transport failures
  remain fail-closed.
- Distinguish independently verified authentication from an explicitly authorized
  operator-confirmed checkpoint. A URL transition, elapsed settling time or new
  screen alone is not authentication proof. Do not invent account indicators,
  selectors or authenticated endpoints.
- Use only separately authorized Netcore operations. Standing restrictions prohibit
  Save, Finish Later, Next Step, Audience modification, Schedule, Publish, Activate
  and Send. These require separate explicit authorization and a reviewed
  implementation; a Teacher recommendation or nominal audience never grants it.
- Keep approved fixed field values and existing-tag-only restrictions within the
  authorized task. Do not create tags or change unrelated account settings.
- Do not access or export cookies, JWTs, session storage or authentication artifacts.
  Credential values remain within the existing trusted credential handler; never
  expose them in prompts, tool arguments/results, logs, screenshots or skills.

## Doer–Teacher learning and evidence

- A deterministic Python, schema, dispatch or executor defect is
  `CODE_FIX_REQUIRED`, not a model reasoning failure. Teacher may analyze it, but
  must not issue executable skill corrections as a substitute for code repair.
- Classify the responsible component from evidence. A controller blocking Doer
  startup is not a model reasoning failure. Record unknown ownership honestly.
- Capture meaningful sanitized failure evidence whenever technically possible:
  actual attempted/successful actions, tool results, failed verification predicates,
  browser/session health, iteration, strategy version and retry safety. Record
  capture/transport gaps explicitly rather than fabricating observations.
- Teacher lessons must cite actual execution events. Validate candidate scope,
  supported tools, evidence and retry safety before loading guidance. Unsupported,
  contradictory, duplicate or unsafe lessons must not become executable policy.
- Preserve approved skills separately from versioned, unapproved Teacher
  candidates. Record actual loaded candidate bytes and their hash for each
  iteration; retain previous versions, provenance, journals and rollback paths.
- Do not promote unsupported guidance or automatically overwrite approved skills.
  Preserve original traces and failure records; never rewrite them to appear more
  successful. Archive important evidence before any justified deletion.
- Keep attempt, request and token budgets finite. Reserve final Teacher review
  within the authorized budget; exhausted attempts do not authorize another Doer
  attempt. Stop unsafe retries even when a Teacher proposes continuing.
- After success, preserve evidence-backed optimization candidates for review.
  Compare only equivalent starting states; an already-filled live form is not a
  fresh optimization trial or proof of improvement.

## Focused verification before completion or further live execution

- When a tool changes, test every affected supported field/action combination
  and explicit rejection of unsupported combinations, including argument flags,
  prerequisites, readback, authorization and uncertain-write handling.
- Run focused local tests before further live execution. Use actual local Chromium
  when the change concerns browser interaction or lifecycle; model doubles do not
  establish actual provider or tenant compatibility. Avoid unrelated broad suites.
- Do not delete tests, rebase historical integrity manifests, weaken security or
  modify unrelated code merely to make checks pass. Report pre-existing failures
  separately from regressions introduced by the change.
- Before declaring completion, verify the actual diff, affected contracts and
  appropriate test results. Report the confirmed root cause, files changed,
  tool-contract impact, tests executed and remaining uncertainty.
- Distinguish implemented, locally verified, live Netcore-verified and unverified
  capabilities. A fixture pass alone does not establish that a live defect is
  repaired. State the exact validation level and whether a live retest occurred.
- Report actual provider requests and input/output/total tokens across the whole
  session, separated by Login Doer, task Doer, Teacher and iteration where available.
  Include cached/reasoning tokens as subsets, without double counting. Missing
  usage is unavailable/null, not zero; configured limits are not actual usage.
- Report verified fields, outcome, elapsed time, failure owner and cleanup status.
  Do not claim cleanup without confirmation or kill/adopt unrelated browsers.
- Live execution and paid inference require applicable explicit authorization;
  permission for an engineering-rules or documentation task does not grant either.

## Repository guidance

### Development tooling ownership

- `.agents/skills/` contains project-specific Codex development references.
  For a browser schema/dispatch/target/readback audit, read
  `.agents/skills/browser-tool-contract-audit/SKILL.md`. For execution evidence,
  Teacher/candidate evaluation or token accounting, read
  `.agents/skills/doer-teacher-evaluation/SKILL.md`.
- `skills/` remains the existing Python/runtime task-skill package and versioned
  store used by the Doer–Teacher. Do not move it into `.agents/skills/`, load Codex
  development references as runtime guidance, or promote runtime candidates while
  installing development tooling.
- Superpowers is globally installed. Use the available global skills; do not copy
  them into the project. Project references supply source maps, not replacement
  methodology or duplicate engineering policies.
- Context7 documentation access and optional GitHub access are supplied by global
  MCP configuration or connected apps. Do not duplicate their configuration or
  credentials in this repository. Use GitHub repository tools only when remote
  access is needed; it is not a prerequisite for local work.

Use the existing package boundaries and operational guidance in `README.md`,
`docs/architecture.md`, `docs/operations.md` and `docs/known_limitations.md`.
Consult `learning/DOER_FAILURES.md` and saved sanitized traces for historical
evidence. Treat historical repair reports and Teacher proposals as evidence with
their original validation limits, not as automatically approved runtime policy.

Prefer correct, observable, bounded execution over speculative fixes or extra
guardrails. Do not weaken established protections merely to improve success rates.
