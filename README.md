# Netcore CEP WhatsApp V2

Status: **IMPLEMENTED — NOT LIVE VERIFIED**. This implementation was checked only
with static source inspection. No V2 browser, Netcore or provider execution occurred.

The canonical WhatsApp path is app.cli whatsapp -> modules/whatsapp/workflow.py.
LangGraph owns the lifecycle; genuine Deep Agents implement Doer and Teacher.
A goal-oriented Observer combines LLM interpretation with independent deterministic
readback. Existing Stagehand/Chromium ownership, Login broker and verification are
reused. One scoped browser interface supports DOM and reviewed screenshot actions.

The target is Login -> Setup -> Audience -> Content -> Schedule -> Save and Preview
-> verified saved draft -> stop. Publish, activate, send and test-send remain
prohibited. Every Next Step is treated as a persistent action.

The supplied test values are private in credentials/whatsapp-v2-payload.json.
The approved schedule is 17 November 2026, 16:00 Asia/Kolkata; expiration blocks
execution. UI evidence is incremental: independent Login precedes bounded read-only
discovery; actual identity/duplicate and persistence evidence gates state changes.
All future page selectors are not required before launch. No selectors/endpoints
were invented, and no new live run occurred during the preflight correction.

- [Architecture](docs/architecture.md)
- [Setup and operations](docs/operations.md)
- [WhatsApp workflow and tools](docs/whatsapp.md)
- [Known limitations and evidence](docs/known_limitations.md)
- [Learning and approval](docs/learning.md)

Existing Login commands retain their source contracts. Retired WhatsApp entrypoints
fail explicitly; historical source bodies and run evidence remain preserved.
No historical tests, outputs, private state or candidate history were deleted.
Synthetic/mocked/fixture execution is prohibited under the revised policy.

Historical docs/configuration.md, browser_learning.md and all repair/consolidation
reports describe earlier implementations. Their banners identify them as history;
they are not current execution instructions. JSON manifests remain immutable.

Repository policy: AGENTS.md. Development references: .agents/skills/. Runtime
instructions: skills/. Python storage infrastructure: learning/skill_storage/.
The WhatsApp skill is an unapproved V2 candidate and has not graduated.
