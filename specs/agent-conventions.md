---
id: SPEC-0001
title: Apply the agent-factory convention to structure and operation
type: convention
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# Apply the agent-factory convention

## Context

The repository governs agents through prose (`PRINCIPLES.md`, `docs/DIRECTIVE.md`)
and one guard test, aimed at a human-driven session. There was no root
`AGENTS.md`, no declarative operating mode, no CI, and Law 11 / Law 12 were not
machine-enforced — so a harness that natively edits in place and runs the suite
could not "obediently follow" them. Three reference PDFs (Google agent SDLC,
HackerNoon dark factory, Osmani software factories) agree on the skeleton:
instructions + progressive disclosure + deterministic gates, with verification
(not generation) as the constraint.

## Decision

1. **Operating levels as modes.** `.agentfactory.toml` is the single source of
   truth; L0 harness-only → L3 dark-factory, each setting *autonomy / gates /
   review*. L3 is declared but gated (`status = "declared-gated"`).
2. **Thin entry point.** `AGENTS.md` is a table of contents that points to
   `PRINCIPLES.md` (constitution) and `docs/agent/` (progressive disclosure);
   `CLAUDE.md` / `GEMINI.md` are one-line pointers.
3. **Law 11 enforced, not requested.** `opencode.json` denies `edit` (which also
   denies `write`/`patch`); all mutation goes through `tools/safe-replace.sh`.
4. **Law 12 amended for automation with checks.** The authoring agent never runs
   the suite or claims a pass; the operator (L0–L2) or an isolated validator
   (L2–L3) does.
5. **Specs first at L2+.** `specs/` holds front-mattered specs; `specs/holdout/`
   is reserved for scenarios the author must never see.
6. **Deterministic enforcement.** `tests/test_agent_conventions.py` guards this
   layer; `.github/workflows/gates.yml` runs the gates in CI.

## Scope

- **In:** mode policy, entry files, `docs/agent/`, `specs/`, CI, guard, Law 12 amendment, branch/topology docs.
- **Out:** building the L3 validator; auto-merge; any change to `src/` semantics; any remote push.

## Acceptance criteria

- [ ] `.agentfactory.toml` parses and `factory.level` is a valid level.
- [ ] `AGENTS.md`, `CLAUDE.md`, `GEMINI.md` exist; pointers resolve.
- [ ] `opencode.json` sets `permission.edit = "deny"` with `$schema`.
- [ ] `tests/test_agent_conventions.py` passes under the operator's run.
- [ ] CI runs `ruff`, `mypy`, `cbp-check`, and the suite.
- [ ] `PRINCIPLES.md` Law 12 reflects the amendment; README/HANDOFF state the new branch topology.

## Risks / invariants

Respects Laws 4 (progressive disclosure), 6 (auditability), 11, 12; does not
touch KERNEL invariants or `src/` semantics. L3 is not activated.
