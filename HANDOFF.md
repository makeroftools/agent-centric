# HANDOFF — Agent-centric (mission-critical system)

> **Read first:** [`AGENTS.md`](AGENTS.md) is the always-loaded table of
> contents; [`PRINCIPLES.md`](PRINCIPLES.md) is the constitution. This file is
> the **current** session-continuity one-pager: it points, it does not restate.

**Prepared for a new model session. Every claim below was verified with the
commands in §"Verify everything" — that block is authoritative; commit hashes in
prose drift as work continues.** The workspace root is `cbp/` (a plain directory, **not** a
git repo); the work lives in three sibling repos:

| repo | visibility | role |
| --- | --- | --- |
| [`core/`](AGENTS.md) | public | Python **reflection** + the frozen specs (`specs/SPEC-0011`–`0017`). |
| [`../conformance/`](../conformance/AGENTS.md) | public | The shared contract: WIT ABI + conformance vectors + certifier. |
| [`../pro/`](../pro/AGENTS.md) | private | The Rust host (optimization edition). |

The most recent session delivered **Layers 0, 1a, 1b, 1c, signing, and **Layer 2**
network.v1**, of the full-version plan (`SPEC-0011`): the frozen component ABI
(`component-abi.v1`) and v1 conformance vectors; a **Rust host certified
cross-runtime-identical** to the Python reference host (shared suite **31/31**);
**content-addressed, signed WASM execution** (WASM suite **10/10**), including a
full **`component-abi.v1` WASM guest** that announces its channels/tasks and
exchanges Information Packets over the host-mediated transport, and locked-down
refusal of unsigned/bad-signature artifacts. **Layer 2 `network.v1` is
delivered** (static + deterministic-planner: pinned before it runs, typed at
instantiation, replayable; network suite **14/14** on both hosts). **Next is
Layer 3** (§Next).

## Verify everything (do this before acting)

```sh
# core (Python reflection) ------------------------------------------------
cd core
uv sync --extra dev                                   # one-time
uv run ruff check .                                   # -> clean
uv run mypy src                                       # -> 117 files, clean
uv run agent-centric cbp-check                        # -> READY (8/8)
uv run pytest -o addopts="" -p no:cacheprovider       # -> 1400 passed

# conformance (shared contract) -------------------------------------------
cd ../conformance
python3 certifier/certify.py                          # Python reference: 31/31
python3 certifier/certify.py --host-cmd "python3 certifier/reference_host.py"  # ext protocol: 31/31
#   (the WASM suite is certified by the Rust host, below)

# pro (Rust host) ---------------------------------------------------------
cd ../pro
./scripts/certify.sh                                  # builds + certifies BOTH suites (31/31 + 10/10) + unit tests
cargo test --release --features wasm                  # codec + artifact-signature tests: 5 passed
```

Certifier exit codes: `0` certified · `1` a case failed / nondeterministic ·
`2` suite, lock, or contract drifted (refused **before** running).

## Verified state (tips)

- Tips at verification time — `core` **e931a0d** · `conformance` **77e6684** ·
  `pro` **0546245** — all clean, all pushed to `origin/main`. **These advance as
  work continues: re-run the verification block rather than trusting the hashes.**
- Core gates: `ruff` clean; `mypy` clean (117 files); `cbp-check` **READY**;
  convention + home-path guard passed; full suite **1400 passed** (~63 s).
- Conformance: shared ABI suite **31/31** on the Python reference host (both
  in-process and over the external protocol) **and** the Rust host; WASM
  execution suite **10/10** on the Rust host (a narrow task fixture, a full
  `component-abi.v1` guest, and detached Ed25519 artifact-signing accept/refuse).
- Network (**Layer 2**): `network.v1` freezes a component network that is
  **content-addressed (pinned) before it runs**, type-checks every edge at
  instantiation, and records a deterministic, replayable trajectory that
  includes the pin. A `planner`-provenanced (generated) network is re-verified
  before execution: the deterministic planner is re-run on the pinned inputs and
  must reproduce the pin byte-identically (`plan-mismatch` otherwise). Network
  suite **14/14** on **both** the Python reference host and the Rust host
  (cross-runtime equivalence).
