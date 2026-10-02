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
| [`core/`](AGENTS.md) | public | Python **reflection** + the frozen specs (`specs/SPEC-0011`–`0019`). |
| [`../conformance/`](../conformance/AGENTS.md) | public | The shared contract: WIT ABI + conformance vectors + certifier. |
| [`../pro/`](../pro/AGENTS.md) | private | The Rust host (optimization edition). |

Delivered so far, of the full-version plan (`SPEC-0011`): **Layers 0, 1a, 1b,
1c, signing, and Layer 2** (the frozen `component-abi.v1` and v1 conformance
vectors; a **Rust host certified cross-runtime-identical** to the Python
reference host — shared suite **31/31**; **content-addressed, signed WASM
execution** — WASM suite **10/10**, including a full **`component-abi.v1` WASM
guest** that announces its channels/tasks and exchanges Information Packets over
the host-mediated transport, with locked-down refusal of unsigned/bad-signature
artifacts; **`network.v1`** — static + deterministic-planner, pinned before it
runs, typed at instantiation, replayable — network suite **14/14** on both
hosts), plus **Layer 3** (a deterministic, content-addressed read-only network
diagram) and **Layer 4** (**`appointed.v1`** — allowlisted appointed sources,
admitted only through a host-configured source allowlist and a detached Ed25519
signature, contained with zero capabilities (A0), recorded as a scoped Assurance
Label — appointed suite **8/8** on both hosts). The distribution spine is also
advanced: the **`design → pin → components.lock` producer** (`cbp/lockfile.py`,
SPEC-0008 Phase 3), **signing-key rotation** with overlapping trust windows
(`cbp/trust.py`, SPEC-0007 §6), and the **n8n authoring adapter**
(`cbp/n8n_adapter.py`, SPEC-0008 Phase 2, lossless `design.v1` ⇄ n8n round-trip)
are delivered, as is the **recorded, confidence-scored `model` component**
boundary (`cbp/model_record.py`, SPEC-0009 slice 7) and **design envelope
limits** (SPEC-0008 §4, validated and pinned), the **offline umbrella
`components.lock`** generator (test-signed offline; operator signing remains),
and the **SPEC-0011 "viable" end-to-end** — boot a signed lock → typed-port
dataflow → durable-ledger replay, with a recorded, confidence-scored `model`
node (`cbp/boot_network.py`).

The **L3 isolated holdout validator (SPEC-0019)** is built with **structural**
isolation: scenarios live in the operator-private root (owner-only; the public
`specs/holdout/` is a placeholder), the validator is a separate context-gated
process, and runs emit an append-only, content-addressed **idempotent** ledger.
It has a synthetic known-good/known-bad self-test. Operator-authored scenarios,
network-boot probes, and the L3 flip remain.

## Verify everything (do this before acting)

```sh
# core (Python reflection) ------------------------------------------------
cd core
uv sync --extra dev                                   # one-time
uv run ruff check .                                   # -> clean
uv run mypy src                                       # -> 125 files, clean
uv run agent-centric cbp-check                        # -> READY (8/8)
uv run pytest -o addopts="" -p no:cacheprovider       # -> 1618 passed

# conformance (shared contract) -------------------------------------------
cd ../conformance
python3 certifier/certify.py                          # Python reference: 31/31
python3 certifier/certify.py --host-cmd "python3 certifier/reference_host.py"  # ext protocol: 31/31
python3 certifier/certify.py \
  --suite vectors/network-suite.v1.json \
  --fixtures vectors/network-fixtures.v1.json \
  --lock vectors/network.lock.v1.json \
  --contract-lock contracts/ABI.lock.v1.json          # network.v1: 14/14
python3 certifier/certify.py \
  --suite vectors/appointed-suite.v1.json \
  --fixtures vectors/appointed-fixtures.v1.json \
  --lock vectors/appointed.lock.v1.json \
  --contract-lock contracts/ABI.lock.v1.json \
  --artifacts-dir artifacts                           # appointed.v1: 8/8
#   (the WASM suite is certified by the Rust host, below)

# pro (Rust host) ---------------------------------------------------------
cd ../pro
./scripts/certify.sh                                  # builds + certifies ALL FOUR suites (31/31 + 10/10 + 14/14 + 8/8) + unit tests
cargo test --release --features wasm                  # codec + artifact-signature + appointed tests: 6 passed

# workspace IDE hygiene (run from the workspace root) ----------------------
cd ..
./scripts/check-pyright.sh                            # -> 0 errors, 0 warnings, 0 notes
```

