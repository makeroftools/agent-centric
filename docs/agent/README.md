# docs/agent — the convention map (human)

This directory is the **human** map of the agent convention layer. The
agent-facing, on-demand instructions are **Agent Skills** under
[`.agents/skills/`](../../.agents/skills) — the canonical, industry-standard
form. A skill **is a component** (CBP); opencode discovers them directly, and
they are mirrored for Claude via the [`.claude/skills`](../../.claude/skills)
symlink. The guard auto-discovers every skill, so adding components needs no
registration here beyond the map below.

| Skill | Use when |
| --- | --- |
| [`safe-file-editing`](../../.agents/skills/safe-file-editing/SKILL.md) | Changing any file (Law 11). |
| [`test-authority`](../../.agents/skills/test-authority/SKILL.md) | Validating work (Law 12). |
| [`commit-and-push`](../../.agents/skills/commit-and-push/SKILL.md) | Committing or pushing (Law 13). |
| [`operating-levels`](../../.agents/skills/operating-levels/SKILL.md) | You need the active mode. |
| [`verification-gates`](../../.agents/skills/verification-gates/SKILL.md) | Designing or judging a gate. |
| [`home-path-safety`](../../.agents/skills/home-path-safety/SKILL.md) | Any home path or username. |
| [`cbp-architecture`](../../.agents/skills/cbp-architecture/SKILL.md) | The execution architecture. |
| [`skill-authoring`](../../.agents/skills/skill-authoring/SKILL.md) | Adding or changing a component/skill. |

## Pages (progressive disclosure)

The always-loaded table of contents is [`AGENTS.md`](../../AGENTS.md); detail is
revealed here, only as needed:

| Page | What it covers |
| --- | --- |
| [`levels.md`](levels.md) | Operating levels — the active mode and its three axes. |
| [`testing.md`](testing.md) | Test authority (Law 12). |
| [`verification.md`](verification.md) | Verification gates and the holdout validator. |
| [`committing.md`](committing.md) | Commit and push (Law 13). |
| [`components.md`](components.md) | Adding a component (a skill); the scaffold and the contract. |
| [`architecture.md`](architecture.md) | Harness vs tree; components contain or point to children; distribution boundaries. |
| [`l3-milestone-plan.md`](l3-milestone-plan.md) | The L3 milestone plan — implemented agent-side; operator gates remain. |
| [`component-adapter.md`](component-adapter.md) | The Agent→Component adapter, the parent-provisioned catalog, and the removed globals (current architecture). |
| [`spec-0002-phase1-adapter-design.md`](spec-0002-phase1-adapter-design.md) | SPEC-0002 Phase-1 remainder — adapter-first design + implementation progress record. |

## The constitution

[`PRINCIPLES.md`](../../PRINCIPLES.md) is supreme; [`AGENTS.md`](../../AGENTS.md)
is the always-loaded table of contents. The target architecture is
[`specs/SPEC-0002-cbp-component-architecture.md`](../../specs/SPEC-0002-cbp-component-architecture.md).

## Background (non-normative)

The external **literature** that informed the design — Flow-Based Programming,
the Critical Path Method, agent SDLC, and the dark-factory essays — is indexed in
[`docs/references/README.md`](../references/README.md). It is **non-normative**
and its PDFs are **local-only**; reading it is optional. The authoritative
sources are [`PRINCIPLES.md`](../../PRINCIPLES.md) and [`specs/`](../../specs).

## Enforcement

Prose points; machines decide:

- [`opencode.json`](../../opencode.json) denies the `edit`/`write`/`patch` tools.
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) and
  [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml) run the gates.
- [`tests/test_agent_conventions.py`](../../tests/test_agent_conventions.py)
  auto-discovers every skill/component and validates the skills and this layer.

If this map ever contradicts [`PRINCIPLES.md`](../../PRINCIPLES.md), the laws win.
