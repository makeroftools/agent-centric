# HANDOFF — Agent-centric (mission-critical system)

> **Read first:** [`AGENTS.md`](AGENTS.md) is the always-loaded table of
> contents, and [`PRINCIPLES.md`](PRINCIPLES.md) is the constitution. This file
> is the **current** session-continuity one-pager: it points, it does not
> restate.

**Prepared for a new model session.** Facts below are current as of the tip of
`main`; `main` is the only branch and is pushed to `origin`. The most recent work
is the **MVP planning session** that froze the **full-version plan** and wrote
specs **SPEC-0012–SPEC-0017** (component ABI, cross-runtime conformance, network
trust, provenance/Assurance Labels, editions, parked frontier). The immediate next
step is to **start implementation at Layer 0: freeze the component ABI and the
conformance vectors**. Run the fresh-session checklist before acting.

## Read first (in order)

1. [`AGENTS.md`](AGENTS.md) — table of contents + the hard laws.
2. [`PRINCIPLES.md`](PRINCIPLES.md) — the non-negotiable laws (1–13).
3. [`.agentfactory.toml`](.agentfactory.toml) — the active mode (**L1**; target L3, gated).
4. [`.agents/skills/`](.agents/skills) — the on-demand Agent Skills.
5. [`docs/agent/`](docs/agent/README.md) — progressive disclosure: architecture,
   levels, testing, verification, committing, components.
6. [`specs/`](specs) — the frozen plan of record:
   - **SPEC-0011** (MVP definition; **accepted**) — the session outcome.
   - **SPEC-0012** — Component ABI v1.
   - **SPEC-0013** — cross-runtime realization + conformance vectors.
   - **SPEC-0014** — network execution and trust.
   - **SPEC-0015** — provenance, trust gates, Assurance Labels.
   - **SPEC-0016** — editions, distribution, cloud.
   - **SPEC-0017** — parked frontier workstreams.
   - Plus SPEC-0002 (CBP target), SPEC-0007 (harness/shell + distribution),
     SPEC-0008 (shell design / n8n), SPEC-0009 (FBP/ABM conformance),
     SPEC-0010 (extensibility).

> **Background (optional, non-normative).** The external **literature** that
> informed the design (FBP, CPM, agent SDLC, dark factory) is indexed in
> [`docs/references/README.md`](docs/references/README.md); its PDFs are
> **local-only** (not in git) and non-normative. Reading it is **not required** —
> the laws and specs are authoritative.

## Current git state

- **Branch:** `main` — the CBP line. In sync with `origin/main`.
- **Topology:** `main` is the only branch. The previous lines were retired; the
  Manager line's tip is marked by the annotated tag `v0.29.0-milestone`. The
  convention guard forbids the legacy `agent_centric.fbp` package and `fbp-*`
  gates from returning.
- **Push policy:** commit and push continuously, no permission needed (Law 13);
  never bypass hooks (`--no-verify` is forbidden).
- **Environment note (this machine).** Global `git` has `commit.gpgsign=true`
  (SSH signing) and `~/.ssh/config` points `github.com` at a *public* key. If the
  Bitwarden SSH agent is unavailable, commits fail at signing and pushes fail
  (`bad permissions`). Commit with a per-command `-c commit.gpgsign=false` (never
  `--no-verify`) and push with
  `git -c core.sshCommand="ssh -o IdentityAgent=$SSH_AUTH_SOCK -o IdentitiesOnly=no"`.
  Do **not** edit global config or keys.

## Fresh-session start

1. Read [`AGENTS.md`](AGENTS.md) → [`PRINCIPLES.md`](PRINCIPLES.md) →
   [`.agentfactory.toml`](.agentfactory.toml) (active mode **L1**; target L3, gated).
2. Confirm the tree: `git branch --show-current` → `main`; `git status` clean.
3. Baseline gates (after a one-time `uv sync --extra dev`): `uv run ruff check .`, `uv run mypy src`,
   `uv run agent-centric cbp-check`.