Certifier exit codes: `0` certified · `1` a case failed / nondeterministic ·
`2` suite, lock, or contract drifted (refused **before** running).

## Verified state (tips)

- All three repos are on `main` and pushed to `origin/main`. **Never trust a
  commit hash written in prose — it drifts. Read the live tips instead:**
  `git -C core log -1 --oneline`, `git -C ../conformance log -1 --oneline`,
  `git -C ../pro log -1 --oneline`.
- Core gates: `ruff` clean; `mypy` clean (125 files); `cbp-check` **READY (8/8)**;
  convention + home-path guard passed; full suite **1618 passed** (~63 s).
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
- Appointed (**Layer 4**): `appointed.v1` freezes the allowlisted-appointment
  trust gate — a component from a named external origin is admitted only through
  a host-configured **source allowlist** and a detached **Ed25519** signature over
  its exact bytes, runs contained with zero capabilities (**A0**), and records a
  scoped Assurance Label (property, tier, evidence). The ordered, fail-closed gate
  is implemented by the Python reference host and the Rust host and passes **8/8**
  cross-runtime; `appointed.lock.v1.json` carries the allowlist trust root.
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
  `-c commit.gpgsign=false` (never `--no-verify`). For tests that create temp git
  repos, point `GIT_CONFIG_GLOBAL` at a throwaway gitconfig with
  `[commit] gpgsign = false` - never edit the real global config. To push while
  Bitwarden's SSH agent is down, use the already-authenticated `gh` token over
  HTTPS through a throwaway askpass (`GIT_ASKPASS` -> `gh auth token`) with the
  repository's HTTPS URL; this changes no remote and logs no secret.
  **Do not edit global config/**keys.**

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
> **L1**; target L3, operator-gated) -> `core/HANDOFF.md`, then
> `core/specs/SPEC-0011`–`0019`, `conformance/AGENTS.md`, `pro/AGENTS.md`, and
> `infra/HANDOFF.md` + `infra/specs/SPEC-0018-instantiation.md`. Confirm **all
> four** repos (`core`, `conformance`, `pro`, `infra`) are on `main` and clean,
> then run `core/HANDOFF.md`'s verification block (expect: `cbp-check` READY,
> **1618 passed**; shared suite **31/31** in-process + external + Rust; WASM
> **10/10**; network **14/14**; appointed **8/8**; infra **79 passed**;
> basedpyright **0/0/0**). Delivered: Layers 0–4 + signing, the distribution
> spine, the offline umbrella lock (committed test-signed; the **operator-signed**
> system lock is recorded in `infra/locks/core-umbrella/`), the local CD
> substrate, and **SPEC-0019** (the L3 isolated holdout validator: structural
> isolation, decision engine, `holdout.lock`, probe runner, append-only ledger).
> **SPEC-0018 Phase 2 (authored, private companion): the bootstrap services
> are graduated to `component.v1` and `service.v1` binds the composition**; the
> agent verifies the composition lock + graph before apply. **Next mission:
> self-hosting** (boot the composition itself) and, operator-gated, provision/
> enroll the intranet host and publish to the live mirror.
> Hard laws: whole-file replacement only via `tools/safe-replace.sh` (**never
> in-place edits**), resolve every home path from `$HOME`, commit and push
> continuously, never `--no-verify`. Mission-critical boundaries: never act above
> L1 without the operator changing `.agentfactory.toml`; operator gates = real
> host provisioning/enrollment, the operator signing key, and any public publish;
> the **authoring principal must be unprivileged and distinct** (`agent-runner`,
> no docker/sudo — docker is root-equivalent; this workstation is a rehearsal),
> and holdout scenarios and keys never enter a public repo.

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
| 4 | **Appointed** components (allowlisted) | static-only | **done (allowlist + A0 gate)** |

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
- **Network + diagram (Layers 2/3)** — `../conformance/`: `contracts/network-v1.md`
  (revision 2) and `vectors/network-{fixtures,suite,lock}.v1.json` (**14 cases**;
  static + deterministic-planner, pinned before running, typed at instantiation,
  replayable), implemented by the Python reference host and the Rust host
  (`../pro/src/network.rs`). `core`: `cbp/diagram.py` projects a validated network
  into a deterministic, content-addressed read-only diagram (JSON + SVG).
- **Appointed (Layer 4)** — `../conformance/contracts/appointed-v1.md` (revision
  1) and `vectors/appointed-{fixtures,suite,lock}.v1.json` (**8 cases**):
  allowlisted appointed sources, an ordered fail-closed gate, A0 containment, and
  a recorded Assurance Label. Implemented by the Python reference host
  (`certifier/reference_host.py`, pure-stdlib RFC 8032 verifier) and the Rust host
  (`../pro/src/appointed.rs`); **8/8** on both.
- **Convention layer (SPEC-0001/0003/0004)** — `AGENTS.md` TOC, canonical skills
  (auto-discovered), `tools/new-skill.sh`, same-name component mapping, guarded by
  `tests/test_agent_conventions.py`.
- **Publication hygiene (SPEC-0016 §94 / SPEC-0018 §13)** —
  `tests/test_public_boundary.py` fails closed if a private sibling-repo slug,
  private host id, or RFC 1918 address appears in this public repo. The
  workspace-root basedpyright policy (the IDE tray must read
  `0 errors, 0 warnings, 0 notes`) is versioned in `tools/workspace/` with
  `check-pyright.sh`.
- **Distribution spine (SPEC-0007)** — `component.v1` + `components.lock/v1`;
  content-addressed cache; deterministic resolver; minisign/gpg verification;
  offline directory + git sources; deterministic bundle; boot from lock (with
  lock-level signature verification, fail-closed); process isolation; signing
  service + transparency log. Boot is **idempotent** (booting the same lock twice
  yields an identical tree) and the transparency-log append is **idempotent for
  its head** (a retried release never duplicates evidence).
- **Offline umbrella `components.lock` (SPEC-0011 / SPEC-0007 Phase 2)** —
  `designs/core.v1.json` (shell → registry) is pinned by an **offline directory
  source** through the additive `DirectoryPin` path in `cbp/lockfile.py`; the
  canonical bundles (`artifacts/components/*.tar`), the lock (`components.lock`),
  and its detached signature are committed, and `tools/umbrella-lock.sh`
  regenerates and verifies them deterministically. The committed lock is
  **test-signed offline**; the operator re-signs with the real key (key gate).
  Boot verifies the lock signature and every component signature from a
  `DirectorySource` (12 tests).
- **Shell design (SPEC-0008)** — `design.v1` + `validate_design`/`compile_design`.
- **Design → pin → lock (SPEC-0008 Phase 3)** — `cbp/lockfile.py` (`pin_design` +
  `ComponentPin`) resolves a design's requested refs to immutable commits and
  content-addressed bundles **at pin time** (offline), deterministically and
  fail-closed (invalid design, unnamed identity, unsigned component, unpinned
  contract, or unresolvable ref all refuse); `record_release` signs + appends the
  lock and `boot_from_lock` boots the tree. Proven by `tests/test_lockfile.py` +
  demo `examples/design_pin.py`.
- **Signing-key rotation (SPEC-0007 §6)** — `cbp/trust.py` implements overlapping
  trust windows (`TrustStore`/`TrustWindow`): a retired key keeps **verifying**
  while only the current key **signs**; key selection and verification are
  explicit-time and fail-closed; every release records its `key_id`, and the
  transparency log verifies against the rotated ring. Proven by
  `tests/test_trust.py` + demo `examples/key_rotation.py`.
- **n8n authoring adapter (SPEC-0008 Phase 2)** — `cbp/n8n_adapter.py` projects a
  `design.v1` ⇄ an n8n workflow (**nodes** ⇄ components, **connections** ⇄ edges;
  edge/IIP metadata on the target node). The round-trip is lossless and stable
  (same `design_hash`; re-export reproduces the same canonical bytes) and
  fail-closed on any unsupported/inconsistent input. Proven by
  `tests/test_n8n_adapter.py` + demo `examples/n8n_roundtrip.py`.
- **FBP/ABM conformance (SPEC-0009)** — named/typed ports, Information-Packet
  flow with fail-closed back-pressure/deadlock, per-port IIPs, composite
  external-ports boundary guard, repeated-activation streaming, per-component
  state sovereignty, a component at rest has no edges, and recorded,
  confidence-scored `model` components (`cbp/model_record.py`). **9/9** built.
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
- **SPEC-0015** `draft` — **appointed launch slice delivered** (Layer 4:
  `appointed.v1` source allowlist + ordered A0 gate, **8/8** cross-runtime); tiers
  A1–A4, `review.v1` promotion, and the generated/discovered gates remain
  (SPEC-0017).
- **SPEC-0016** `draft` — frozen; nothing built. **SPEC-0017** `draft` —
  frontier index; its §4 **isolated holdout validator** is promoted to
  **SPEC-0019** (below).
- **SPEC-0002** `accepted` — Phase 1 not built (**0/6**): `review.v1` absent; no
  `Agent`→`Component` adapter; `_REGISTRY`/`_child_class_for` remain; no holdout
  scenarios; no response `confidence`/Review; no `inproc`-only backend.
- **SPEC-0003/0004/0006** `implemented` — criteria met by the convention guard;
  some checkboxes un-ticked (doc-only correction).
- **SPEC-0007** `accepted` — Phases 0/0.5a/1a/1b/2 delivered; 0.5b signing and
  **key rotation** delivered; the **offline umbrella `components.lock`** is
  committed (test-signed offline; operator re-signs with the real key);
  **live mirror open**.
- **SPEC-0008** `draft` — `design.v1` + validate/compile, the Phase 3
  `pin`/`record` wiring (`cbp/lockfile.py`), the Phase 2 n8n adapter
  (`cbp/n8n_adapter.py`), **and** design envelope limits (`cbp/design.py`
  validate + `pin`) delivered.
- **SPEC-0009** `draft` — **9/9** built: recorded, confidence-scored `model`
  components delivered (`cbp/model_record.py`); spec status understates it.
- **SPEC-0010** `draft` — roadmap only.
- **SPEC-0018** `draft` — service-host provisioning + continuous deployment
  (verify-then-apply; self-hosted Gitea first). Public origin-agnostic spec in
  `specs/SPEC-0018-service-host-provisioning-and-continuous-deployment.md`; the
  private instantiation lives in a separate **private companion repo**. Additive
  public **`service.v1`** contract delivered (`contracts/service.py`, 36 tests,
  incl. additive task-content pins and opt-in `require_task_digests`); Phase 1
  infra **authored + hardened** (deterministic tasks, a pull/apply agent that
  content-verifies every mutating task, a pinned host definition + `service.v1`
  instance, enrollment runbook, infra CI, DR drill) — **nothing provisioned**.
  Phase 2 graduation **authored**: the bootstrap services are wrapped as public
  additive `component.v1`, pinned in a signed composition lock, and bound by
  `service.v1` (`refs.composition`); the agent verifies the lock + graph before
  apply (fail-closed).
- **SPEC-0019** `draft` — the **L3 isolated holdout validator**: **structural**
  isolation (scenarios in the operator-private root, owner-only; public
  `specs/holdout/` a placeholder; the authoring principal must be distinct and
  unprivileged — no docker/sudo, since docker is root-equivalent), a context-
  gated separate process, the deterministic decision engine (`ERROR` = infra
  flake; mixed PASS/FAIL = nondeterministic; high = all-run; normal = 2-of-3),
  fail-closed `holdout.lock` verification + a `holdout.v1` task-probe runner, an
  append-only **idempotent run ledger**, and a synthetic known-good/known-bad
  self-test (`tools/holdout-validate.py`, 39 tests; core `26b555c`).
  Operator-authored scenarios, network-boot probes, and the L3 flip remain; L3
  stays `declared-gated`.

## Next

- **Umbrella `components.lock` — delivered offline; operator signing remains.**
  The `design → pin → components.lock` producer and the additive offline
  **directory-pin** path are delivered; `designs/core.v1.json`, the canonical
  bundles, the lock, and its detached signature are committed and regenerated
  deterministically by `tools/umbrella-lock.sh`. The committed lock is
  **test-signed offline**; the operator re-signs it with the real, out-of-process
  key and installs the public trust root (key gate). Everything else on the spine
  is done: content-addressed cache, deterministic resolver, minisign/gpg
  verification, git/directory sources, deterministic bundle, boot-from-lock (with
  lock-level signature + transparency log verification, fail-closed), **key
  rotation**, and the **`pin`/`record`** wiring.
- **SPEC-0018 (draft): service-host provisioning + CD** — public spec in
  `specs/`; the instantiation lives in a **private companion repo**. Phase 1:
  bootstrap the intranet host (native Gitea + OpenBao + pull/apply agent) under
  verify-then-apply. Phase 2 (authored): the bootstrap services graduated to
  `component.v1`, with `service.v1` binding the signed composition lock. The
  private mission brief holds the plan and paste-in kickoff prompts.
- **L3 holdout gate (SPEC-0019)** — the validator is built and its isolation is
  **structural**; the remaining steps are operator-side: author scenarios in the
  operator-private root (`$CBP_HOLDOUT_ROOT`, default `$HOME/.cbp/holdout/suite`),
  pin them (`tools/holdout-validate.py --build-lock --suite …`), run the validator
  to **green** on a principal/host distinct from the author, then flip
  `[levels.L3] status` deliberately.
- **Signing** — **key rotation delivered** (`cbp/trust.py`); optional threshold
  signing remains.
- **SPEC-0017 frontier** — formal semantics/ontology; reduction + formal
  verification; Universal Function Index; bounded RSI; whitepaper last. The
  **isolated validator (L3)** is now **SPEC-0019** (spec + Phase 1 isolation +
  decision engine + probe runner delivered).

## Open threads (blockers & honest gaps)

- **Live self-hosted mirror** (Gitea) — no real pinned remotes yet. Now tracked by
  **SPEC-0018** (`specs/`) + a **private companion repo**; Core's umbrella lock is
  offline-signed (SPEC-0011) and no longer depends on it.
- **Integration spine** — delivered. The **lock-level signature is consumed at
  boot** (`boot_from_lock` verifies a detached signature over `lock_hash` before
  resolving anything, and records `lock_verified`, fail-closed); boot may also
  require the lock to be published in a **serving transparency log** (chain +
  signatures + head verified; an unpublished lock refuses). The
  `design → pin → signed lock` producer is delivered (`cbp/lockfile.py`), and
  `cbp/boot_network.py` closes the end-to-end wiring: **boot a signed lock →
  run its typed-port `network.v1` over the verified entries → replay the durable
  directive ledger**, with a spawned `model` node recorded and
  confidence-scored (`tests/test_viable_demo.py`, `examples/viable_demo.py`).
  The **operator-signed system umbrella lock is recorded** in the private infra
  repo (`locks/core-umbrella/`: lock, bundles, and the **public** trust root),
  verified with the core `GpgVerifier` against a keyring holding only the public
  key; the committed Core lock stays test-signed for hermetic CI.
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

**Process risk (observed — guard against it).** Law-11 slips have occurred here.
An early session made **three** (twice `sed -i`, once Python `write_text`); a
later hardening session made **two more `write_text` slips** — a patch script
wrote docs and tests directly instead of piping through `safe-replace`. Each was
caught and the whole file re-emitted via `tools/safe-replace.sh`, so committed
content is byte-complete — but the *method* was wrong. **The patch script is the
trap:** even a "read → transform → write" script must write to a temp and go
through `safe-replace`; never call `Path.write_text` on a repo file. If you catch
yourself reaching for an in-place tool, stop and use `safe-replace`.

## Key files

- **conformance (1c)** — `artifacts/abi-identity.wasm`;
  `vectors/wasm-{fixtures,suite,lock}.v1.json` (10 cases).
- **pro (1c)** — `fixtures/abi-identity/` (WIT-referencing `component-abi.v1`
  guest); `src/wasm.rs` (ABI bindgen + host mediator).
- **core** — `src/agent_centric/cbp/` (`component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `component_state.py`,
  `component_graph.py`, `component_boot.py`, `component_process.py`,
  `signing_service.py`, `transparency.py`, `trust.py`, `design.py`,
  `lockfile.py`, `n8n_adapter.py`, `network.py`, `flow.py`,
  `cache.py`, `resolver.py`, `signing.py`, `agent.py`); `src/agent_centric/contracts/`
  (`component.py`, `components_lock.py`, `design.py`); `specs/SPEC-0011`–`0019`;
  `examples/components/`; `tests/test_agent_conventions.py`.
- **conformance** — `contracts/component-abi-v1.wit`, `contracts/ABI.md`,
  `contracts/ABI.lock.v1.json`, `contracts/network-v1.md`,
  `contracts/appointed-v1.md`;
  `vectors/{fixtures,suite,vectors.lock}.v1.json`;
  `vectors/network-{fixtures,suite,lock}.v1.json` (network.v1, 14 cases);
  `vectors/appointed-{fixtures,suite,lock}.v1.json` (appointed.v1, 8 cases);
  `vectors/wasm-{fixtures,suite,lock}.v1.json`; `artifacts/identity.wasm`;
  `certifier/{certify.py,reference_host.py,PROTOCOL.md}`.
- **pro** — `src/{main.rs,host.rs,codec.rs,wasm.rs,network.rs,appointed.rs}`; `fixtures/identity/`
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
