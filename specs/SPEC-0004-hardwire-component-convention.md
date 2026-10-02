---
id: SPEC-0004
title: Hard-wire the component/skill convention (auto-discovery + scaffold)
type: convention
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# Hard-wire the component/skill convention

## Context

SPEC-0003 closed the convention gaps for the set that existed then, but
enforcement was an allowlist (`_REQUIRED_SKILLS`) and authoring a component was
manual. The operator will create **many** components/skills; the convention must
be wired into the creation path and the guard, not maintained by hand.

## Decision

1. **Auto-discovery.** `tests/test_agent_conventions.py` discovers every
   `SKILL.md` under `.agents/skills/` and holds each to the contract:
   `name` equals the directory, `description` is 1–1024 chars,
   `metadata.component` equals the name, `metadata.determinism` is
   `deterministic` or `suspect`, and both required sections are present.
   `_REQUIRED_SKILLS` is only the law-critical minimum that must exist.
2. **Scaffold.** `tools/new-skill.sh <name> [deterministic|suspect]` emits a
   canonical `SKILL.md` atomically. A component created this way is born
   compliant and is discovered immediately — no registration step.
3. **No stray dirs; mirrored exposure.** The guard fails on a skill directory
   without a `SKILL.md`, and asserts `.claude/skills` exposes every discovered
   skill.
4. **Document the workflow.** A `skill-authoring` component and the human page
   [`docs/agent/components.md`](../docs/agent/components.md) describe the
   contract; `AGENTS.md` points at the scaffold.

## Scope

- **In:** `.agents/skills/**`, `tools/new-skill.sh`, the guard,
  `docs/agent/components.md` and the map, `AGENTS.md`, this spec.
- **Out:** `PRINCIPLES.md` laws, `.agentfactory.toml` level, `src/` semantics,
  KERNEL invariants, execution backends.

## Acceptance criteria

- [x] Every discovered skill satisfies the canonical contract (guard).
- [x] A skill directory without `SKILL.md` fails the guard.
- [x] `tools/new-skill.sh` exists, is executable, and refuses invalid names and
      overwrites.
- [x] `.claude/skills` exposes every discovered skill.
- [x] `tests/test_agent_conventions.py`, `ruff`, and `mypy src` are clean.

## Risks / invariants

Respects Laws 4 (progressive disclosure), 6 (auditability), 11 (whole-file
writes), 12 (test authority), and 13 (commit and push). It touches no `src/`
semantics and no KERNEL invariant. Auto-discovery considers only
`SKILL.md`-bearing directories (dot/underscore-prefixed dirs are ignored), so
scaffolding can be iterative without failing the guard mid-step.
