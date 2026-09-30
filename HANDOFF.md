# HANDOFF — Agent-centric (mission-critical system)

> **Read first:** [`AGENTS.md`](AGENTS.md) is the always-loaded table of
> contents, and [`PRINCIPLES.md`](PRINCIPLES.md) is the constitution. This file
> is the **current** session-continuity one-pager: it points, it does not
> restate.

**Prepared for a new session thread.** All facts below are current as of
`HEAD = c64fcf2` (branch `main`, pushed to `origin`).

## Read first (in order)

1. [`AGENTS.md`](AGENTS.md) — table of contents + the hard laws.
2. [`PRINCIPLES.md`](PRINCIPLES.md) — the non-negotiable laws (1–13).
3. [`.agentfactory.toml`](.agentfactory.toml) — the active mode (**L1**; target L3, gated).
4. [`.agents/skills/`](.agents/skills) — the on-demand Agent Skills.
5. [`docs/agent/`](docs/agent/README.md) — progressive disclosure: architecture,
   levels, testing, verification, committing, components.
6. [`specs/SPEC-0002-cbp-component-architecture.md`](specs/SPEC-0002-cbp-component-architecture.md)
   (CBP target) and
   [`specs/SPEC-0007-harness-shell-component-distribution.md`](specs/SPEC-0007-harness-shell-component-distribution.md)
   (harness/shell + distribution).

> **Background (optional, non-normative).** The external **literature** that
> informed the design (FBP, CPM, agent SDLC, dark factory) is indexed in
> [`docs/references/README.md`](docs/references/README.md); its PDFs are
> **local-only** (not in git) and non-normative. Reading it is **not required** —
> the laws and specs are authoritative.

## Current git state

- **Branch:** `main` — the FBP line. Working tree clean; in sync with
  `origin/main`.
- **Topology:** `agent-manager-version` is the frozen prior Manager line;
  `archive/agent-centric-fbp` is the archived pre-convention line.
- **Tag:** `v0.29.0-milestone` (historical kernel milestone).
- **Push policy:** commit and push continuously, no permission needed (Law 13);
  never bypass hooks (`--no-verify` is forbidden).

## What Agent-centric is

- **Two layers (SPEC-0007).** `agent-centric` is the **harness** — boot +
  runtime + meta. It **executes** the CBP tree and is **not** a node. The tree's
  root is the **shell component**, an ordinary component.
- **Everything is a component** (FBP node = ABM agent), self-contained with
  local SQLite state; a **skill** is a component directive; an **LLM** is a
  component of kind `model`.
- **Posture:** deterministic control plane, local-first, fail-closed, no
  unverified success, full auditability.

## Where we are (delivered)

- **Convention layer** (SPEC-0001/0003/0004): `AGENTS.md` TOC, canonical skills
  (8, auto-discovered), scaffold [`tools/new-skill.sh`](tools/new-skill.sh),
  same-name component mapping (SPEC-0006), guarded by
  [`tests/test_agent_conventions.py`](tests/test_agent_conventions.py).
- **CBP target architecture:** SPEC-0002.
- **Distribution spine** (SPEC-0007):
  - **Phase 0** — spec; Law 5 amended (run-time local-first vs acquisition);
    `KERNEL.md` note; `docs/agent/architecture.md`.
  - **Phase 0.5a** — `component.v1` + `components.lock/v1` contracts;
    content-addressed atomic cache; deterministic resolver; minisign/gpg
    signature verification; offline directory source
    (`fbp/cache.py`, `fbp/resolver.py`, `fbp/signing.py`).
  - **Phase 1a** — deterministic bundle; offline git source + pinner; offline
    pin→lock→resolve→verify→load→run with an allowlisted entry; replay proof
    (`fbp/component_bundle.py`, `fbp/component_source.py`,
    `fbp/component_runtime.py`). Example component
    [`examples/components/counter/`](examples/components/counter) and demo
    [`examples/component_distribution.py`](examples/component_distribution.py).
  - **Phase 1b** — real example composite `bills_registry`: a SQLite **state
    descriptor** (`fbp/component_state.py`: path-safe, WAL, integrity-checked,
    atomic); both child modes — **contains** the embedded `bills_rules`
    component, **points-to** the referenced `agenda`; deterministic
    dependency-ordered graph resolution (`fbp/component_graph.py`), with cycles
    and absences failing closed. Demo
    [`examples/component_graph.py`](examples/component_graph.py); example
    [`examples/components/bills_registry/`](examples/components/bills_registry).

## Validation (last full run)

- `uv run pytest -p no:cacheprovider` → **1237 passed**.
- `uv run ruff check .` → clean.
- `uv run mypy src` → clean (**106** source files).
- Convention guard → passes.

## Next (candidate — not started)

- **Phase 0.5b:** automated signing service + append-only transparency log +
  live self-hosted mirror (needs the Forgejo/Gitea instance).
- **Phase 2:** registry component + shell component; boot from the lock.
- **Phases 3–5:** directives/models; migration + hermetic CI; hardening drills.

## How to work here (hard laws)

- **Law 11 — no in-place edits, ever.** Replace whole files via
  [`tools/safe-replace.sh`](tools/safe-replace.sh) `<file> < new-content`
  (`opencode.json` denies `edit`/`write`/`patch`). See
  [`docs/agent/components.md`](docs/agent/components.md) and the
  `safe-file-editing` skill.
- **Law 12 — test authority.** The agent may run any tests; report results
  faithfully; the operator's run and CI remain the record of truth.
- **Law 13 — commit and push continuously.** Pre-commit hooks and CI are the
  guardrails; never `--no-verify`.

See [`docs/agent/levels.md`](docs/agent/levels.md),
[`docs/agent/verification.md`](docs/agent/verification.md), and
[`docs/agent/committing.md`](docs/agent/committing.md).

## Key files

- `src/agent_centric/fbp/` — the active subsystem: `component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `cache.py`, `resolver.py`,
  `signing.py`.
- `src/agent_centric/contracts/` — versioned contracts, including
  `component.py` and `components_lock.py`.
- `specs/` — specs (SPEC-0002 target; SPEC-0007 distribution plan).
- `examples/components/counter/` — example component source.
- `tests/test_agent_conventions.py` — convention guard;
  `tests/test_component_distribution.py` — the distribution slice.

## Non-goals (do not build without explicit direction)

See [`AGENTS.md`](AGENTS.md) → *Non-goals*. In short: no new agents/ACP/refactor/
dependency bumps; no auto-accept or unsupervised money; no send/delete/move
email; no cloud/network in CI; no changing Manager orchestration/verification/
policy/envelope/accounting semantics (prefer adapters/backends); never bypass
hooks or CI.

## Historical documents

[`STATUS.md`](STATUS.md), [`KERNEL.md`](KERNEL.md),
[`docs/FBP_HANDOFF.md`](docs/FBP_HANDOFF.md),
[`README_FBP.md`](README_FBP.md), and [`docs/DIRECTIVE.md`](docs/DIRECTIVE.md)
describe past states and are not updated retroactively.
