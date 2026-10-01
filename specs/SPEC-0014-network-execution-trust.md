---
id: SPEC-0014
title: Network execution and trust — static or generated wiring, pinned before running
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Network execution and trust — static or generated wiring, pinned before running

## Context

SPEC-0012 makes ports **dynamic endpoints** and leaves components edgeless at
rest. The current `network.v1` is a *static* typed-port document with a content
hash and static edge-type checks (SPEC-0009). The full version generalizes this:
**the network is a layer orthogonal to the components it comprises**, and it may
be **static or dynamically generated**. That raises the mission-critical question
of what is verified, and when, when the wiring is not known until just before
execution.

## Decision

**The network is independent of the components it comprises.** A component never
knows its wiring; the network document does. This aligns with "a component at
rest has no edges."

**A network document may be static or dynamically generated.** Static networks
are authored and hashed as today. Dynamic networks are produced **just before
execution** — by a signed planner component and/or an autonomous agent.

**Hard invariant — execution only ever runs a content-addressed network.** If the
network was generated, the generated network is **content-hashed, pinned, and
recorded before it runs**. This gives two admissible modes:

- **Deterministic planner** — a signed planner component generates the network
  from signed inputs; the plan is content-addressed; replay re-runs the planner
  and reconstructs byte-identical wiring.
- **Autonomy proposes, the pin disposes** — an autonomous (possibly
  model-mediated) agent proposes the network; the **pinned plan** is the trust
  anchor; replay uses the frozen plan rather than regenerating it.

In both modes the **network generator's output must be deterministic once
pinned**, and **every edge is contract-checked at instantiation**, even when it
is dynamic.

**Goal — deterministic autonomy in a dynamic environment**, to the extent it can
be implemented cleanly and safely. Non-determinism is permitted in *generation*
only if the generated artifact is pinned before it executes; non-determinism is
never permitted in *execution* of a claimed-verified result.

## Scope

- **In:** the network-as-orthogonal-layer model; static vs generated networks;
  the pin-before-run invariant; the deterministic-planner and
  propose-then-pin modes; per-edge contract checks at instantiation; trajectory
  recording and replay of the network.
- **Out:** the component ABI (SPEC-0012); provenance classes and trust gates
  (SPEC-0015); editions (SPEC-0016); the isolated holdout validator (L3,
  SPEC-0017).

## Acceptance criteria

- [ ] A network may be static or generated, and both execute only after the
      network is content-addressed.
- [ ] A generated network is pinned and recorded **before** execution; replay
      reproduces identical wiring (planner re-run or pinned plan).
- [x] Every edge, static or dynamic, is contract-checked at instantiation and
      fails closed on mismatch.
- [x] The generator's output hash is part of the recorded trajectory.
- [x] No result is claimed verified from a network that was never pinned.

## Progress

- **Layer 2 (static `network.v1`) delivered.** `conformance/contracts/network-v1.md`
  freezes the document, the pin rule (canonical-JSON content address computed with
  `content_hash` removed), typed enforcement at instantiation, and the trajectory.
  The executable contract is `vectors/network-{fixtures,suite,lock}.v1.json`
  (9 cases: identity chain, fan-in, msgpack, replay determinism, and
  type-mismatch/cycle/not-pinned/pin-mismatch/unknown-fixture refusals). It is
  implemented by the Python reference host and the Rust host and passes **9/9** on
  both (`certifier/certify.py`), proving cross-runtime equivalence.
- **Open (generated mode).** A deterministic planner component and the
  propose-then-pin path are not built; the pinned-before-run invariant and the
  typed/replayable execution they require are.

## Risks / invariants

- Respects Laws 1, 2, 8; no unverified success.
- A live-wired, never-pinned network is **not** an admissible trust model for a
  verified result and is rejected.
- Replay must be side-effect free with respect to the original trajectory.
- Additive-only; no `src/` semantics change on acceptance of the spec alone.
