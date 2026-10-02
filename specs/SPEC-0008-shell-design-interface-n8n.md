---
id: SPEC-0008
title: Shell design interface and the n8n authoring adapter
type: feature
target_repo: agent-centric
target_branch: main
status: implemented
owner: operator
---

# Shell design interface and the n8n authoring adapter

**Operator intent (recorded).** The **shell** (the tree's root node) is also the
human-facing **interface to the system**, and humans will design CBP process
graphs with **n8n**. This spec fixes the boundary: n8n is a **design-time
authoring surface**; the harness stays the **local-first, deterministic
executor**. It is target architecture with phases; no claim exceeds what the
operator's suite proves.

## Context

The harness (`agent-centric`) is the **wrapper and harness agent** around the
CBP/ABM execution model. That model — the network of components/agents — may be
**generated dynamically at run time**, and **any interface** (the user interface,
an n8n surface, an API, …) is itself **composed and available at start-up** of an
`agent-centric` execution network. The invariant that keeps this deterministic is
that whatever is generated or composed is **frozen and content-hashed before it
executes** (SPEC-0002 §3).

- The shell is already the root **component** of the tree — not an external
  orchestrator — see [`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md)
  §1 and [`docs/agent/architecture.md`](../docs/agent/architecture.md). Phase 2
  boots a tree from a pinned lock (`cbp/component_boot.py`).
- The target architecture already names the graph document **`network.v1`** —
  authored statically or dynamically, then frozen and content-hashed before
  execution — see [`SPEC-0002`](SPEC-0002-cbp-component-architecture.md) §3/§6.
- n8n is a widely used, self-hostable workflow editor and a good **human design
  surface**, but it is a network application with its own runtime and
  credentials; it must never sit on the execution path (Law 5).
- Missing: a defined **design interface** (what a designer submits, what the
  shell validates and returns) and an **adapter contract** (how n8n maps to
  `network.v1`), so implementation can proceed without inventing scope.

## Decision

### 1. Compose (or generate), freeze, then execute

| Phase | Where | Artifact | Never does |
| --- | --- | --- | --- |
| **Compose / generate** | human tools (n8n) **or** the running harness | a `design.v1` document | execute an unfrozen component; hold runtime credentials; bypass validation |
| **Execute** | the harness, local-first | a verified `components.lock` + booted tree | call n8n; require network; accept an unfrozen design |

The harness (`agent-centric`) is the **wrapper/harness agent** around the CBP/ABM
execution model. The **shell** is its root component — the single boundary
through which a design is validated, compiled, pinned, signed, and booted. A
network may be generated dynamically (by an agent, a human, or n8n); it becomes
executable only after it is frozen, content-hashed, and verified.

### 1a. Interfaces are composed components

An **interface** (UI, n8n surface, CLI, API) is not a privileged harness feature:
it is a component, composed and available **at start-up** of the execution
network, exactly like any other node (SPEC-0002 §1: no privileged type). Local
interfaces compose freely; interfaces that require network are **declared-gated**
and off by default (Law 5).

### 2. The design artifact is declarative and content-hashed

A `design.v1` document wraps a `network.v1` graph and a list of *requested*
component references (name + tag/range/commit). Tags/ranges are resolved to
immutable commits **only at pin time** (the SPEC-0007 tag rule); a design carries
no execution authority and never floats at run time.

### 3. The n8n adapter is out-of-process and optional

A separate adapter translates n8n workflow JSON ⇄ `design.v1`. It is an
**adapter** (the non-goals prefer adapters over core changes): the harness never
imports n8n, and n8n never enters the execution path. It is self-hosted, and CI
tests it with fixtures — never the network.

### 4. Fail-closed validation

A design is untrusted input. Validation (schema, graph acyclicity, port/edge
integrity, component availability, envelope limits) is deterministic and
fail-closed; an invalid design is an explicit error, never a partial plan. A
design can only **reference** components that resolve, verify, and sign under
SPEC-0007 — it can never introduce executable code, an unsigned artifact, or a
grant the operator did not pin.

### 5. One canonical round-trip

`design.v1 → n8n → design.v1` (and the reverse) must be **stable**: the same
design re-imports to the same canonical bytes (`design_hash`), so a designer's
work is reproducible and diffable.

## Scope

- **In:** `design.v1` (additive contract); the shell design interface
  (`validate` / `compile` / `pin` / `record`); the n8n adapter mapping and
  round-trip contract; validation and its fail-closed gates; the design/execution
  boundary and its guard.
- **Out (explicit non-goals):** putting n8n (or any network tool) on the run path
  or giving CI network access; a new execution backend; changing Manager
  orchestration, verification, policy, envelope, or accounting semantics; new
  agents; operating or hosting n8n for the operator; cloud/remote n8n; granting a
  design any authority beyond the operator's pinned, signed components.

## Interface sketch (to be finalized in Phase 1)

```
design.v1: {
  schema: "design.v1",
  id, title,
  network: <network.v1>,                 # canonical graph (SPEC-0002 §6)
  components: [ {name, requested} ],     # tag|range|commit — resolved at pin time
  metadata: { ... }                      # non-executable annotations (author, notes)
}
design_hash = sha256(canonical_serialization)
```

Shell design interface (harness-owned, local):

1. `validate(design) -> report` — deterministic, fail-closed.
2. `compile(design) -> network.v1` — canonical document + `design_hash`.
3. `pin(design, source) -> components.lock` — resolve requested refs to commits
   (SPEC-0007 §4), verify, and build the lock.
4. `record(lock, signer) -> transparency entry` — sign + append (SPEC-0007 §6).
5. `boot` — Phase 2 `boot_from_lock`.

n8n mapping (adapter-owned): n8n node ⇄ component; n8n connection ⇄ edge; n8n
workflow setting ⇄ envelope/grant — all validated against `design.v1`.

## Phases

| Phase | Status | Deliverable |
| --- | --- | --- |
| 0 | **delivered** | this spec; the wrapper/interface model; the compose-freeze-execute boundary and non-goals |
| 1 | MVP | `design.v1` contract (`contracts/design.py`) + deterministic, fail-closed `validate`/`compile` (`cbp/design.py`); demo `examples/design_compile.py`; `tests/test_design.py` |
| 2 | **delivered** | n8n adapter import/export + canonical round-trip (`cbp/n8n_adapter.py`); fixture tests `tests/test_n8n_adapter.py` + demo `examples/n8n_roundtrip.py` |
| 3 | **delivered** | `pin`/`record` wiring: `cbp/lockfile.py` (`pin_design` + `ComponentPin`) resolves a design's requested refs to immutable commits and content-addressed bundles at pin time (deterministic, offline, fail-closed) and builds the lock; `record_release` signs + appends it; `tests/test_lockfile.py` + demo `examples/design_pin.py` |
| 4 | declared-gated | live self-hosted n8n integration (needs operator infra) |

## Acceptance criteria

- [x] `design.v1` exists, is additive, canonical, and content-hashable
      (`contracts/design.py`).
- [x] `validate` is deterministic and fail-closed on schema, cycle, port/edge,
      and absence violations — proven by `tests/test_design.py`.
- [x] An identical design compiles to the same `design_hash`; compilation is
      deterministic and offline.
- [x] `validate` also enforces envelope limits (with the runtime/pin phase).
- [x] Pinning resolves refs to commits and never floats at run time
      (`cbp/lockfile.py`; the lock stores commits + `tree_sha256` only).
- [x] The n8n round-trip is stable (same canonical bytes) and tested with
      fixtures only — no network in CI.
- [x] No execution path imports n8n; a design cannot introduce unsigned code
      (pinning refuses an unsigned component).
- [x] The convention guard asserts this spec is recorded.

## Progress

- **Phase 3 delivered.** `cbp/lockfile.py` implements the design `pin` step:
  requested refs are resolved to immutable commits and content-addressed bundles
  **at pin time** (the SPEC-0007 tag rule), from an offline source per
  component. Pinning is deterministic (name-sorted entries; the same design
  yields the same `lock_hash`) and fail-closed: an invalid design, an unnamed
  identity, an unsigned component, an unpinned contract, or an unresolvable ref
  all refuse before a lock is produced. `record_release` signs the lock and
  appends it to the transparency log; `boot_from_lock` boots the resulting tree.
  Proven by `tests/test_lockfile.py` (pin → record → boot end to end) and
  `examples/design_pin.py`.

## Progress

- **Phase 2 delivered.** `cbp/n8n_adapter.py` translates a `design.v1` ⇄ an n8n
  workflow projection: nodes ⇄ components, connections ⇄ edges, edge/IIP
  metadata on the target node, and design identity/metadata in `meta.cbp`. The
  round-trip is lossless and stable (`from_n8n(to_n8n(d))` keeps `design_hash`;
  `to_n8n(from_n8n(w))` reproduces the same bytes) and strictly fail-closed:
  a non-projection workflow, an unknown node type or component/edge/IIP key, an
  unsupported nested `subnet`, an inconsistent connection graph, or a broken
  edge/IIP ordering refuses. n8n is never imported. Proven by
  `tests/test_n8n_adapter.py` and `examples/n8n_roundtrip.py`.

## Progress

- **Envelope limits delivered (Phase 1/3 remainder).** A `design.v1` may
  declare a per-component hard resource **envelope** (`contracts/design.py`
  `EnvelopeGrant`); `validate_design` enforces it deterministically and
  fail-closed (the grant must name a real component, be unique, declare at
  least one numeric bound, and construct a valid runtime `ResourceEnvelope` —
  unknown/malformed/negative/empty bounds refuse). `pin_design` re-validates
  and pins each validated grant into its signed lock entry, so the declared
  hard bounds are immutable and signed. `Design` and `LockEntry` serialize the
  envelope **additively** (omitted when empty, so prior design/lock hashes are
  unchanged), and the n8n round-trip preserves envelopes in `meta.cbp`.
  Proven by `tests/test_cbp_design_envelopes.py`.

## Risks / invariants

- **Untrusted design** → validate fail-closed; reference-only; pin/sign gates.
- **Adapter drift** → one canonical round-trip; fixture tests; no core coupling.
- **Credential leakage** → n8n credentials stay in n8n; the adapter passes only
  design documents; the harness never stores n8n secrets.
- **Silent network / cloud** → self-hosted, design-time only; CI hermetic.
- **Scope creep into core** → adapter-first; Manager-semantics changes remain
  forbidden.

Respects Laws 1, 2, 3, 4, 5 (amended: acquisition separate from run time), 7, 8,
10, 11, 12, 13. It changes no `src/` semantics in Phase 0 and no KERNEL invariant;
like SPEC-0007 it extends the harness adapter-first.
