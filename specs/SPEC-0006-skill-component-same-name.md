---
id: SPEC-0006
title: A skill maps to a component of the same name
type: convention
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# A skill maps to a component of the same name

## Context

SPEC-0005 made each skill declare `metadata.terminates-in` naming a command or
gate. That conflated the *mapping* with the *implementation*: in CBP a skill maps
to a **component** — its deterministic execution counterpart — and that component
shares the skill's name. The concrete command belongs to the component, not to
the mapping.

## Decision

- The mapping is carried by `metadata.component`, which **must equal the skill
  name**: skill `<name>` maps to component `<name>`.
- `metadata.terminates-in` is removed. The component's concrete command/gate is
  described in the `## Deterministic termination` section.
- The guard enforces `metadata.component == name` for every auto-discovered
  skill, so a skill whose component name differs fails closed.
- The scaffold emits exactly `component: <name>` and `determinism: <...>`.

## Scope

- **In:** `.agents/skills/**` frontmatter, `tools/new-skill.sh`, the guard,
  `docs/agent/components.md`, this spec.
- **Out:** `src/` / `contracts/` — the runtime `component.v1` / `skill.v1`
  binding stays roadmap (SPEC-0002 §6, §8); `PRINCIPLES.md` laws;
  `.agentfactory.toml` level; KERNEL invariants.

## Acceptance criteria

- [x] No skill declares `metadata.terminates-in`.
- [x] Every discovered skill has `metadata.component == <skill name>`.
- [x] `tests/test_agent_conventions.py`, `ruff`, and `mypy src` are clean.

## Risks / invariants

Respects Laws 4 (progressive disclosure), 6 (auditability), 8 (no unverified
success), 11, 12, 13. Binding this same-name mapping to a runtime `component.v1`
identity is roadmap and would need its own spec plus a `src/contracts/` addition.
