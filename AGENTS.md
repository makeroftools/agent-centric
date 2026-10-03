# AGENTS.md — agent entry point

This file is the **table of contents** for any coding agent working in this
repository (opencode, Zed, Claude Code, Codex, Cursor, Gemini, …). It *points*;
it does not restate. The constitution is [`PRINCIPLES.md`](PRINCIPLES.md):
**if anything here disagrees with it, `PRINCIPLES.md` wins.**

## Read first

1. [`PRINCIPLES.md`](PRINCIPLES.md) — the non-negotiable laws.
2. [`.agentfactory.toml`](.agentfactory.toml) — the active operating level (mode).
3. [`.agents/skills/`](.agents/skills) — on-demand Agent Skills, one concern each
   (canonical, industry-standard `SKILL.md`).
4. [`src/agent_centric/cbp/spec.md`](src/agent_centric/cbp/spec.md),
   [`src/agent_centric/cbp/protocol.md`](src/agent_centric/cbp/protocol.md) — architecture contracts.
5. [`specs/SPEC-0002-cbp-component-architecture.md`](specs/SPEC-0002-cbp-component-architecture.md)
   — the target CBP component architecture (MVP + roadmap).
6. [`specs/SPEC-0007-harness-shell-component-distribution.md`](specs/SPEC-0007-harness-shell-component-distribution.md)
   — the harness/shell split and component distribution (target).

7. [`specs/SPEC-0023-ontology-semantic-graphs.md`](specs/SPEC-0023-ontology-semantic-graphs.md)
   — the ratified **ontology / semantic-graph** design (a SPEC-0010 refinement);
   its M1 kernel, the M2 entailment closure + semantic subnet, and the M3
   authored-fact store + one-way RDF export are delivered. Human summary:
   [`docs/agent/ontology.md`](docs/agent/ontology.md).

## Background (optional, non-normative)

What is here is **enough to work**. The external **literature** that informed the
design — Flow-Based Programming, the Critical Path Method, agent SDLC, and the
dark-factory essays — is indexed in
[`docs/references/README.md`](docs/references/README.md). Those PDFs are
**local-only** (not in git) and **non-normative**: the authoritative sources are
[`PRINCIPLES.md`](PRINCIPLES.md), [`specs/`](specs/), and
[`docs/agent/`](docs/agent/README.md). Reading the literature is **not required**
to continue as a coding agent.

## The hard laws you are judged on

- **Law 11 — no in-place file edits, ever.** Every change is a whole-file,
  atomic replace. In opencode the `edit`/`write` tools are denied by
  [`opencode.json`](opencode.json); use `./tools/safe-replace.sh <file>`
  (temp + `cp`, byte-complete). Full page:
  [`.agents/skills/safe-file-editing/SKILL.md`](.agents/skills/safe-file-editing/SKILL.md).
- **Law 12 — test authority.** The agent **may run any tests**; report results
  faithfully. The operator's run and CI remain the record of truth for a
  release. Skill: [`.agents/skills/test-authority/SKILL.md`](.agents/skills/test-authority/SKILL.md).
- **Law 13 — commit and push continuously.** Commit each coherent unit and
  push often; no permission is needed. Hooks and CI are the guardrails. Full
  skill: [`.agents/skills/commit-and-push/SKILL.md`](.agents/skills/commit-and-push/SKILL.md).

## Operating level (mode)

The active mode is `factory.level` in [`.agentfactory.toml`](.agentfactory.toml).
Levels: **L0** harness-only · **L1** assisted · **L2** light-factory ·
**L3** dark-factory (declared, gated). What each level authorizes — across the
axes *autonomy*, *gates*, *review* — is defined in
[`.agents/skills/operating-levels/SKILL.md`](.agents/skills/operating-levels/SKILL.md). Never act above the active level
without the operator changing it first.

## Home directory — never hand-type it

Resolve every home path from `$HOME`; never type the username. Skill:
[`.agents/skills/home-path-safety/SKILL.md`](.agents/skills/home-path-safety/SKILL.md);
directive: [`docs/HOME_DIRECTIVE_for_agents.md`](docs/HOME_DIRECTIVE_for_agents.md).

## Layout

- **Two layers:** `agent-centric` is the **harness** (boot + runtime; it
  *executes* the CBP tree and is **not** a node). The tree's root is the **shell
  component**, an ordinary component. Target:
  [`specs/SPEC-0007-harness-shell-component-distribution.md`](specs/SPEC-0007-harness-shell-component-distribution.md);
  human page: [`docs/agent/architecture.md`](docs/agent/architecture.md).
- `src/agent_centric/cbp/` — the active CBP subsystem (the tree of agents).
- `src/agent_centric/contracts/` — versioned contracts; **additive-only**.
- `tests/` — invariant tests; the operator or CI runs them, not the authoring agent.
- `docs/` — design/handoff docs; `docs/agent/` is the human convention map.
- `.agents/skills/` — the canonical, on-demand Agent Skills; a skill **is** a
  component (CBP). Add one with `./tools/new-skill.sh <name>`; the convention
  guard auto-discovers every skill. See `docs/agent/components.md`.
- `specs/` — spec files with YAML front-matter; `specs/holdout/` is never fed to the author.
- `examples/` — runnable demos; `examples/components/<name>/` are example component
  sources (SPEC-0007).
- `tools/` — the only sanctioned file-mutation primitives.

## Validation (static checks you may run)

```sh
uv run ruff check .
uv run mypy src
uv run agent-centric cbp-check        # deterministic readiness gate
```

Run the test suite as the active level permits (the agent may run any tests); see
[`.agents/skills/test-authority/SKILL.md`](.agents/skills/test-authority/SKILL.md).

**Workspace IDE hygiene (Zed/basedpyright).** Zed launches basedpyright with cwd
= the workspace root (`cbp/`), where it reads only the root `pyrightconfig.json`.
The canonical config and `check-pyright.sh` live in
[`tools/workspace/`](tools/workspace/README.md); run the check as part of every
verification and keep the tray at **0 errors, 0 warnings, 0 notes** (fix causes,
never suppress a real diagnostic).

## Branches

`main` is the CBP line (history of the former `clean` branch).
The prior Manager line is frozen in history, marked by tag `v0.29.0-milestone`.
`STATUS.md` records the volley-by-volley history and is not updated
retroactively. The legacy `agent_centric.fbp`
package and the retired `fbp-*` gates/branches are gone; `main` is the only
branch, and the convention guard (`tests/test_agent_conventions.py`)
forbids their return.

## Non-goals — do not build without explicit direction

- New agents, ACP features, refactors, or dependency bumps.
- Auto-accept / unsupervised filing or committing of money.
- SMTP / send / delete / move email; email→draft stays read-only.
- Cloud OCR, cloud APIs, or any network in the **component** CI. (SPEC-0018 §11
  narrows this: a separate private infra repo has its own CI that may use the
  network — lint/plan → build → sign → publish — and never runs in this repo's
  gates.)
- Changing Manager orchestration, verification, policy, envelope, or accounting
  semantics (prefer adapters/backends over core changes).
- Bypassing pre-commit hooks or CI (never `--no-verify`); fix the cause instead.
