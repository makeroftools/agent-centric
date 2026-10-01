---
id: SPEC-0013
title: Cross-runtime realization and conformance vectors
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# Cross-runtime realization and conformance vectors

## Context

SPEC-0012 freezes the component ABI. The full version must realize a component in
**any runtime** (Rust, Python, Go, WASM, Node, …). The naive justification —
"every language is Turing complete, so translation holds deterministically" — is
**unsound**: Turing completeness guarantees *computability*, not *equivalence*,
and cross-language translation is not generally deterministic or
semantics-preserving (side effects, partial functions, floating point,
concurrency, host libraries, undefined behavior). Trusting a translation
"because Turing" would create an unverified-success hole.

## Decision

**A component may be realized by one of two paths; both must satisfy the same ABI
and the same conformance vectors.**

### Path 1 — Translation / compilation

The task is translated or compiled into a portable target, canonically **WASM**,
and executed in the host. Translation is a **generation act**: it produces a
**new artifact** that is content-addressed, signed, and **untrusted until it
passes conformance** (SPEC-0015 provenance).

### Path 2 — Native-runtime invocation (dialed-up JIT runtime)

A **native runtime executes a component defined in any other language's runtime**
by using a **language-server protocol** as the execution adapter. The runtime is
**"dialed up" (JIT-provisioned) on demand** for the component's language. No
translation occurs; the component runs where it was defined.

### Equivalence is proven, never assumed

The **shared conformance vectors** are the sole proof of equivalence across paths
and runtimes. They are a **language-agnostic fixture set** — a versioned contract
plus golden cases — covering at least:

- **Pure function semantics** — typed inputs to typed outputs, including edge and
  error cases.
- **Lifecycle** — `init` / `run` / `kill` ordering, pre-init config handling,
  deterministic teardown.
- **Transport** — ZeroMQ channel announcement, ports and tasks over the initial
  hard-coded channels, packet framing.
- **Determinism** — identical vectors yield identical outputs and ordering across
  runs and across runtimes.
- **Capability bounds** — a component cannot exceed its granted capabilities;
  sandbox-escape attempts fail closed.

Any host (Rust, Python, WASM, browser) must pass the vectors to **certify a
runtime**. The same suite certifies that the public **Python reflection** stands
in for the **Rust core** without divergence.

### The runtime is part of the trusted computing base

A dialed-up foreign runtime is **part of the TCB**. Therefore:

- The provisioned runtime is **version-pinned and signed**; a runtime is an
  artifact and is content-addressed in the registry.
- It runs **capability-bounded and fail-closed**.
- Its **identity/version is recorded in the trajectory** — otherwise replay
  drifts when a floating Node/Python/Rust version silently changes behavior.

## Scope

- **In:** the two realization paths; the conformance-vector suite as the
  equivalence proof; TCB pinning and runtime-identity recording; runtime
  certification.
- **Out:** host implementation specifics; the formal-semantics/ontology work
  (SPEC-0017); provenance gates (SPEC-0015); the ABI itself (SPEC-0012).

## Acceptance criteria

- [x] The two realization paths are documented and both are stated to honor the
      SPEC-0012 ABI.
- [x] A language-agnostic conformance-vector suite exists, versioned and
      content-addressed, covering semantics, lifecycle, transport, determinism,
      capability bounds, and the data-plane encodings (`json` / canonical
      `msgpack`).
- [ ] Any runtime that executes a trusted component has passed the suite
      (certification), and its version is pinned, signed, and recorded.
- [ ] Translation output is treated as an untrusted generated artifact until it
      passes the suite.
- [x] The suite certifies cross-runtime equivalence (e.g. Python reflection vs
      Rust core) without appealing to Turing completeness (`pro/cbp-host` passes
      the same 31/31 vectors).

## Progress

- **Layer 0 (delivered).** The language-agnostic conformance suite is frozen in
  `conformance/vectors/` (`fixtures.v1.json` + `suite.v1.json`), content-addressed
  by `vectors.lock.v1.json` and bound to the ABI `contract_sha256`. It covers the
  five categories (semantics, lifecycle, transport, determinism, capability).
  `conformance/certifier/` runs it against a host over the
  `cbp.conformance-host.v1` protocol and records the pinned runtime identity; the
  reference host passes 31/31. An `encoding` category proves canonical `json`
  and pinned-canonical `msgpack` bytes, cross-encoding equivalence, and strict
  rejection of non-canonical input. The Rust host (`../pro`, `cbp-host`) passes
  the same suite **31/31**, so cross-runtime equivalence (Python reflection vs
  Rust core) is now proven. Layer 1b added content-addressed WASM execution
  (`../pro`, feature `wasm`): after verifying an artifact's bytes against the
  declared sha256 (refusing a tampered artifact), the host compiles it as a WASM
  component and drives `init`/`run`/`kill`; the WASM suite
  (`conformance/vectors/wasm-suite.v1.json`) passes **4/4**. A full
  `component-abi.v1` WASM guest and artifact signing are open (Layer 1c).

## Risks / invariants

- Respects Laws 1, 2, 8, 11; no unverified success.
- The verifier, compiler, translator, and runtime are part of the TCB: unpinned
  toolchains make a "pass" non-reproducible and are rejected.
- Additive-only; no `src/` semantics change on acceptance of the spec alone.
