---
id: SPEC-0009
title: FBP/ABM conformance — ports, Information Packets, subnets, and composition
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# FBP/ABM conformance — ports, Information Packets, subnets, and composition

**Operator direction (recorded).** Follow the **FBP** and **ABM** standards; be
robust, correct, and mission-critical; deliver **both determinism and dynamism**.
This spec states what that commits us to, where the current code is only
*FBP-shaped*, and the additive path to real conformance.

## Context

- [`SPEC-0002`](SPEC-0002-cbp-component-architecture.md) already names the target:
  a component network as a document (`network.v1`), ports + Information Packets
  as MVP, a deterministic CPM scheduler, and models as recorded non-determinism.
- [`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) distributes
  self-contained, independently versioned components.
- [`SPEC-0008`](SPEC-0008-shell-design-interface-n8n.md) composes/generates a
  design, then freezes it before execution.
- **Gap (honest).** `cbp/network.py` currently wires an edge by reading the
  *source component's `args` as its output record* (`network.py:159`) — there are
  no ports, no IPs, and no bounded connections. `ChildRef` uses `embed`/`ref`,
  which conflates **packaging** (where bytes live) with **composition** (how
  processes are wired).
- **Canonical FBP vocabulary** (J. Paul Morrison): processes; named **ports**;
  **connections as bounded buffers**; **Information Packets** with lifetimes;
  **IIPs**; **subnets** (a network used as a component) with **external ports**;
  and asynchronism + multiple input ports + **back-pressure** as the classical
  litmus test.

## Decision

### 1. Adopt FBP vocabulary precisely

| FBP term | Our artifact |
| --- | --- |
| Process / component | a **component** (`component.v1`), self-contained package |
| Port | a **named inport/outport** on a component |
| Connection (bounded buffer) | an **edge** with a **capacity** in `network.v1` |
| Information Packet (IP) | a typed value flowing along a connection |
| IIP | an **initial IP** bound to an input port in the network document |
| Subnet | a **composite** — a network used as a component, with **external ports** |
| Back-pressure | a full connection suspends its producer (deterministically) |

### 2. Composition, not absorption

- A component at rest is a **self-contained package with no edges**; dependency
  exists **only** in a network document. Dependency = edge; nothing else.
- A **composite is a subnet**: its *body* is a persisted, content-addressed
  network document (the "function body" is data), registered for posterity
  (Law 10). The body may itself contain composites — composition is fractal.
- Children are **referenced**, each its own pinned/signed package (SPEC-0007).
  The `embed`/`ref` distinction is **packaging only** (`vendored` mirror vs
  `pinned`); it never implies the parent owns or absorbs a child. This is an
  additive re-framing; no silent rename.
- **Cross-boundary flow uses only the composite's declared external ports.** An
  interior connection is private; an outer edge that reaches past the boundary is
  rejected fail-closed. This is what preserves encapsulation, reusability, and
  each child's sovereignty.

### 3. ABM: sovereignty is structural

- An **agent** is a component that perceives and acts (SPEC-0002 §0).
- The **parent is the children's shared context** (identity, grants, the network
  document); a **child owns its own state**; siblings never write each other;
  effects propagate **up as verified proposals**.
- Composition adds a level of context; it never overrides a child's interior or
  its state. That is the invariant that lets nesting be safe.

### 4. Determinism and dynamism, reconciled

- **Dynamism:** a network (or a component) may be generated at plan time by a
  human, an agent, or a tool (SPEC-0008).
- **Determinism:** whatever is generated is **frozen and content-hashed before it
  executes**; the deterministic control plane then reproduces the same trajectory
  for the same frozen document (Law 2).
- **Async without losing determinism:** connections are bounded and back-pressure
  is real, but the drain order is driven by the **deterministic CPM scheduler**
  (deterministic IP ordering), not by wall-clock/OS-thread races. True OS-level
  asynchronism stays **declared-gated** until determinism is proven.
- **Non-determinism is never silent:** `model` components are recorded, pinned,
  and confidence-scored (SPEC-0002 §4).

## Scope

- **In:** the FBP vocabulary above; ports + IPs + bounded connections in
  `network.v1`; external ports on composites; composition-by-reference and the
  packaging re-framing; the ABM sovereignty invariants; the determinism/dynamism
  reconciliation; the boundary guard (no cross-boundary bypass).
- **Out:** OS-thread/remote asynchronism as a run-time claim (gated); changing
  Manager orchestration/verification/policy/envelope/accounting semantics;
  adding a backend; changing `PRINCIPLES.md`; any silent (non-additive) contract
  rename.

## Progress

- **Slice 1 (delivered).** `network.v1` carries named **ports** per component and
  a **capacity** per connection, with fail-closed port/capacity validation, a
  deterministic `content_hash()`, and port-aware compilation (port edges compile
  to `wires`; the legacy args-based model is unchanged). Tests:
  `tests/test_cbp_ports.py`.
- **Slice 2 (delivered).** Deterministic Information-Packet flow (`cbp/flow.py`):
  a verified output routes outport→inport over `BoundedConnection`s (FIFO,
  capacity); a full buffer is **back-pressure** and an empty receive is a
  **deadlock** — both fail closed, never hang; `args` act as **IIPs** for unwired
  inports. Port-declared networks auto-route through `run_flow` from `run_network`.
  Tests: `tests/test_cbp_flow.py`.
- **Slice 3 (delivered).** First-class per-port **IIP documents**
  (`tests/test_cbp_iips.py`); the composite **external-ports boundary guard**
  (`tests/test_cbp_composite.py`) — a nested subnet body plus an external-port
  map, rejecting a bypass (a dotted reference, an undeclared port, a mismatched
  interior port, or over-deep nesting) fail-closed; and **repeated-activation
  streaming** (`cbp/stream.py`, `tests/test_cbp_stream.py`) — a component
  re-activates once per input IP, a streaming out-port's iterable yields one IP
  per element, and a full bounded connection deterministically **suspends** the
  producer until a consumer drains it, with deadlocks and runaway sources failing
  closed.
- **Slice 4 (delivered).** Named **typed ports** (`cbp/network.py`): a
  component may declare a value type per named port (`port_types`, reusing the
  hand-off type vocabulary: `str`/`int`/`float`/`bool`/`dict`/`list`/`null`/
  `any`). Edge type compatibility and IIP values are validated fail-closed at
  `validate()`, and both flow engines (`cbp/flow.py`, `cbp/stream.py`) enforce
  the declared type on every Information Packet at run time. An absent type is
  the wildcard `"any"`, so untyped networks are byte-for-byte unchanged
  (serialization and content hash preserved). Tests:
  `tests/test_cbp_typed_ports.py`.
- **Slice 5 (delivered).** **Component state sovereignty**
  (`cbp/component_state.py`): a `StateScope` binds a component to exactly one
  namespace under the shared state root and refuses any descriptor or path
  that escapes it, fail-closed. Boot materializes every declared state through
  its scope, so two components can never resolve to the same file — a child
  owns its own state and siblings never write each other. Tests:
  `tests/test_component_sovereignty.py`.

## Acceptance criteria

- [x] `network.v1` exposes components with **named typed ports**; edges connect
      **port → port**; a connection declares a **capacity**
      (`cbp/network.py` `port_types`, `tests/test_cbp_typed_ports.py`).
- [x] The compiler resolves a network to a **deterministic plan**; an identical
      document yields an identical plan and content hash (`content_hash()`).
- [x] A composite declares **external ports**; a cross-boundary edge that does
      not use them is **rejected fail-closed** (nested subnet body + external
      map; `tests/test_cbp_composite.py`).
- [ ] A component at rest has **no edges**; dependency appears only in a network
      document.
- [x] Bounded connections enforce capacity; a full connection applies
      **deterministic back-pressure**; a deterministic deadlock is an explicit,
      audited failure — never a hang (`cbp/flow.py`, `tests/test_cbp_flow.py`).
- [x] IIPs parametrize input ports at network-definition time: first-class
      per-port IIP documents (`network.v1` `iips`, `tests/test_cbp_iips.py`), with
      component `args` as the fallback template.
- [x] No component writes another's state; effects propagate as verified
      proposals (test-enforced) — `StateScope` mediates per-component
      namespaced state and boot wires through it
      (`tests/test_component_sovereignty.py`).
- [ ] `model` components are recorded and confidence-scored.
- [ ] The convention guard records this spec; no `src/` semantics change lands
      before its acceptance criteria are implemented and green.

## Risks / invariants

- **Async vs determinism.** The main risk is trading Law 2 for FBP asynchrony;
  the clamp is a deterministic scheduler over bounded buffers, with true async
  gated.
- **Back-pressure liveness.** A cyclic wait must fail closed (explicit failure),
  not deadlock silently.
- **Contract churn.** `component.v1`/`network.v1` are additive-only/versioned;
  composition becomes first-class additively (or as a new version), never a
  silent rename.
- **Over-modeling.** Adopt only what is proven; every capability carries a status
  and no claim exceeds the operator's suite.

Respects Laws 1, 2, 3, 4, 5 (amended), 6, 7, 8, 9 (amended), 10, 11, 12, 13.
This is target architecture: it changes no `src/` semantics on acceptance of the
spec alone, and every implementation step is its own reviewed change.