4. Run the test suite as the level permits (Law 12); the operator/CI is the record.
5. Continue from **Next** below. Never act above the active level without the
   operator changing `.agentfactory.toml` first.

### Kickoff prompt (paste into a new session)

> Continue the mission-critical `agent-centric` system (CBP/ABM; deterministic,
> local-first, fail-closed). Read `AGENTS.md` -> `PRINCIPLES.md` ->
> `.agentfactory.toml` (active **L1**; target L3 gated) -> `HANDOFF.md`, then the
> frozen plan `specs/SPEC-0011` through `specs/SPEC-0017`. Confirm `main` and a
> clean tree, run `uv sync --extra dev` once, then run `uv run ruff check .`, `uv run mypy src`,
> `uv run agent-centric cbp-check`, and the suite
> `uv run pytest -o addopts="" -p no:cacheprovider`. Begin **Layer 0** of the
> full-version plan: freeze the component ABI (SPEC-0012) and the shared
> conformance vectors (SPEC-0013). Obey the hard laws: whole-file replacement only
> via `tools/safe-replace.sh` (never in-place edits), commit and push
> continuously, never `--no-verify`. Never act above L1 without the operator
> changing `.agentfactory.toml` first.

## What Agent-centric is

- **Two layers (SPEC-0007).** `agent-centric` is the **harness** — boot +
  runtime + meta. It **executes** the CBP tree and is **not** a node. The tree's
  root is the **shell component**, an ordinary component.
- **Everything is a component** (CBP node = ABM agent). In the full version a
  component is an **abstract, blank object**: `init`/`run`/`kill` over a **ZeroMQ**
  event loop, a **typed WIT contract**, and **two-level identity** (abstract =
  contract hash; task = signed content hash).
- **Posture:** deterministic control plane, local-first, fail-closed, no
  unverified success, full auditability.

## The frozen plan (full version — SPEC-0011 accepted)

The planning session fixed the target as the **full version**, not a crippled
subset. The Core public repo is a **Python reflection** that helps build it.

- **SPEC-0012 Component ABI v1.** `init(boot_config)` / `run(event_loop)` /
  `kill()`. Transport **ZeroMQ**. All channels/ports and tasks are announced over
  the **initial hard-coded channels**. Identity is **never the ports**; at rest a
  component has **no edges**. Registry sources are open (git repo, directory,
  binary, …).
- **SPEC-0013 Cross-runtime realization + conformance vectors.** Two paths:
  **translation/compilation** (canonically WASM) and **native-runtime invocation**
  (language-server protocol; runtime **dialed up / JIT-provisioned**). **Equivalence
  is proven by shared conformance vectors, never by Turing completeness**; a
  runtime is **TCB** and is version-pinned, signed, and recorded.
- **SPEC-0014 Network execution and trust.** The network is **orthogonal to
  components**; static or **generated**; **execution only runs a content-addressed
  network**; a generated network is **pinned before running**. Goal: deterministic
  autonomy where cleanly/safely possible.
- **SPEC-0015 Provenance, trust gates, Assurance Labels.** Classes: **static**
  (launch), **appointed** (web/RAG; optional), **generated/compiled/translated**
  (gated), **discovered** (internet index; gated). "Safe" is a **scoped,
  evidence-backed label** (tiers **A0–A4**), never absolute; promotion past A0/A1
  is human-gated until the L3 isolated validator exists.
- **SPEC-0016 Editions, distribution, cloud.** **Core** (public Python reflection,
  headless) / **Pro** (private, optimization/Rust) / **Enterprise** (private
  components) / **cloud** (deployment adapter + manifest filtering). **Edition =
  a manifest selecting components.** Invariant: **one verified semantics**; tiers
  differ only in capacity/performance/deployment/governance/support/UI.
