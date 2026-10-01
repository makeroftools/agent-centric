# HANDOFF — Agent-centric (mission-critical system)

> **Read first:** [`AGENTS.md`](AGENTS.md) is the always-loaded table of
> contents, and [`PRINCIPLES.md`](PRINCIPLES.md) is the constitution. This file
> is the **current** session-continuity one-pager: it points, it does not
> restate.

**Prepared for a new model session.** Facts below are current as of the tip of
`main`; `main` is the only branch and is pushed to `origin`. The most recent work
is the SPEC-0009 FBP slices (named ports + deterministic Information-Packet flow);
see `git log -1` and *Where we are (delivered)*. Run the fresh-session checklist
before acting.

## Read first (in order)

1. [`AGENTS.md`](AGENTS.md) — table of contents + the hard laws.
2. [`PRINCIPLES.md`](PRINCIPLES.md) — the non-negotiable laws (1–13).
3. [`.agentfactory.toml`](.agentfactory.toml) — the active mode (**L1**; target L3, gated).
4. [`.agents/skills/`](.agents/skills) — the on-demand Agent Skills.
5. [`docs/agent/`](docs/agent/README.md) — progressive disclosure: architecture,
   levels, testing, verification, committing, components.
6. [`specs/`](specs) — SPEC-0002 (CBP target), SPEC-0007 (harness/shell +
   distribution), SPEC-0008 (shell design / n8n), SPEC-0009 (FBP/ABM
   conformance), SPEC-0010 (extensibility).

> **Background (optional, non-normative).** The external **literature** that
> informed the design (FBP, CPM, agent SDLC, dark factory) is indexed in
> [`docs/references/README.md`](docs/references/README.md); its PDFs are
> **local-only** (not in git) and non-normative. Reading it is **not required** —
> the laws and specs are authoritative.

## Current git state

- **Branch:** `main` — the CBP line. Working tree clean; in sync with
  `origin/main`.
- **Topology:** `main` is the only branch. The prior Manager line and the
  pre-convention FBP line were retired as redundant (0 commits unique to them);
  their history remains in `main`, and the Manager line's tip is marked by the
  annotated tag `v0.29.0-milestone`. The convention guard forbids the legacy
  `agent_centric.fbp` package and `fbp-*` gates from returning.
- **Tag:** `v0.29.0-milestone` (historical kernel milestone).
- **Push policy:** commit and push continuously, no permission needed (Law 13);
  never bypass hooks (`--no-verify` is forbidden).

## Fresh-session start

1. Read [`AGENTS.md`](AGENTS.md) → [`PRINCIPLES.md`](PRINCIPLES.md) →
   [`.agentfactory.toml`](.agentfactory.toml) (active mode **L1**; target L3, gated).
2. Confirm the tree: `git branch --show-current` → `main`; `git status` clean.
3. Baseline gates (fast, offline): `uv run ruff check .`, `uv run mypy src`,
   `uv run agent-centric cbp-check`.
4. Run the test suite as the level permits (Law 12); the operator/CI is the record.
5. Continue from **Next** below. Never act above the active level without the
   operator changing `.agentfactory.toml` first.

### Kickoff prompt (paste into a new session)

> Continue the mission-critical `agent-centric` system (CBP/ABM; deterministic,
> local-first, fail-closed). Read `AGENTS.md` -> `PRINCIPLES.md` ->
> `.agentfactory.toml` (active **L1**; target L3 gated) -> `HANDOFF.md`. Confirm
> `main` and a clean tree, then run `uv run ruff check .`, `uv run mypy src`,
> `uv run agent-centric cbp-check`, and the suite
> `uv run pytest -o addopts="" -p no:cacheprovider`. Continue from HANDOFF
> **Next**. Obey the hard laws: whole-file replacement only via
> `tools/safe-replace.sh` (never in-place edits), commit and push continuously,
> never `--no-verify`. Never act above L1 without the operator changing
> `.agentfactory.toml` first.

## What Agent-centric is

- **Two layers (SPEC-0007).** `agent-centric` is the **harness** — boot +
  runtime + meta. It **executes** the CBP tree and is **not** a node. The tree's
  root is the **shell component**, an ordinary component.
