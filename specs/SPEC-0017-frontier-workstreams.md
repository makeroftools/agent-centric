---
id: SPEC-0017
title: Frontier workstreams — formal semantics, discovery, verification, and RSI
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Frontier workstreams — formal semantics, discovery, verification, and RSI

## Context

The MVP planning session deferred a set of large, deliberately open workstreams so
they do not block the two-week full-version launch. This spec holds them as a
single parked index: each is named, scoped, and marked *parked*, so the intent is
preserved ("go back a spec or two to resurrect intent") without coupling it to
the launch.

## Decision

The following workstreams are **parked**. None changes `src/` semantics on
acceptance of this spec. Each is to be promoted to its own spec (and, where
noted, its own planning session) before implementation.

### 1. Formal semantics, ontology, and closed shapes (dedicated session)

The deterministic formalizer at the model boundary (SPEC-0002 §4) grows into a
real formal-semantics layer: an **ontology** (TBox) as a versioned, signed,
content-addressed contract; a **semantic graph** (ABox) as component-owned state;
**closed shapes (SHACL-style)** as deterministic fail-closed gates; and a
**pinned entailment closure** (identical graph + ontology + rule profile yields
an identical closure hash). Full OWL DL is out; a decidable subset lands first.
This absorbs the confidence/`review.v1` richness (a minimal deterministic scorer
ships first; thresholds and the persistent append-only `review.v1` queue mature
here). Uses context-sensitive grammars; **WASM is a candidate execution vehicle
for them** (research thread).

### 2. Program reduction, analysis, and formal verification

**Full reduction** (slicing / dead-code elimination / dependency minimization)
shrinks discovered code to snippets and removes hidden behavior. Static/dynamic
analysis (type/effect/capability) and **formal verification (Z3/SMT)** prove
scoped properties for decidable fragments. Output is an **Assurance Label**
(SPEC-0015 A0-A4) with its evidence. Reduction and translation are *generation*
acts: their output is untrusted until re-verified.

### 3. Universal Function Index / dynamic discovery

An **index of existing functionality** (repos, directories, binaries) that can be
resolved on demand and wrapped into components. "All components already exist, if
we grab and shape them" is the dream; the reality is **grab → understand → wrap →
verify**. Candidate-only: every artifact is wrapped, content-addressed,
sandboxed, conformance-checked, and `review.v1`-reviewed before use; license and
provenance are recorded. **Curated allowlist first**; open crawl only once the
isolated validator (L3) exists. Ties to native-runtime (language-server)
invocation, which is the easiest way to execute discovered code where it lives.

### 4. Isolated holdout validator (L3)

The declared-gated program that would let verification, not generation, be the
constraint on autonomy. Required before any auto-labeling at scale and before any
self-improvement of verification.

Promoted to its own spec: [SPEC-0019](SPEC-0019-isolated-holdout-validator.md) (draft; spec only, not
implemented).

### 5. Bounded RSI

Propose → review → sign → lock. Verification remains off-limits until the
isolated validator exists. Unbounded/autopoietic self-modification stays out.

### 6. Whitepaper

Written **last**, after the design is fully thought through. The Core repo
provides the evidence; the prose is authored by the operator.

## Scope

- **In:** a single parked index recording each workstream, its scope, and its
  gating dependencies.
- **Out:** any implementation; any change to `PRINCIPLES.md`; any change to
  Manager orchestration/verification/policy/envelope/accounting semantics; any
  change to `src/`.

## Acceptance criteria

- [ ] Each parked workstream is named, scoped, and marked parked.
- [ ] The gating dependencies are recorded (L3 validator before auto-labeling and
      verification self-improvement; ontology change is constitutional-level).
- [ ] No workstream is claimed as in-launch.
- [ ] The "resurrect intent" process is recorded: on blockage, step back one or
      two specs rather than forcing a workaround.

## Risks / invariants

- Respects Laws 1-13; changes nothing on acceptance.
- Discovery and generation must never be auto-trusted; the SPEC-0015 gates apply.
- Keeping these parked is what keeps the two-week launch achievable.