- **SPEC-0017 Parked frontier.** Formal semantics/ontology/closed shapes;
  reduction/analysis/formal verification (Z3/SMT → Assurance Labels); Universal
  Function Index/dynamic discovery; isolated validator (L3); bounded RSI;
  whitepaper (last).

**Two-week execution layers (target; every layer fallbacked).**

| Layer | Target | Fallback |
| --- | --- | --- |
| 0 | Freeze **ABI + conformance vectors** | must not slip |
| 1 | **Rust host** runs signed **WASM** components | **Python host**, same ABI |
| 2 | **Content-addressed network** + typed enforcement + replayable trajectory | static `network.v1` |
| 3 | **Read-only web diagram** from the network document | static JSON/image |
| 4 | **Appointed** components (allowlisted) | static-only |

Launch provenances: **static**, **A0 sandboxed**. Gated: generated/translated/discovered.
Language-server native-runtime invocation is Layer 4+ unless a launch-critical
component cannot target WASM.

**Process rule:** on complexity/blockage, **step back one or two specs to
resurrect intent** before forcing a workaround.

**Open design items (deferred with intent).** (1) State model: parent-held
environmental state for children vs component-sovereign local state. (2) The
blank-component → dynamic task load protocol (bind timing), hardened at Layer 2.

## Where we are (delivered)

- **Convention layer** (SPEC-0001/0003/0004): `AGENTS.md` TOC, canonical skills
  (auto-discovered), scaffold `tools/new-skill.sh`, same-name component mapping,
  guarded by `tests/test_agent_conventions.py`.
- **Distribution spine** (SPEC-0007): `component.v1` + `components.lock/v1`
  contracts; content-addressed cache; deterministic resolver; minisign/gpg
  verification; offline directory + git sources; deterministic bundle; boot from
  lock; process isolation; signing service + transparency log.
- **Shell design** (SPEC-0008): `design.v1` + `validate_design`/`compile_design`.
- **FBP/ABM conformance** (SPEC-0009): named/typed ports, Information-Packet
  flow with fail-closed back-pressure and deadlock, per-port IIP documents,
  composite external-ports boundary guard, repeated-activation streaming,
  per-component state sovereignty, a component at rest has no edges. **8/9**
  built; the remaining item is recorded/confidence-scored `model` components.
- **Docs / presentation:** README showcase; historical docs removed; `STATUS.md`
  retained as volley history.

## Validation (docs-only commit)

- `uv run ruff check .` → clean.
- `uv run mypy src` → clean (**117** source files).
- `uv run agent-centric cbp-check` → **8/8**, READY.
- Convention guard → **39 passed**.
- The full suite was **not** re-run for the docs-only spec commit; the last full
  run on record was **1400 passed** (10 distribution tests need the operator's
  global `commit.gpgsign` agent and pass under `GIT_CONFIG_GLOBAL=/dev/null`).

## Spec status audit

Spec `status` is `draft | accepted | implemented`; `accepted` means the **design
is approved**, not built.

- **SPEC-0011** `accepted` — MVP definition frozen; checkboxes for testable
  acceptance and the status-record correction remain.
- **SPEC-0012–0017** `draft` — the frozen full-version plan; nothing built.
- **SPEC-0002** `accepted` — Phase 1 not built (**0/6**): `review.v1` absent; no
  `Agent`→`Component` adapter; `_REGISTRY`/`_child_class_for` remain; no holdout
  scenarios; no response `confidence`/Review; no `inproc`-only backend.
- **SPEC-0003 / 0004 / 0006** `implemented` — criteria met by the convention
  guard; some checkboxes are un-ticked (doc-only correction).
- **SPEC-0007** `accepted` — Phases 0/0.5a/1a/1b/2 delivered; 0.5b signing
  delivered, **live mirror open**; no committed umbrella `components.lock`.
- **SPEC-0008** `draft` — `design.v1` + validate/compile delivered; n8n adapter,
  design→commit pinning, envelope limits open.
