---
id: SPEC-0010
title: Extensibility — knowledge graphs (ontology over semantic graphs) and bounded recursive self-improvement
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Extensibility — knowledge graphs and bounded recursive self-improvement

> The dedicated-session design for the semantic layer is drafted in
> [`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md) (`ontology.v1`,
> `semantic-graph.v1`, closed shapes, and the pinned entailment closure).
>
> The dedicated-session design for the learning / bounded-RSI frontier is
> drafted in [`SPEC-0024`](SPEC-0024-learning-bounded-rsi.md) (the proposal
> loop, pinned bounds, `learning.proposal/v1` / `learning.bounds/v1`, and the
> post-L3 gate).

**Operator direction (recorded).** The architecture will eventually include
**knowledge graphs** with an **ontology mapped on top of semantic graphs**, and
**recursive self-improvement** (RL / active inference). This spec records the
seams and the gates so future work composes instead of inventing scope. It is
target architecture with statuses; no claim exceeds what the operator's suite
proves.

## Context

- [`SPEC-0002`](SPEC-0002-cbp-component-architecture.md): everything is a
  component; `network.v1` is the execution graph; models are quarantined
  non-determinism with `confidence` and a `review.v1` queue.
- [`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md): components
  are self-contained, independently versioned, signed, content-addressed.
- [`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md): ports, IPs, bounded
  connections; subnet composition; parent-as-context / child-owns-state.
- The passive **registry** (Law 10) is already a proto-knowledge-graph; per
  component **state** is already local, single-writer SQLite.

## Decision

### 0. Three graphs, kept distinct

- **Process graph** (`network.v1`) — FBP control/data-flow (ports, connections).
- **Semantic graph** — knowledge: subject/predicate/object assertions.
- **Design graph** (`design.v1`) — authoring: references + requested pins.

They compose **through components**, never by being conflated. A knowledge graph
is *a component* whose state is the semantic graph.

### 1. Ontology over semantic graphs

| Semantic layer | Our artifact |
| --- | --- |
| Ontology (TBox: classes, properties, axioms) | a **versioned, signed, content-addressed contract**; shared context |
| Instance triples (ABox) | a component's **state** (single-writer SQLite, WAL, integrity-checked) |
| Shapes / constraints | **verifiers/gates** (deterministic, fail-closed) |
| Entailment / materialization | a **pinned deterministic closure** (content-hashed) |
| Non-deterministic inference (embeddings, LLM linking) | a **`model` component**, recorded + confidence-scored |
| Identity | component identity (`name` + `commit_sha` + `tree_sha256`) → IRIs; provenance |

- **The ontology is a contract**: additive, versioned, signed. Changing it changes
  *meaning* and *verification*, so it is **constitutional-level** (spec + review),
  exactly like a lock change (SPEC-0007 §7.4) — never a runtime side effect.
- **Reasoning is pinned and decidable**: materialize an explicit entailment
  closure from a fixed rule profile; content-hash it. Full OWL DL is out
  (expensive/undecidable); a decidable subset lands first.
- **Sovereignty**: ontology is shared context (parent-owned); instance data is
  child-owned; cross-graph writes are mediated proposals (SPEC-0002 §2).

### 2. The open-world / fail-closed clamp

Ontology reasoning is **open-world** (absence ≠ falsity); our gates are
**closed** (fail-closed on absence). The clamp:

1. open-world inference may **derive** facts for use, but
2. an operational **gate** is satisfied only by a **closed shape** (SHACL-style)
   over the pinned entailment closure — **never by an unproven entailment**.

This keeps the correctness spine intact while allowing open-world reasoning as a
non-authoritative aid.

### 3. Bounded recursive self-improvement (RL / active inference)

- **Learning is quarantined.** Policy learning, exploration, and active-inference
  updates live only in **model/training components**; the deterministic control
  plane (Law 2) never depends on an unreproducible update.
- **Every update is recorded and pinned.** To replay, the applied policy must be
  reproducible from a **pinned artifact**; hidden learner state is either
  recorded or excluded from the control plane.
- **Self-improvement proposes; it does not promote.** An improvement is a new
  signed, content-addressed component/network with provenance. Promotion to the
  trusted set is **gated**: proposal → review → sign → lock (SPEC-0007 §6/§7.4).
- **It must not touch verification or the constitution.** Improving verifiers is
  the recursive case ("who verifies the improver?"); it is **declared-gated** and
  requires the isolated holdout validator (L3,
  [`.agentfactory.toml`](../.agentfactory.toml)), which does not exist yet.
- **Active inference**, specifically: the generative model and observations are
  pinned; prediction error / expected free energy are **recorded signals** that
  feed `confidence` and `review.v1` — not silent control inputs.
- **Reward-gaming is clamped** by immutable evidence (Law 10) and **external
  holdout** evaluation; a metric-satisfying but wrong improvement must fail the
  holdout.

### 4. Determinism ⊗ dynamism

Generation/learning is dynamic; **whatever is generated or learned is frozen and
content-hashed before it executes**. Identical frozen artifacts replay to
identical trajectories (Law 2). Non-determinism is recorded, never relied upon.

## Status

| Capability | Status |
| --- | --- |
| Three-graph separation; ontology-as-contract; shapes-as-gates | **roadmap** |
| Semantic-graph component over local SQLite state | **roadmap** |
| Decidable entailment closure (pinned) | **roadmap** |
| Non-deterministic inference as recorded `model` components | **roadmap** |
| Bounded RSI (propose → review → sign → lock) | **roadmap** |
| Self-improvement of verification (isolated validator) | **declared-gated** |
| Unbounded / autopoietic self-modification | **out** |

## Scope

- **In:** the three-graph separation; ontology as a versioned contract; the
  semantic-graph component; shapes as gates; the OWA/fail-closed clamp; bounded
  RSI and its gates; the determinism/dynamism rule.
- **Out (non-goals):** unbounded self-modification; auto-promotion to the trusted
  lock; any change to `PRINCIPLES.md`; changing Manager
  orchestration/verification/policy/envelope/accounting semantics; self-editing
  of verification; a new execution backend; cloud/network at run time.

## Acceptance criteria

- [ ] The three graphs are distinct and documented; a semantic graph is a
      component over local single-writer state.
- [ ] An ontology is a versioned, signed, content-addressed contract; an ontology
      change is gated (spec + review) and additive.
- [ ] A gate is satisfied **only** by a closed shape over a pinned entailment
      closure — never by an unproven open-world entailment (test-enforced).
- [ ] Entailment materialization is deterministic: identical graph + ontology +
      rule profile ⇒ identical closure hash.
- [ ] Non-deterministic inference is a recorded `model` component with
      `confidence`, enqueued to `review.v1` when sub-threshold.
- [ ] An RSI improvement cannot enter the trusted set without review + signature +
      a lock change; it cannot modify verifiers without the isolated validator.
- [ ] The convention guard records this spec.

## Risks / invariants

- **Verifier recursion.** Who verifies the improver? Clamp: gated promotion + the
  isolated holdout validator; verification is off-limits until it exists.
- **Reward-gaming.** Clamp: immutable evidence + external holdout.
- **OWA softening the spine.** Clamp: open-world for inference only; closed
  shapes for gates.
- **Ontology drift.** Clamp: versioned/signed contracts; additive-only.
- **Determinism loss.** Clamp: learning quarantined; frozen-before-execute;
  recorded non-determinism.

Respects Laws 1, 2, 3, 4, 5 (amended), 6, 7, 8, 10, 11, 12, 13. Target
architecture: it changes no `src/` semantics on acceptance of the spec alone.
