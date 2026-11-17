> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Development-tooling audit

Date: 13 November 2026. Controlled documentation and skill-reference integration;
no source moves, browser execution, Netcore login, campaign action or application
model inference. No dependency installation or runtime skill promotion.

## Original structure and findings

The root already contained app, browser, deep_agents, learning, modules, skills,
evals, tests, docs and outputs, plus private/generated directories. The audit
excluded private environment variants, credentials, profiles, outputs and caches
from content/fingerprint inspection. The non-private inventory included 149 Python
files: 104 runtime modules and 45 test modules. No folder was inferred absent from
an editor view: .agents/skills was checked directly and did not yet exist.

`skills/` is both an imported Python package and a versioned runtime skill store.
Actual consumers include app.cli, app.whatsapp_baseline, deep_agents.controller,
deep_agents.doer, learning.autonomous, learning.three_run and the retained
modules.whatsapp.workflow. ApprovedSkillBackend exposes an immutable virtual
/approved-skills/netcore-login/SKILL.md catalog; SkillStore owns the real versioned
bundles. Moving skills/ would break imports and consumers. No source was proven
dead. Older seven-tool and explicit legacy paths remain reachable and retained.

Genuine organization issues were an absent project Codex reference location and
architecture text that described the baseline as always model-free despite the
opt-in autonomous path. Existing historical reports and generated evidence were
not treated as disposable duplicates. Nothing was deleted merely to reduce counts.

## Actual changes and ownership

Created:
- .agents/skills/browser-tool-contract-audit/SKILL.md (416 words).
- .agents/skills/doer-teacher-evaluation/SKILL.md (484 words).
- This audit report.

Modified:
- AGENTS.md: appended development-tooling ownership; existing policies preserved.
- README.md: linked development references and this audit.
- docs/architecture.md: clarified default versus autonomous execution, corrected
  broken flow-arrow text and documented ownership in the existing architecture guide.

Moved/removed: none. Runtime code, tests, dependencies, .gitignore, runtime skill
bundles and learning history were retained. Non-private baseline hashes prove
existing runtime/test/history files were unchanged. No protected directory was
cleaned or overwritten. Netcore_agent_archive and Reference were not modified.

AGENTS.md remains policy. Project .agents/skills contains Codex source-map and
evaluation references; it is not loaded by application agents. Global Superpowers
provides methodology and was not copied. learning remains shared runtime/evaluation;
modules remains task-specific capability code; tests/docs/outputs keep their roles.
The new guides provide concrete retrieval maps rather than duplicate policy.

## Global versus project configuration

Global Superpowers skill files were read from the installed plugin, including
systematic-debugging, writing-skills, its TDD/discovery references and
verification-before-completion. Context7 is also declared in the existing global
Codex configuration; only its section name was inspected, not configuration values
or credentials. Context7 and GitHub are available through the connected tools.
Neither MCP configuration nor credentials were copied into this repository.
No project .mcp.json or .codex/config.toml was introduced.

Context7 successfully resolved and queried official Playwright Python native
select documentation. GitHub's read-only authenticated identity request succeeded;
no repository query, remote mutation or GitHub requirement was added.

The existing .gitignore already excludes .env/private variants, credentials,
profiles, outputs, runtime skill state and generated learning/build artifacts.
Project .agents/skills remains available for future version control. No ignore
change was justified. Git reports this directory is not a repository: whether any
sensitive files are tracked cannot be established. No repository was initialized
and no history was rewritten.

## Skill-reference and discovery verification

Baseline fresh-context retrieval found neither project guide; it did not indicate
a runtime failure. Both scenarios could identify relevant source manually. No claim
is made that the guides improve reasoning quality; they centralize retrieval.

The contract guide was written and tested first. A fresh-context reader found
abbreviated paths and a missing shared-loop source reference; those were corrected
and the reader confirmed the gaps resolved. It applied the guide to distinguish
schema admission from actual Campaign-dropdown support and late dependency-flag
validation. The capability registry is still required policy, not implemented by
this tooling task.

The learning guide was then written and independently checked. All 18 referenced
source/test paths existed. Its reader correctly separated cumulative login/task
usage, identified required reload evidence, and refused to interpret a terminal
failure's old candidate as safe runtime guidance. This is reference retrieval and
application validation, not an adversarial behavior-improvement benchmark.

Both guides passed YAML name/description, matching parent-folder name, frontmatter
length, word-count and root-relative reference checks. A newly started installed
Codex app-server accepted initialize and read-only skills/list with forceReload
for the project cwd and returned both exact project skill names. No model turn or
browser agent was started. The first restricted probe failed to resolve its home
directory; an authorized read-only retry outside that restriction succeeded with
no startup error. This verifies fresh app-server discovery, not live agent behavior.

## Commands and results

- `git status --short`: unavailable, not a Git repository.
- `git diff --stat`: unavailable; original-document unified diffs and baseline
  SHA-256 comparisons were inspected instead. No claim of a clean Git diff.
- Runtime compilation and imports with `python -B`: 104 modules passed.
- `python -B -m app.cli --help` and `python -B -m app.whatsapp_baseline --help`: passed.
- `pytest --collect-only -q -p no:cacheprovider`: 665 tests collected. Browser
  baseline, browser-learning, interactive-widget and Teacher-reliability tests
  remain discoverable (3, 2, 2 and 13 collected respectively).
- Focused tests with RUN_* flags removed and cache provider disabled:
  test_browser_baseline.py, test_baseline_login_handoff.py and
  test_browser_learning_loop.py: 8 passed, 1 actual-browser test skipped.

No files moved, so there were no import/path migrations to regression-test. The
entire suite was not executed; known historical failures are not claimed fixed.
No protected execution record was regenerated. CLI help and reference validation
are development-tooling checks, not production or tenant compatibility evidence.

## Remaining issues

No local Git metadata means no normal diff/status or tracked-secret determination.
The runtime still lacks the authoritative capability registry identified by the
contract audit. Historical source-hash/contract test debt remains. Some historical
repair documents describe earlier behavior; they remain evidence, not current
policy. Runtime skills, candidate versions, outputs and temporary execution helpers
were retained wherever ownership or future replay use was uncertain. No proven
obsolete source or safe redundant runtime implementation was removed.

Future code work should use the two project references with AGENTS.md and global
Superpowers, then perform its own focused contract/learning verification. These
skills do not authorize browser tests, paid calls or another live Netcore run.
