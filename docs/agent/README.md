# docs/agent — the convention map (human)

This directory is the **human** map of the agent convention layer. The
agent-facing, on-demand instructions are now **Agent Skills** under
[`.agents/skills/`](../../.agents/skills) — the canonical, industry-standard
form. They are mirrored for Claude via the [`.claude/skills`](../../.claude/skills)
symlink, and opencode discovers them through the `skill` tool.

| Skill | Use when |
| --- | --- |
| [`safe-file-editing`](../../.agents/skills/safe-file-editing/SKILL.md) | Changing any file (Law 11). |
| [`test-authority`](../../.agents/skills/test-authority/SKILL.md) | Validating work (Law 12). |
| [`commit-and-push`](../../.agents/skills/commit-and-push/SKILL.md) | Committing or pushing (Law 13). |
| [`operating-levels`](../../.agents/skills/operating-levels/SKILL.md) | You need the active mode. |
| [`verification-gates`](../../.agents/skills/verification-gates/SKILL.md) | Designing or judging a gate. |
| [`home-path-safety`](../../.agents/skills/home-path-safety/SKILL.md) | Any home path or username. |
| [`cbp-architecture`](../../.agents/skills/cbp-architecture/SKILL.md) | The execution architecture. |

## The constitution

[`PRINCIPLES.md`](../../PRINCIPLES.md) is supreme; [`AGENTS.md`](../../AGENTS.md)
is the always-loaded table of contents. The target architecture is
[`specs/SPEC-0002-cbp-component-architecture.md`](../../specs/SPEC-0002-cbp-component-architecture.md).

## Enforcement

Prose points; machines decide:

- [`opencode.json`](../../opencode.json) denies the `edit`/`write`/`patch` tools.
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) and
  [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml) run the gates.
- [`tests/test_agent_conventions.py`](../../tests/test_agent_conventions.py)
  validates the skills and this layer.

If this map ever contradicts [`PRINCIPLES.md`](../../PRINCIPLES.md), the laws win.