- Diagram (**Layer 3, static**): `cbp/diagram.py` projects a validated network
  into a deterministic, content-addressed `cbp.diagram.v1` document (longest-path
  ranks; nodes/edges sorted) and renders it to XML-escaped, read-only SVG/HTML.
  Served read-only at `/network/diagram/<name>`. Pure and offline (**14 tests**).
- ABI content address: `component-abi.v1` **revision 3**, `source_sha256`
  `d8e0325d53789d1af631bae7638a5b8947606a2da00698d40452471316a3386e`
  (see `../conformance/contracts/ABI.lock.v1.json`, which also hashes
  `ABI.md` via `abi_md_sha256`). Revision 3 adds the additive data-plane
  transport (`transport.send-on` / `receive-on`).
- WASM fixture artifacts: `../conformance/artifacts/identity.wasm` (task world),
  sha256 `7f798bfd0268fc18cfc19f631de883d010f2cb2514930602a06f4c19f161246e`; and
  `../conformance/artifacts/abi-identity.wasm` (`component-abi.v1` guest),
  sha256 `8ee5c8a4a68310c0be0a5b5fa9f9c4dabeb086f133df32be4aa5d9506033a157`.

## Pinned toolchain / TCB

- Rust **1.97.0** (`pro/rust-toolchain.toml`); `serde_json` **1.0.151**,
  `sha2` **0.10.9**, `wasmtime` **49.0.1** (optional, feature `wasm`),
  `ed25519-dalek` **2.2.0** (optional, feature `wasm`, artifact signing), pinned
  in `pro/Cargo.lock`.
- ABI resolver `wasm-tools` **1.260.0** (in `ABI.lock.v1.json`); guest builder
  `cargo-component` **0.21.1** (fixture uses `wit-bindgen-rt` 0.44.0).
- `pro` deps and the fixture are reproducible: `Cargo.lock` is **committed** in
  both.

## Read first (in order)

1. This file, then [`AGENTS.md`](AGENTS.md) → [`PRINCIPLES.md`](PRINCIPLES.md) →
   [`.agentfactory.toml`](.agentfactory.toml) (active mode **L1**; target L3, gated).
2. [`.agents/skills/`](.agents/skills) — on-demand Agent Skills.
3. [`docs/agent/`](docs/agent/README.md) — architecture, levels, testing,
   verification, committing, components.
4. [`specs/`](specs) — the frozen plan of record: **SPEC-0011** (MVP; accepted),
   **SPEC-0012** (Component ABI), **SPEC-0013** (cross-runtime + vectors),
   **SPEC-0014**–**SPEC-0017**; plus SPEC-0002/0007/0008/0009/0010.
5. `../conformance/AGENTS.md` and `../pro/AGENTS.md` — the shared contract and
   the Rust host.

> **Background (optional, non-normative).** The external literature (FBP, CPM,
> agent SDLC, dark factory) is indexed in
> [`docs/references/README.md`](docs/references/README.md); its PDFs are
> local-only and non-normative. Reading it is **not required** — the laws and
> specs are authoritative.

## Current git state

- **Branch:** `main` in every repo; in sync with `origin/main`.
- **Topology:** `main` is the only branch. The retired Manager line's tip is the
  annotated tag `v0.29.0-milestone`. The convention guard forbids the legacy
  `agent_centric.fbp` package and `fbp-*` gates from returning.
- **Push policy:** commit and push continuously, no permission needed (Law 13);
  never bypass hooks (`--no-verify` forbidden).
