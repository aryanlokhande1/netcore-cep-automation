> Historical record: this document describes an earlier implementation and is not authoritative for V2. See [architecture](architecture.md), [operations](operations.md), [WhatsApp](whatsapp.md) and [learning](learning.md). Original bytes are retained in the implementation source snapshot.

# Skill storage refactor ? implementation record

Migration completed without live tenant, browser, training or provider execution.
The previously approved design is implemented. Canonical visibility is not graduation.

## Final storage tree

```text
learning/skill_storage/
  __init__.py
  registry.py
  backend.py
  generation.py
  procedure.py
  audit.py
skills/login/
  SKILL.md
  version.json
outputs/skill_history/login/
  manifest.json
  candidates/
    5640b39f9b6b4dca8c700f8209e507ae/netcore-login/
    5c4c6fa22d264fbcaa846fbe1b7aedc7/netcore-login/
    5f8564328e1448c3a7395a91d8512134/netcore-login/
    62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/
    6316c05d2f7c450e97d0247160115438/netcore-login/
    69c98b6281764451b6672d3065cd67d3/netcore-login/
    6a1aaa74d6ba434194b9357e045489c9/netcore-login/
    6b43462a00564fb69025123f7d61afcf/netcore-login/
    809f983ae4da42749a2b5aed4e32eb46/netcore-login/
    8fa5dfb51e6f4704841b41df04e87c82/netcore-login/
    977ac1c686fe4258bcd011e16f7a15c7/netcore-login/
    b6d7d0aa842649dabc53a833ef0566fc/netcore-login/
    d84d5db9a56e400bae6214606a2469a2/netcore-login/
outputs/skill-migration-rollback-20261113-135716/
  before.zip
  manifest.json
deep_agents/prompt_builder.py
prompt.txt
```

