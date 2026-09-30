---
id: SPEC-0005
title: Map every skill to its deterministic execution component
type: convention
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# Map every skill to its deterministic execution component

> **Superseded by [`SPEC-0006-skill-component-same-name.md`](SPEC-0006-skill-component-same-name.md).**
> The command-based `metadata.terminates-in` field below was replaced by the rule
> that a skill maps to a component of the same name (`metadata.component == skill
> name`). Kept as a historical record.

## Context

In CBP a skill **is** a component (SPEC-0002). A skill's own reasoning is
non-deterministic (`determinism: suspect`) and must **terminate in a call to a
deterministic process** — a command, a gate, or another component. Today the
execution target is only prose (the `## Deterministic termination` section),
so it is not machine-checkable and a freshly scaffolded skill passes the guard
even with placeholders. The formal binding (`skill.v1`, mirroring
`component.v1`) is declared roadmap and not built.

## Decision

Add a required, machine-readable execution mapping to every skill's
frontmatter:

```yaml
metadata:
  component: <name>
  determinism: deterministic | suspect
  terminates-in: <the deterministic component or command this skill ends in>
```

- **Scaffold** (`tools/new-skill.sh`) emits the field with a visible
  placeholder.
- **Guard** (`tests/test_agent_conventions.py`) requires `terminates-in` to be a
  non-empty string that is not a placeholder (`<…>`), for every discovered
  skill. A new component therefore cannot pass until its execution mapping is
  real.
- All existing skills declare a concrete, in-repo deterministic target (a
  `tools/` primitive, a gate, or the test suite).

This is the declaration-level mapping. The runtime `skill.v1` contract remains
roadmap; when it lands it will bind this field to a `component.v1` identity.

## Scope

- **In:** `.agents/skills/**` frontmatter, `tools/new-skill.sh`,
  `tests/test_agent_conventions.py`, `docs/agent/components.md`, this spec.
- **Out:** any `src/` or `contracts/` change (`skill.v1` stays roadmap);
  `PRINCIPLES.md` laws; `.agentfactory.toml` level; KERNEL invariants.

## Acceptance criteria

- [ ] Every discovered skill declares a non-placeholder `metadata.terminates-in`.
- [ ] A skill scaffolded by `tools/new-skill.sh` fails the guard until the
      placeholder is replaced.
- [ ] The guard, `ruff`, and `mypy src` are clean.

## Risks / invariants

Respects Laws 4 (progressive disclosure), 6 (auditability), 8 (no unverified
success), 11, 12, 13. It changes no `src/` semantics and no KERNEL invariant.
The field is a pointer to a deterministic component; it is descriptive and
enforced, not executed.