- **Everything is a component** (CBP node = ABM agent), self-contained with
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
    (`cbp/cache.py`, `cbp/resolver.py`, `cbp/signing.py`).
  - **Phase 1a** — deterministic bundle; offline git source + pinner; offline
    pin→lock→resolve→verify→load→run with an allowlisted entry; replay proof
    (`cbp/component_bundle.py`, `cbp/component_source.py`,
    `cbp/component_runtime.py`). Example component
    [`examples/components/counter/`](examples/components/counter) and demo
    [`examples/component_distribution.py`](examples/component_distribution.py).
  - **Phase 1b** — real example composite `bills_registry`: a SQLite **state
    descriptor** (`cbp/component_state.py`: path-safe, WAL, integrity-checked,
    atomic); both child modes — **contains** the embedded `bills_rules`
    component, **points-to** the referenced `agenda`; deterministic
    dependency-ordered graph resolution (`cbp/component_graph.py`), with cycles
    and absences failing closed. Demo
    [`examples/component_graph.py`](examples/component_graph.py); example
    [`examples/components/bills_registry/`](examples/components/bills_registry).
  - **Rebrand (operator-directed, early Phase 3 cut-over).** The active subsystem
    moved `agent_centric.fbp` → **`agent_centric.cbp`**; classes `Fbp*` → `Cbp*`;
    CLI `fbp-check`/`fbp-web`/… → `cbp-*`; tests `tests/test_cbp_*`; examples
    `examples/cbp_*.py`; docs `docs/cbp.md`. The acronym `FBP` now survives only
    as the **heritage credit** (SPEC-0002 §10, `docs/references/`).
  - **Anti-drift guard** — `tests/test_agent_conventions.py::TestNoStaleLabels`
    asserts the legacy `agent_centric.fbp` package and the retired `fbp-*` gates
    cannot return.
  - **Phase 2 (MVP) — boot from lock** (`cbp/component_boot.py`): resolve the
    graph children-first, gate on `lock_hash`, cross-check manifest↔lock
    conformance, materialize per-component namespaced SQLite state, and resolve
    allowlisted entries, failing closed at every step; example `shell` (root
    composite → `registry`) and `registry` components; demo
    `examples/component_boot.py`.
  - **Phase 2 (MVP) — process-isolated execution** (`cbp/component_process.py`,
    private `_component_runner.py`): a verified bundle's own entry runs in a child
    process (one JSON request/response, `python -I`, minimal env, hard timeout,
    output bound); opt-in via `boot_from_lock(..., allow_subprocess=True)` and
    fail-closed. Process isolation, **not** an OS sandbox — the signature +
    content hash remain the trust anchor. The committed umbrella
    `components.lock` still needs the live mirror.
  - **Phase 0.5b (MVP) — signing + transparency** (`cbp/signing_service.py`,
    `cbp/transparency.py`): machine-performed release signing with `gpg`/`minisign`
    (no private key in-process) and an append-only, hash-chained transparency log
    (tamper + truncation detection, signatures re-verified on read); demo
    `examples/release_signing.py`. The **live self-hosted mirror** remains.
  - **Shell design (SPEC-0008, Phase 1 MVP)** (`contracts/design.py`,
    `cbp/design.py`): a content-hashed `design.v1` (a `network.v1` + requested
    refs) with deterministic, fail-closed `validate_design`/`compile_design`
    (schema/cycle/edge/absence gates); demo `examples/design_compile.py`. The
    n8n adapter and the pin/record wiring remain.
  - **FBP/ABM conformance (SPEC-0009)** (`cbp/network.py`, `cbp/flow.py`):
    slice 1 — named **ports** + per-connection **capacity**, fail-closed
    validation, deterministic `content_hash()`, port-aware compilation. slice 2 —
    deterministic **Information-Packet flow**: verified outputs route
    outport→inport over `BoundedConnection`s (FIFO, capacity); overflow is
    back-pressure and an empty receive is a deadlock, both fail closed; `args`
    supply IIPs. Tests: `tests/test_cbp_ports.py`, `tests/test_cbp_flow.py`.
    Slice 3 (streaming, per-port IIP docs, external-ports boundary guard) remains.

## Validation (last full run)

- `uv run pytest -o addopts="" -p no:cacheprovider` → **1324 passed**.
- `uv run ruff check .` → clean.
- `uv run mypy src` → clean (**116** source files).
- `uv run agent-centric cbp-check` → **8/8**, READY.
- Convention guard → **40 passed**.

## Next (candidate — not started)

