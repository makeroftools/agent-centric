---
name: skill-authoring
description: Use when adding or changing a component/skill. Every component lives at .agents/skills/<name>/SKILL.md; scaffold it with tools/new-skill.sh, and the convention guard auto-discovers it. A skill is a component (SPEC-0002).
license: MPL-2.0
compatibility: opencode
metadata:
  component: skill-authoring
  determinism: deterministic
---

# Authoring a component (skill)

In CBP a **skill is a component**; every component has exactly one canonical
form and one home:

```
.agents/skills/<name>/SKILL.md
```

There is **no allowlist**: `tests/test_agent_conventions.py` auto-discovers
every skill under `.agents/skills/`, so a new component that does not follow the
convention fails the guard (pre-commit and CI). Name the directory after the
skill; opencode discovers it directly, and the
[`.claude/skills`](../../../.claude/skills) symlink exposes it to Claude.

## Add a component

1. Scaffold it (never hand-write the skeleton):
   ```sh
   ./tools/new-skill.sh <name> [deterministic|suspect]
   ```
2. Fill in the placeholders; keep `metadata.component == <name>` and the two
   required sections.
3. Validate:
   ```sh
   uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py
   ```

## The contract (hard-wired by the guard)

- `name` — lowercase-hyphen, 1–64 chars, equals the directory name.
- `description` — 1–1024 chars, specific enough to choose correctly.
- `metadata.component` — equals the skill name (the CBP equivalence).
- `metadata.determinism` — `deterministic` or `suspect`.
- `## Deterministic termination` — the command/gate the component ends in.
- `## Non-determinism (suspect)` — what is suspect and how it is contained.

A suspect component must terminate in a call to a deterministic process (a
command, a gate, or another component) — Law 8: no unverified success.

## Deterministic termination

The scaffold [`tools/new-skill.sh`](../../../tools/new-skill.sh) and the guard
`uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py` are
deterministic; the guard decides compliance.

## Non-determinism (suspect)

None — this component is deterministic. Authoring prose is a skill act, but its
accepted output is fixed by the scaffold and checked by the guard.