No approved or versions directory existed to migrate. No candidate was generated.
The six former skills/*.py modules moved to learning/skill_storage/; imports,
package discovery and default history consumers follow that location.
NETCORE_SKILL_HISTORY_DIR optionally configures historical storage.
Native immutable approved-snapshot loading and approval/graduation gates remain.
Default loading returns NONE until explicit validation and approval.

## Canonical metadata

```json
{
  "identity": "netcore-cep-login",
  "approved": false,
  "status": "UNVERIFIED_INACTIVE",
  "source_version": 1,
  "content_sha256": "8984f5e88295c6f6e07a88767d8c0210e189c7d387fdf9cf53f8d90340579d5b",
  "provenance": {
    "source": "modules/login/skill/SKILL.md",
    "live_skill_load_provenance": "NOT_ESTABLISHED",
    "graduated": false
  }
}
```

SKILL.md is byte-identical to modules/login/skill/SKILL.md. Existing candidate
bundles and their internal provenance/manifests were not rewritten. All 13 bundles
were copied and hash-verified before old locations were removed. The history
manifest maps original paths to their preserved SHA-256 hashes.

## Rollback

The rollback ZIP was independently read back against all 778 manifest hashes.
To roll back, stop runtime processes, extract before.zip to a separate staging
directory, verify its files against manifest.json, then restore the original
paths from staging. Remove migration-only files using the change inventory below
after backing up any subsequent work. Do not delete the preserved history until
restoration is verified. This rollback is prepared, not executed.

## Prompt and entry points

python -B -m deep_agents.prompt_builder regenerates root prompt.txt using the
same Doer and Teacher builders as execution. It contains application-controlled
offline context only, explicitly omits session-specific handoff material, and
does not claim to capture framework/provider messages or private live context.
No unverified canonical skill is injected into the execution prompt.

Storage imports and AST parsing passed. Both app.cli --help and
app.whatsapp_baseline --help exited 0. app.cli skill status returned
{"approved_digest": null, "state": "NONE"}.

## Executed validation

Focused: python -B -m pytest tests/test_skill_storage.py tests/test_capability_contract.py tests/test_pass2.py -q --tb=short -p no:cacheprovider
Result: 185 passed.

Complete offline: RUN_* flags removed, OPENAI_API_KEY empty in child process;
pytest -q --tb=line -p no:cacheprovider --junitxml=outputs/skill-storage-final-offline.xml
Result: 750 passed, 20 failed, 56 skipped (826 total; 25.490 seconds).
Baseline: 750 passed, 17 failed, 56 skipped. All original 17 failures remain.
Three new failures are frozen source-hash assertions reflecting moved imports
and shared prompt extraction; frozen manifests are intentionally unchanged:
- tests.test_final_browser_readiness::test_teacher_and_accepted_replay_artifacts_are_unchanged
- tests.test_whatsapp_handoff_repair::test_original_records_and_accepted_teacher_replays_are_unchanged
- tests.test_whatsapp_phase1::test_preserved_login_browser_teacher_and_graduation_implementations

The suite is not green; source-integrity policy requires explicit review, not
automatic rebasing. Local checks do not establish real login skill provenance.
No paid inference occurred; provider requests/tokens: 0. Codex session token
usage is not exposed by these tools.

## Changed/moved tracked-source inventory

Changes relative to the rollback snapshot (includes removed old paths):
- app/cli.py
- app/whatsapp_baseline.py
- deep_agents/controller.py
- deep_agents/doer.py
- deep_agents/teacher.py
- learning/autonomous.py
- learning/three_run.py
- modules/whatsapp/workflow.py
- pyproject.toml
- skills/__init__.py
- skills/audit.py
- skills/backend.py
- skills/generation.py
- skills/netcore-login/candidates/5640b39f9b6b4dca8c700f8209e507ae/netcore-login/SKILL.md
- skills/netcore-login/candidates/5640b39f9b6b4dca8c700f8209e507ae/netcore-login/evaluation.json
- skills/netcore-login/candidates/5640b39f9b6b4dca8c700f8209e507ae/netcore-login/execution-history.md
- skills/netcore-login/candidates/5640b39f9b6b4dca8c700f8209e507ae/netcore-login/provenance.json
- skills/netcore-login/candidates/5c4c6fa22d264fbcaa846fbe1b7aedc7/netcore-login/SKILL.md
- skills/netcore-login/candidates/5c4c6fa22d264fbcaa846fbe1b7aedc7/netcore-login/evaluation.json
- skills/netcore-login/candidates/5c4c6fa22d264fbcaa846fbe1b7aedc7/netcore-login/provenance.json
- skills/netcore-login/candidates/5f8564328e1448c3a7395a91d8512134/netcore-login/SKILL.md
- skills/netcore-login/candidates/5f8564328e1448c3a7395a91d8512134/netcore-login/evaluation.json
- skills/netcore-login/candidates/5f8564328e1448c3a7395a91d8512134/netcore-login/execution-history.md
- skills/netcore-login/candidates/5f8564328e1448c3a7395a91d8512134/netcore-login/graduation.json
- skills/netcore-login/candidates/5f8564328e1448c3a7395a91d8512134/netcore-login/provenance.json
- skills/netcore-login/candidates/62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/SKILL.md
- skills/netcore-login/candidates/62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/evaluation.json
- skills/netcore-login/candidates/62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/execution-history.md
- skills/netcore-login/candidates/62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/graduation.json
- skills/netcore-login/candidates/62fe35b713e54e3eb71d3fbcaaf3aa37/netcore-login/provenance.json
- skills/netcore-login/candidates/6316c05d2f7c450e97d0247160115438/netcore-login/SKILL.md
- skills/netcore-login/candidates/6316c05d2f7c450e97d0247160115438/netcore-login/evaluation.json
- skills/netcore-login/candidates/6316c05d2f7c450e97d0247160115438/netcore-login/execution-history.md
- skills/netcore-login/candidates/6316c05d2f7c450e97d0247160115438/netcore-login/graduation.json
- skills/netcore-login/candidates/6316c05d2f7c450e97d0247160115438/netcore-login/provenance.json
- skills/netcore-login/candidates/69c98b6281764451b6672d3065cd67d3/netcore-login/SKILL.md
- skills/netcore-login/candidates/69c98b6281764451b6672d3065cd67d3/netcore-login/evaluation.json
- skills/netcore-login/candidates/69c98b6281764451b6672d3065cd67d3/netcore-login/execution-history.md
- skills/netcore-login/candidates/69c98b6281764451b6672d3065cd67d3/netcore-login/provenance.json
- skills/netcore-login/candidates/6a1aaa74d6ba434194b9357e045489c9/netcore-login/SKILL.md
- skills/netcore-login/candidates/6a1aaa74d6ba434194b9357e045489c9/netcore-login/evaluation.json
- skills/netcore-login/candidates/6a1aaa74d6ba434194b9357e045489c9/netcore-login/provenance.json
- skills/netcore-login/candidates/6b43462a00564fb69025123f7d61afcf/netcore-login/SKILL.md
- skills/netcore-login/candidates/6b43462a00564fb69025123f7d61afcf/netcore-login/evaluation.json
- skills/netcore-login/candidates/6b43462a00564fb69025123f7d61afcf/netcore-login/execution-history.md
- skills/netcore-login/candidates/6b43462a00564fb69025123f7d61afcf/netcore-login/provenance.json
- skills/netcore-login/candidates/809f983ae4da42749a2b5aed4e32eb46/netcore-login/SKILL.md
- skills/netcore-login/candidates/809f983ae4da42749a2b5aed4e32eb46/netcore-login/evaluation.json
- skills/netcore-login/candidates/809f983ae4da42749a2b5aed4e32eb46/netcore-login/execution-history.md
- skills/netcore-login/candidates/809f983ae4da42749a2b5aed4e32eb46/netcore-login/graduation.json
- skills/netcore-login/candidates/809f983ae4da42749a2b5aed4e32eb46/netcore-login/provenance.json
- skills/netcore-login/candidates/8fa5dfb51e6f4704841b41df04e87c82/netcore-login/SKILL.md
- skills/netcore-login/candidates/8fa5dfb51e6f4704841b41df04e87c82/netcore-login/evaluation.json
- skills/netcore-login/candidates/8fa5dfb51e6f4704841b41df04e87c82/netcore-login/execution-history.md
- skills/netcore-login/candidates/8fa5dfb51e6f4704841b41df04e87c82/netcore-login/provenance.json
- skills/netcore-login/candidates/977ac1c686fe4258bcd011e16f7a15c7/netcore-login/SKILL.md
- skills/netcore-login/candidates/977ac1c686fe4258bcd011e16f7a15c7/netcore-login/evaluation.json
- skills/netcore-login/candidates/977ac1c686fe4258bcd011e16f7a15c7/netcore-login/execution-history.md
- skills/netcore-login/candidates/977ac1c686fe4258bcd011e16f7a15c7/netcore-login/provenance.json
- skills/netcore-login/candidates/b6d7d0aa842649dabc53a833ef0566fc/netcore-login/SKILL.md
- skills/netcore-login/candidates/b6d7d0aa842649dabc53a833ef0566fc/netcore-login/evaluation.json
- skills/netcore-login/candidates/b6d7d0aa842649dabc53a833ef0566fc/netcore-login/execution-history.md
- skills/netcore-login/candidates/b6d7d0aa842649dabc53a833ef0566fc/netcore-login/graduation.json
- skills/netcore-login/candidates/b6d7d0aa842649dabc53a833ef0566fc/netcore-login/provenance.json
- skills/netcore-login/candidates/d84d5db9a56e400bae6214606a2469a2/netcore-login/SKILL.md
- skills/netcore-login/candidates/d84d5db9a56e400bae6214606a2469a2/netcore-login/evaluation.json
- skills/netcore-login/candidates/d84d5db9a56e400bae6214606a2469a2/netcore-login/provenance.json
- skills/procedure.py
- skills/registry.py
- tests/test_final_readiness.py
- tests/test_pass2.py
- tests/test_pass2_browser.py
- tests/test_target_attach_repair.py
- tests/test_three_run.py

New source/content files:
- deep_agents/prompt_builder.py
- learning/pass1_candidates/02d0a209ff8f4d6fb81715bbf09017dd.md
- learning/pass1_candidates/035f229adc864e3788cba81970a04dc6.md
- learning/pass1_candidates/04b2d562fdbe498bbb305921d267f666.md
- learning/pass1_candidates/0cd6c3f6033948f2b5544d0e9c9180a7.md
- learning/pass1_candidates/14c2ff9832464a34b2ef0e1c769f4b1d.md
- learning/pass1_candidates/22cfdaa0808647d8b0b4c0fb2ff1015a.md
- learning/pass1_candidates/43945850befc4da0bd3099c1946321e0.md
- learning/pass1_candidates/558f95e001204ea98643de5bf884991f.md
- learning/pass1_candidates/5b7a0578764144e0b065b7532c48dc6c.md
- learning/pass1_candidates/a2b00ce50c574a699752109cfa53dca0.md
- learning/pass1_candidates/c6072394868945c48c9ff93fa8ee735b.md
- learning/pass1_candidates/c87a4b7f3ec048d0811dcf0b22f708ae.md
- learning/pass1_candidates/cecb743e8bbe41d48a52b9fbce44106a.md
- learning/pass1_candidates/daf743e5ca054e2d8f065440e89dfbf4.md
- learning/pass1_candidates/e71bee86ae1645afba943cbb51dbdab3.md
- learning/pass1_candidates/f4047fd785e441248bfb51199794ac52.md
- learning/skill_storage/__init__.py
- learning/skill_storage/audit.py
- learning/skill_storage/backend.py
- learning/skill_storage/generation.py
- learning/skill_storage/procedure.py
- learning/skill_storage/registry.py
- skills/login/SKILL.md
- skills/login/version.json
- tests/test_skill_storage.py

Additional configuration/documentation: .env.example, .gitignore, AGENTS.md,
README.md, docs/architecture.md, .agents/skills/doer-teacher-evaluation/SKILL.md,
docs/skill_storage_refactor.md, prompt.txt.
Historical destination bundles and rollback files are listed above.

Remaining acceptance: separately authorized bounded live validation must prove
the selected approved skill is actually read by the Doer and authentication
is independently verified. This migration does not provide that certification.