- **Phase 2 remainder:** commit the umbrella `components.lock` (blocked on the
  Phase 0.5b mirror — no real pinned remotes yet).
- **Shell design (SPEC-0008) remainder:** the n8n adapter (import/export +
  canonical round-trip, fixture-tested) and the pin/record wiring
  (design → signed lock → boot). Phase 1 (`design.v1` + validate/compile) is
  delivered.
- **Phase 0.5b remainder:** the live self-hosted mirror (Forgejo/Gitea); the
  signing service and transparency log are delivered.
- **FBP/ABM conformance (SPEC-0009):** slices 1–2 delivered (ports + bounded
  connections; deterministic IP flow with fail-closed back-pressure/deadlock).
  Slice 3 remains: repeated-activation streaming, first-class per-port IIP
  documents, and the composite external-ports boundary guard.
- **Extensibility (SPEC-0010, draft):** knowledge graphs (ontology as a versioned
  contract over a component-owned semantic graph; closed shapes for gates;
  pinned entailment closure) and bounded RSI (propose → review → sign → lock;
  verification off-limits until the isolated validator exists).
- **Phases 3–5:** directives aggregated into skills + pinned model components;
  migration + hermetic CI; hardening drills.

## Open threads (blockers & honest gaps)

- **Live self-hosted mirror** (Forgejo/Gitea) — needed for Phase 0.5b and for a
  committed umbrella `components.lock`; there are no real pinned remotes yet, so
  the lock stays generated, not committed.
- **SPEC-0008 remainder:** the n8n adapter (import/export + canonical round-trip,
  fixture-tested) and the pin/record wiring (design -> signed lock -> boot).
- **SPEC-0009 slice 3:** repeated-activation streaming (where back-pressure
  suspends a producer), first-class per-port IIP documents, and the composite
  **external-ports boundary guard** (needs a nested subnet body on the component
  contract).
- **SPEC-0010:** knowledge graphs and bounded RSI are target/roadmap; the isolated
  holdout validator (L3) is required before any self-improvement of verification.
- **Honest FBP note:** non-port networks still run on the legacy args model;
  port-declared networks use `run_flow`. Both are deterministic.

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

- `src/agent_centric/cbp/` — the active subsystem: `component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `component_state.py`,
  `component_graph.py`, `component_boot.py`, `component_process.py`
  (+ private `_component_runner.py`), `bills_component.py`, `shell_component.py`,
  `registry_component.py`, `signing_service.py`, `transparency.py`, `design.py`,
  `network.py`, `flow.py`, `cache.py`, `resolver.py`, `signing.py`.
- `src/agent_centric/contracts/` — versioned contracts, including `component.py`,
  `components_lock.py`, and `design.py` (`design.v1`).
- `specs/` — specs (SPEC-0002 target; SPEC-0007 distribution plan;
  SPEC-0008 shell design interface; SPEC-0009 FBP/ABM conformance;
  SPEC-0010 extensibility).
- `examples/components/` — `counter` (atomic), `bills_registry` (composite:
  SQLite state + embedded `bills_rules` + referenced `agenda`), and the Phase 2
  `shell` (root composite → `registry`) + `registry` components.
- `tests/test_agent_conventions.py` — convention + anti-drift guard;
  `tests/test_component_distribution.py`, `tests/test_component_state.py`,
  `tests/test_component_graph.py`, `tests/test_component_boot.py`,
  `tests/test_component_process.py`, `tests/test_release_signing.py`,
  `tests/test_design.py`, `tests/test_cbp_ports.py`, and `tests/test_cbp_flow.py`
  — the distribution + design + FBP-conformance slice.

## Non-goals (do not build without explicit direction)

See [`AGENTS.md`](AGENTS.md) → *Non-goals*. In short: no new agents/ACP/refactor/
dependency bumps; no auto-accept or unsupervised money; no send/delete/move
email; no cloud/network in CI; no changing Manager orchestration/verification/
policy/envelope/accounting semantics (prefer adapters/backends); never bypass
hooks or CI.

## Historical documents

[`STATUS.md`](STATUS.md), [`KERNEL.md`](KERNEL.md),
[`docs/CBP_HANDOFF.md`](docs/CBP_HANDOFF.md),
[`README_CBP.md`](README_CBP.md), and [`docs/DIRECTIVE.md`](docs/DIRECTIVE.md)
describe past states and are not updated retroactively.
