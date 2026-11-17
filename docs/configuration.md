> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Configuration

Use Python 3.11+ with `requirements.lock.txt`. The lock is unchanged and pins the
resolved environment, including tests. Direct imports previously supplied only
transitively are now explicit in `pyproject.toml` at the same locked versions.
Explicit-package discovery excludes generated learning/skill folders. Prompts
and baseline login skill Markdown are declared package data.

Operation currently requires a writable source checkout: `app.config.ROOT` comes
from the module location. Read-only site-packages/wheel deployment is not supported.
Wheel building remains unverified: installed setuptools is 65.5.0, below the
existing >=68 build requirement, and wheel is absent. No build tooling was
installed/downloaded. Fresh locked installation and index availability remain
unverified; the lock has versions, not distribution hashes.

## Environment settings

`.env.example` contains blank secrets and documented defaults. Manual baseline
mode does not load `.env`, credentials or providers. Doer login mode loads private
configuration only after explicit live/budget authorization. Controlled learning
uses an exported Chromium path without `.env`; older offline fixture CLI paths
still use `Config.load()`.

| Settings | Meaning/default |
| --- | --- |
| `CHROMIUM_EXECUTABLE_PATH`, `HEADLESS` | Installed executable, headed by default; baseline requires `--chromium` and stays headed |
| `NETCORE_LOGIN_URL`, `NETCORE_EXPECTED_HOST` | Fixed reviewed entry/host; altered configuration is rejected |
| `NETCORE_ACCOUNT_NAME`, `NETCORE_USERNAME`, `NETCORE_PASSWORD` | Private broker inputs; excluded from model arguments, prompts, traces and skills |
| `NETCORE_LIVE_LOGIN_ENABLED` | False by default; command authorization also required |
| `NETCORE_MAX_LOGIN_SUBMISSIONS` | Must be 1; uncertain submission consumes the latch |
| `NETCORE_EVIDENCE_FILE` | Private reviewed evidence configuration; missing evidence cannot certify authentication |
| `TEACHER_LIVE_REPLAY_ENABLED` | Must remain false; browser evaluation is local only |
| `NETCORE_LIVE_COMPARISON_ENABLED` | False by default; separate comparison authorization/budgets required |
| `OPENAI_API_KEY`, `OPENAI_MODEL` | Private key; configured `gpt-6-luna` only, no fallback |
| `DOER_MAX_OUTPUT_TOKENS` | Default 500, validated 1..4096 |
| `TEACHER_MAX_OUTPUT_TOKENS` | Default/max 2000 |
| `TEACHER_REASONING_EFFORT` | Default low; none/low/medium/high |
| `TEACHER_MAX_INPUT_TOKENS`, `TEACHER_REQUEST_BUDGET` | Default/max 8000 and 2 |
| `LEARNING_MAX_REQUESTS` | Default/max cycle requests 24; CLI may impose a smaller cap |
| `LEARNING_MAX_INPUT_TOKENS`, `LEARNING_MAX_OUTPUT_TOKENS` | Default 240000 and 21000 |

Model settings parse only selected model keys without credential interpolation.
Role-specific request/token meters enforce separate Doer/Teacher and shared cycle
limits. Authorized model availability lookup is separate from inference. Setup
baseline uses neither. Private `.env` and credential values were not inspected.

## WhatsApp Page 1 task configuration

`modules/whatsapp/page1.json` is the reviewed, non-secret runtime input source for
the workflow, tenant entry URL and four fixed values. It preserves the existing
regular WhatsApp creation route. `load_page1_task` rejects malformed/missing
configuration, non-HTTPS or unapproved origins, credentials/query/fragment in the
URL, other tenants/workflow routes, extra fields and values outside the existing approved
allowlist. Errors omit input values. Editing this file does not authorize a live
run or expand permitted actions. Existing `SETUP_URL` and `VALUES` imports now
derive from this validated file at process startup; there is no hot reload or
implicit `.env` override. New task values require separately reviewed scope.

`python -B -m app.cli whatsapp-contract` reads only public module configuration and
skill metadata. It reports field identities, values, allowed operations, exact
readback requirements, forbidden actions and progress/stop semantics without
loading credentials, private evidence configuration, Chromium or providers.

The registry's `module_baseline('whatsapp')` resolves only
`skills/whatsapp/SKILL.md` and `version.json`, checking content SHA-256, identity
and unverified metadata. Both WhatsApp agent paths select this baseline; the
seven-tool factory records its hash when assembling the prompt, and the nine-tool
adapter preserves exact initial bytes/hash in its existing iteration journal.
Phase B's explicit `whatsapp-setup --doer-only` mode additionally uses the native
immutable catalog and requires `read_module_skill` before browser actions; only
that actual read emits `SKILL_READ` with path, version and content hash. The
legacy factory default retains Phase A controller loading for compatibility.
Loading is not proof of live use,
approval or learning effectiveness. Its state is `UNVERIFIED_BASELINE`,
`approved: false`, `graduated: false`; it grants no action permission. The existing
Login `SkillStore` approval, promotion and rollback gates remain separate and
unchanged. Phase A implements no Teacher writer or skill promotion.

The four fields stop at `SETUP_FILLED_UNSAVED` only after current independent
readback; blocked attempts retain existing phase, failed-predicate and verified
field evidence. Authentication remains independently unverified until reviewed
evidence passes, unless a separately authorized supervised handoff is explicitly
recorded without changing that certification status. Phase B must validate actual
tenant interactions and evidence before claiming live workflow success. The
existing learning loop is retained, not established as a fresh-session learning
demonstration by this configuration change.

## Secret isolation

Never track `.env`, private credential/evidence files, profiles, tokens, session
exports, cookies or authenticated screenshots. Existing broker/browser session
handling is unchanged. `.gitignore` now also covers generated WhatsApp drafts,
legacy candidate reports and build/test artifacts. Recovery snapshots exclude
private environment files, credentials and profiles: they are not secret backups.