- **Environment note (this machine).** Global `git` has `commit.gpgsign=true`
  and `~/.ssh/config` points `github.com` at a public key. If the SSH agent is
  unavailable, commits fail at signing and pushes fail. Commit with a per-command
  `-c commit.gpgsign=false` (never `--no-verify`); push normally (this session's
  pushes succeeded without the ssh workaround). **Do not edit global config/**keys**.

## Fresh-session start

1. Read `AGENTS.md` → `PRINCIPLES.md` → `.agentfactory.toml` (mode **L1**;
   target L3, gated) → this file.
2. Confirm every tree: `git branch --show-current` → `main`; `git status` clean
   (in `core`, `../conformance`, `../pro`).
3. Run §"Verify everything" and confirm the results.
4. Continue from **Next**. Never act above the active level without the operator
   changing `.agentfactory.toml` first.

### Kickoff prompt (paste into a new session)

> Continue the mission-critical `agent-centric` system (CBP/ABM; deterministic,
> local-first, fail-closed). Open at the workspace root `cbp/`. Read
> `core/AGENTS.md` -> `core/PRINCIPLES.md` -> `core/.agentfactory.toml` (active
> **L1**; target L3 gated) -> `core/HANDOFF.md`, then the frozen plan
> `core/specs/SPEC-0011` through `SPEC-0017`, and `conformance/AGENTS.md` +
> `pro/AGENTS.md`. Confirm all three repos are on `main` and clean, then run the
> verification block in `core/HANDOFF.md` (expect: cbp-check READY, **1400
> passed**, shared suite **31/31** on Python + Rust, WASM suite **10/10**).
> **Layers 0, 1a, 1b, 1c and signing are delivered**; the next step is **Layer 2**.
> Obey the hard laws: whole-file replacement
> only via `tools/safe-replace.sh` (**never in-place edits**), commit and push
> continuously, never `--no-verify`. Never act above L1 without the operator
> changing `.agentfactory.toml` first.

## What Agent-centric is

- **Two layers (SPEC-0007).** `agent-centric` is the **harness** — boot + runtime
  + meta. It **executes** the CBP tree and is **not** a node. The tree's root is
  the **shell component**, an ordinary component.
- **Everything is a component** (CBP node = ABM agent). A component is an
  **abstract, blank object**: `init`/`run`/`kill` over a **ZeroMQ** event loop, a
  **typed WIT contract**, and **two-level identity** (abstract = contract hash;
  task = signed content hash).
- **Posture:** deterministic control plane, local-first, fail-closed, no
  unverified success, full auditability.

## The frozen plan (full version — SPEC-0011 accepted)

The planning session fixed the target as the **full version**, not a crippled
subset. The Core public repo is a **Python reflection** that helps build it.

- **SPEC-0012 Component ABI v1.** `init(boot_config)` / `run(event_loop)` /
  `kill()`. Transport **ZeroMQ**. Control plane is always canonical JSON; the
  data plane is a host-granted encoding (`json` baseline, pinned canonical
  `msgpack`; canonical JSON is integers-only, floats MUST use a binary
  encoding). All channels/ports and tasks are announced over the **initial
  hard-coded channels**. Identity is **never the ports**; at rest a component has
  **no edges**. Registry sources are open (git repo, directory, binary, …).
- **SPEC-0013 Cross-runtime realization + conformance vectors.** Two paths:
  **translation/compilation** (canonically WASM) and **native-runtime invocation**
  (language-server protocol; runtime dialed up / JIT-provisioned). **Equivalence
  is proven by the shared vectors, never by Turing completeness**; a runtime is
  TCB and is version-pinned, signed, and recorded.
- **SPEC-0014 Network execution and trust.** Network is orthogonal to
  components; static or generated; execution only runs a content-addressed
  network; a generated network is pinned before running.
- **SPEC-0015 Provenance, trust gates, Assurance Labels.** Classes: static
  (launch), appointed (web/RAG; optional), generated/compiled/translated (gated),
  discovered (gated). "Safe" is a scoped, evidence-backed label (tiers A0–A4),
  never absolute; promotion past A0/A1 is human-gated until the L3 validator.
- **SPEC-0016 Editions, distribution, cloud.** Core (public Python reflection,
  headless) / Pro (private, Rust) / Enterprise (private components) / cloud
  (deployment adapter). Edition = a manifest selecting components. One verified
  semantics; tiers differ only in capacity/performance/deployment/governance/
  support/UI.
- **SPEC-0017 Parked frontier.** Formal semantics/ontology/closed shapes;
  reduction + formal verification (Z3/SMT → Assurance Labels); Universal Function
  Index/dynamic discovery; isolated validator (L3); bounded RSI; whitepaper last.

**Execution layers (target; every layer fallbacked).**

| Layer | Target | Fallback | Status |
| --- | --- | --- | --- |
| 0 | Freeze **ABI + conformance vectors** | must not slip | **done** |
| 1 | **Rust host** runs signed **WASM** components | **Python host**, same ABI | **1a/1b/1c + signing done** |
| 2 | **Content-addressed network** + typed enforcement + replayable trajectory | static `network.v1` | **done (static + generated planner)** |
| 3 | **Read-only web diagram** from the network document | static JSON/image | **done (json + svg + read-only route)** |
| 4 | **Appointed** components (allowlisted) | static-only | not started |

Launch provenances: static, A0 sandboxed. Gated: generated/translated/discovered.

**Process rule:** on complexity/blockage, **step back one or two specs to
resurrect intent** before forcing a workaround.

**Open design items (deferred with intent).** (1) State model: parent-held
environmental state for children vs component-sovereign local state. (2) The
blank-component → dynamic task-load protocol (bind timing), hardened at Layer 2.

## Where we are (delivered)

- **Component ABI + conformance (SPEC-0012/0013, Layer 0)** — `../conformance/`:
  `contracts/component-abi-v1.wit` (validated with pinned `wasm-tools` 1.260.0),
  the control/data-plane split, the normative `ABI.md` (hashed in the lock), and
  the content address `ABI.lock.v1.json`. `vectors/` holds the shared suite
  (31 cases across semantics, lifecycle, transport, determinism, capability,
  encoding) in canonical form, content-addressed by `vectors.lock.v1.json`.
- **Python reflection host + certifier (Layer 0)** — `../conformance/certifier/`:
  `certify.py` (verifies suite/lock/contract, drives a host, compares normalized
  observations, enforces cross-run determinism, emits a deterministic record) and
  `reference_host.py` (the test double + external-protocol endpoint).
- **Rust host (Layer 1a)** — `../pro/`: `cbp-host` implements the ABI and the
  `cbp.conformance-host.v1` protocol and passes the shared suite **31/31**,
  identical to the Python reference host. **Cross-runtime equivalence is proven.**
  Codecs: canonical JSON + pinned canonical MessagePack, strict
  re-encode-and-compare decoding.
- **WASM execution (Layers 1b/1c)** — `../pro/` (feature `wasm`, pinned
  `wasmtime`): content-address an artifact, refuse a tampered one, compile it as
  a **WASM component**, drive `init`/`run`/`kill`. **1b**: narrow task fixture
  (`../pro/fixtures/identity/` → `../conformance/artifacts/identity.wasm`).
  **1c**: a full **`component-abi.v1` guest** (`../pro/fixtures/abi-identity/` →
  `../conformance/artifacts/abi-identity.wasm`) that announces its channels/task
  over the control channel and exchanges Information Packets via the host
  `transport`; the host enforces announcements, `ready`, endpoint addressing,
  declared types, canonical encodings, and the envelope. **Signing**: every
  artifact must carry a detached **Ed25519** signature over its exact bytes that
  verifies against the host trust root (carried in
  `../conformance/vectors/wasm.lock.v1.json`, never fixture data); unsigned or
  bad-signature artifacts are refused before execution. WASM suite **10/10**.
- **Convention layer (SPEC-0001/0003/0004)** — `AGENTS.md` TOC, canonical skills
  (auto-discovered), `tools/new-skill.sh`, same-name component mapping, guarded by
  `tests/test_agent_conventions.py`.
- **Distribution spine (SPEC-0007)** — `component.v1` + `components.lock/v1`;
  content-addressed cache; deterministic resolver; minisign/gpg verification;
  offline directory + git sources; deterministic bundle; boot from lock; process
  isolation; signing service + transparency log.
- **Shell design (SPEC-0008)** — `design.v1` + `validate_design`/`compile_design`.
- **FBP/ABM conformance (SPEC-0009)** — named/typed ports, Information-Packet
  flow with fail-closed back-pressure/deadlock, per-port IIPs, composite
  external-ports boundary guard, repeated-activation streaming, per-component
  state sovereignty, a component at rest has no edges. **8/9** built; only
  recorded/confidence-scored `model` components open.
- **Docs / presentation** — README showcase; historical docs removed; `STATUS.md`
  retained as volley history.

## Spec status audit

`status` is `draft | accepted | implemented`; `accepted` = design approved, not built.

- **SPEC-0011** `accepted` — MVP definition frozen; testable acceptance +
  status-record correction remain.
- **SPEC-0012 / SPEC-0013** `accepted` — **Layers 0/1a/1b/1c + signing
  delivered** (ABI rev 3, vectors, certifier, Rust host 31/31, content-addressed
  WASM 10/10 including a full `component-abi.v1` guest and Ed25519 artifact
  signing).
- **SPEC-0014** `implemented` — `network.v1` (static + deterministic-planner)
  delivered: pinned before running, typed at instantiation, replayable; suite
  **14/14** cross-runtime. (Autonomy proposes / the pin disposes is the pin rule.)
- **SPEC-0015–0017** `draft` — frozen full-version plan; nothing built.
- **SPEC-0002** `accepted` — Phase 1 not built (**0/6**): `review.v1` absent; no
  `Agent`→`Component` adapter; `_REGISTRY`/`_child_class_for` remain; no holdout
  scenarios; no response `confidence`/Review; no `inproc`-only backend.
- **SPEC-0003/0004/0006** `implemented` — criteria met by the convention guard;
  some checkboxes un-ticked (doc-only correction).
- **SPEC-0007** `accepted` — Phases 0/0.5a/1a/1b/2 delivered; 0.5b signing
  delivered, **live mirror open**; no committed umbrella `components.lock`.
- **SPEC-0008** `draft` — `design.v1` + validate/compile delivered; n8n adapter,
  design→commit pinning, envelope limits open.
- **SPEC-0009** `draft` — **8/9** built; only recorded/confidence-scored `model`
  components open. Status understates it.
- **SPEC-0010** `draft` — roadmap only.

## Next

- **Immediate: Layer 4** — **appointed** components (allowlisted; static-only
  fallback), then the signing/distribution remainders below.
- **Signing remainder** — wire the trust root into a signed lock-level artifact
  and the transparency log; key rotation. (Layer 1c signing is delivered: Ed25519
  over content addressing, accept/refuse vectors.)
- **Distribution remainder** — wire the lock-level signature into
  `boot_from_lock`; add the `design/network → pin → components.lock` producer;
  commit the umbrella `components.lock` (needs the live mirror).
- **SPEC-0008 remainder** — n8n adapter (import/export + canonical round-trip).
- **SPEC-0009 remainder** — recorded/confidence-scored `model` components.
- **SPEC-0017 frontier** — formal semantics/ontology; reduction + formal
  verification; Universal Function Index; isolated validator (L3); bounded RSI;
  whitepaper last.

## Open threads (blockers & honest gaps)

- **Live self-hosted mirror** (Forgejo/Gitea) — needed for a committed umbrella
  `components.lock`; no real pinned remotes yet.
- **Integration spine** — the offline primitives exist, but `design/network →
  pin → signed lock → boot → run typed flow → replay` is not wired end-to-end;
  the lock-level signature is not consumed at boot.
- **`review.v1` / `confidence`** — absent; parked under SPEC-0017. Launch ships a
  minimal deterministic scorer + formalizer at the model boundary.
- **Honest FBP note** — non-port networks still run on the legacy args model;
  port-declared networks use `run_flow`. Both are deterministic.
- **The ABI is now consumed.** `component-abi.v1` (revision 3) is implemented
  by a real `component-abi.v1` WASM guest (Layer 1c), so changes are
  **additive-only** from here (`component-abi.v2` for anything else). The one
  pre-consumption correction made at consumption was the data-plane transport
  (`transport.send-on` / `receive-on`), which the frozen `send`/`receive`
  (control channel only) could not express.

## How to work here (hard laws)

- **Law 11 — no in-place edits, EVER.** Replace whole files via
  [`tools/safe-replace.sh`](tools/safe-replace.sh) (`opencode.json` denies
  `edit`/`write`/`patch`). Transform a large file into a temp and `safe-replace`
  it; never retype-and-drift, never `sed -i`, never `Path.write_text`.
  See the `safe-file-editing` skill.
- **Law 12 — test authority.** The agent may run any tests; report results
  faithfully; the operator/CI is the record of truth.
- **Law 13 — commit and push continuously.** Hooks and CI are the guardrails;
  never `--no-verify`.

**Process risk (observed — guard against it).** During this build the authoring
agent made **three Law-11 slips** (twice `sed -i`, once Python `write_text`).
Each was caught and the whole file re-emitted via `tools/safe-replace.sh`, so
committed content is byte-complete — but the *method* was wrong. A new session
must not mutate a file any way other than a whole-file `cp` replace. If you catch
yourself reaching for an in-place tool, stop and use `safe-replace`.

## Key files

- **conformance (1c)** — `artifacts/abi-identity.wasm`;
  `vectors/wasm-{fixtures,suite,lock}.v1.json` (8 cases).
- **pro (1c)** — `fixtures/abi-identity/` (WIT-referencing `component-abi.v1`
  guest); `src/wasm.rs` (ABI bindgen + host mediator).
- **core** — `src/agent_centric/cbp/` (`component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `component_state.py`,
  `component_graph.py`, `component_boot.py`, `component_process.py`,
  `signing_service.py`, `transparency.py`, `design.py`, `network.py`, `flow.py`,
  `cache.py`, `resolver.py`, `signing.py`, `agent.py`); `src/agent_centric/contracts/`
  (`component.py`, `components_lock.py`, `design.py`); `specs/SPEC-0011`–`0017`;
  `examples/components/`; `tests/test_agent_conventions.py`.
- **conformance** — `contracts/component-abi-v1.wit`, `contracts/ABI.md`,
  `contracts/ABI.lock.v1.json`, `contracts/network-v1.md`;
  `vectors/{fixtures,suite,vectors.lock}.v1.json`;
  `vectors/network-{fixtures,suite,lock}.v1.json` (network.v1, 9 cases);
  `vectors/wasm-{fixtures,suite,lock}.v1.json`; `artifacts/identity.wasm`;
  `certifier/{certify.py,reference_host.py,PROTOCOL.md}`.
- **pro** — `src/{main.rs,host.rs,codec.rs,wasm.rs,network.rs}`; `fixtures/identity/`
  (guest + WIT); `scripts/certify.sh`; `Cargo.toml` + committed `Cargo.lock`;
  `rust-toolchain.toml`.

## Non-goals (do not build without explicit direction)

See [`AGENTS.md`](AGENTS.md) → *Non-goals*. In short: no new agents/ACP/refactor/
dependency bumps; no auto-accept or unsupervised money; no send/delete/move
email; no cloud/network in CI; no changing Manager orchestration/verification/
policy/envelope/accounting semantics (prefer adapters/backends); never bypass
hooks or CI; discovery/generation is never auto-trusted (SPEC-0015 gates).

## Historical documents

[`STATUS.md`](STATUS.md) records the volley-by-volley history; not updated
retroactively.
