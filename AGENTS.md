# AGENTS.md — agent entry point

This file is the **table of contents** for any coding agent working in this
repository (opencode, Zed, Claude Code, Codex, Cursor, Gemini, …). It *points*;
it does not restate. The constitution is [`PRINCIPLES.md`](PRINCIPLES.md):
**if anything here disagrees with it, `PRINCIPLES.md` wins.**

## Read first

1. [`PRINCIPLES.md`](PRINCIPLES.md) — the non-negotiable laws.
2. [`.agentfactory.toml`](.agentfactory.toml) — the active operating level (mode).
3. [`docs/agent/`](docs/agent/README.md) — progressive disclosure, one page per concern.
4. [`KERNEL.md`](KERNEL.md), [`src/agent_centric/fbp/spec.md`](src/agent_centric/fbp/spec.md),
   [`src/agent_centric/fbp/protocol.md`](src/agent_centric/fbp/protocol.md) — architecture contracts.
5. [`specs/SPEC-0002-cbp-component-architecture.md`](specs/SPEC-0002-cbp-component-architecture.md)
   — the target CBP component architecture (MVP + roadmap).

## The hard laws you are judged on

- **Law 11 — no in-place file edits, ever.** Every change is a whole-file,
  atomic replace. In opencode the `edit`/`write` tools are denied by
  [`opencode.json`](opencode.json); use `./tools/safe-replace.sh <file>`
  (temp + `cp`, byte-complete). Full page:
  [`docs/agent/editing.md`](docs/agent/editing.md).
- **Law 12 — test authority.** The agent **may run any tests**; report results
  faithfully. The operator's run and CI remain the record of truth for a
  release. Full page: [`docs/agent/testing.md`](docs/agent/testing.md).
- **Law 13 — commit and push continuously.** Commit each coherent unit and
  push often; no permission is needed. Hooks and CI are the guardrails. Full
  page: [`docs/agent/committing.md`](docs/agent/committing.md).

## Operating level (mode)

The active mode is `factory.level` in [`.agentfactory.toml`](.agentfactory.toml).
Levels: **L0** harness-only · **L1** assisted · **L2** light-factory ·
**L3** dark-factory (declared, gated). What each level authorizes — across the
axes *autonomy*, *gates*, *review* — is defined in
[`docs/agent/levels.md`](docs/agent/levels.md). Never act above the active level
without the operator changing it first.

## Home directory — never hand-type it

Resolve every home path from `$HOME`; never type the username.
[`docs/HOME_DIRECTIVE_for_agents.md`](docs/HOME_DIRECTIVE_for_agents.md).

## Layout

- `src/agent_centric/fbp/` — the active FBP subsystem (the tree of agents).
- `src/agent_centric/contracts/` — versioned contracts; **additive-only**.
- `tests/` — invariant tests; the operator or CI runs them, not the authoring agent.
- `docs/` — design/handoff docs, plus `docs/agent/` (this convention layer).
- `specs/` — spec files with YAML front-matter; `specs/holdout/` is never fed to the author.
- `tools/` — the only sanctioned file-mutation primitives.

## Validation (static checks you may run)

```sh
uv run ruff check .
uv run mypy src
uv run agent-centric fbp-check        # deterministic readiness gate
```

Run the test suite only as the active level permits — see
[`docs/agent/testing.md`](docs/agent/testing.md).

## Branches

`main` is the FBP line (history of the former `clean` branch).
`agent-manager-version` is the frozen Manager line. `archive/agent-centric-fbp`
is the archived pre-convention line. Historical documents (e.g. `STATUS.md`,
`docs/FBP_HANDOFF.md`) describe past states and are not updated retroactively.

## Non-goals — do not build without explicit direction

- New agents, ACP features, refactors, or dependency bumps.
- Auto-accept / unsupervised filing or committing of money.
- SMTP / send / delete / move email; email→draft stays read-only.
- Cloud OCR, cloud APIs, or any network in CI.
- Changing Manager orchestration, verification, policy, envelope, or accounting
  semantics (prefer adapters/backends over core changes).
- Bypassing pre-commit hooks or CI (never `--no-verify`); fix the cause instead.