- **SPEC-0009** `draft` — **8/9** built; only recorded/confidence-scored `model`
  components open. Status understates it.
- **SPEC-0010** `draft` — roadmap only.

## Next (candidate — not started)

- **Immediate: Layer 0** — freeze the **component ABI** (SPEC-0012) and the
  **shared conformance vectors** (SPEC-0013). Everything else depends on these.
- **Layer 1** — Rust host running signed WASM components (fallback: Python host,
  same ABI).
- **Layer 2** — content-addressed network execution, typed-contract enforcement,
  replayable trajectory.
- **Layer 3** — read-only web diagram from the network document.
- **Distribution remainder:** wire the **lock-level signature into
  `boot_from_lock`** and add the **`design/network → pin → components.lock`
  producer**; commit the umbrella `components.lock` (still needs the live mirror).
- **SPEC-0008 remainder:** n8n adapter (import/export + canonical round-trip,
  fixture-tested) and pin/record wiring.
- **SPEC-0009 remainder:** recorded/confidence-scored `model` components.
- **SPEC-0017 frontier:** formal semantics/ontology/closed shapes; reduction +
  formal verification (Z3/SMT); Universal Function Index; isolated validator (L3);
  bounded RSI; whitepaper (last).

## Open threads (blockers & honest gaps)

- **Live self-hosted mirror** (Forgejo/Gitea) — needed for a committed umbrella
  `components.lock`; there are no real pinned remotes yet.
- **Integration spine** — the individual offline primitives exist, but
  `design/network → pin → signed lock → boot → run typed flow → replay` is not yet
  wired end-to-end; the lock-level signature is not consumed at boot.
- **`review.v1` / `confidence`** — absent; parked under the formal-semantics
  session (SPEC-0017). Launch ships a minimal deterministic scorer + a
  deterministic formalizer at the model boundary.
- **Honest FBP note:** non-port networks still run on the legacy args model;
  port-declared networks use `run_flow`. Both are deterministic.

## How to work here (hard laws)

- **Law 11 — no in-place edits, ever.** Replace whole files via
  [`tools/safe-replace.sh`](tools/safe-replace.sh) (`opencode.json` denies
  `edit`/`write`/`patch`). See the `safe-file-editing` skill.
- **Law 12 — test authority.** The agent may run any tests; report results
  faithfully; the operator's run and CI remain the record of truth.
- **Law 13 — commit and push continuously.** Pre-commit hooks and CI are the
  guardrails; never `--no-verify`.

See [`docs/agent/levels.md`](docs/agent/levels.md),
[`docs/agent/verification.md`](docs/agent/verification.md), and
[`docs/agent/committing.md`](docs/agent/committing.md).

## Key files

- `src/agent_centric/cbp/` — the active subsystem (`component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `component_state.py`,
  `component_graph.py`, `component_boot.py`, `component_process.py`,
  `signing_service.py`, `transparency.py`, `design.py`, `network.py`, `flow.py`,
  `cache.py`, `resolver.py`, `signing.py`).
- `src/agent_centric/contracts/` — versioned contracts, incl. `component.py`,
  `components_lock.py`, `design.py`.
- `specs/SPEC-0011`–`SPEC-0017` — the frozen full-version plan of record.
- `examples/components/` — `counter`, `bills_registry`, `shell` + `registry`.
- `tests/test_agent_conventions.py` — convention + anti-drift guard.

## Non-goals (do not build without explicit direction)

See [`AGENTS.md`](AGENTS.md) → *Non-goals*. In short: no new agents/ACP/refactor/
dependency bumps; no auto-accept or unsupervised money; no send/delete/move
email; no cloud/network in CI; no changing Manager orchestration/verification/
policy/envelope/accounting semantics (prefer adapters/backends); never bypass
hooks or CI; discovery/generation is never auto-trusted (SPEC-0015 gates).

## Historical documents

[`STATUS.md`](STATUS.md) records the volley-by-volley history and is not updated
retroactively.
