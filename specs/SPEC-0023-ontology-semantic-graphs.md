---
id: SPEC-0023
title: Ontology and semantic graphs — ontology.v1, closed shapes, and the pinned entailment closure
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Ontology and semantic graphs — `ontology.v1`, closed shapes, and the pinned entailment closure

> **Status: draft design spec; no design freeze, no implementation.** This is the
> deliverable the [Brain study charter](../docs/agent/brain-charter.md) calls for
> (the dedicated-session output). Every choice below is marked **PROPOSED** until
> the operator ratifies it; the rule profile in §2 is the first real decision and
> is deliberately left open. This spec changes no `src/` semantics on acceptance
> of the spec alone.

## Context

The system already separates **three graphs** ([`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md)
§0): the **process graph** (`network.v1`), the **design graph** (`design.v1`), and
the **semantic graph** (knowledge). It already makes a parent the **shared context**
of its children: `cbp/context.py` narrows an immutable hierarchy (`domain`,
`rules`, `verifier`) downward; `cbp/component_catalog.py` is an explicit,
**parent-provisioned** passive catalog (Law 10); `cbp/network.py` scopes a
composite's children with a `subnet` body and declared `external` ports, rejecting
cross-boundary flow fail-closed ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
§2–§3). And the projection family — `cbp/diagram.py`, the `ui.v1` targets, the
bills calendar, the n8n adapter — already demonstrates **pure, deterministic,
content-addressed, bounded, read-only projections** of a graph
(`MAX_DIAGRAM_NODES`, canonical-JSON `content_hash`).

The open question this spec answers: **can CBP carry ontological knowledge, and can
a parent hold the ontological subgraph of its children?** Yes — provided it is a
**derived, pinned, recomputable closure** over child-published facts, never
ownership, duplication, or mutation of child state. This formalizes the intent of
[`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md) and its verification
sibling [`SPEC-0017`](SPEC-0017-frontier-workstreams.md) workstream 1; it is a
constitutional-level change because it defines *meaning*.

## Decision

### 0. Three graphs stay distinct

- **Process graph** (`network.v1`) — FBP control/data-flow (ports, connections).
- **Semantic graph** — knowledge: subject/predicate/object assertions.
- **Design graph** (`design.v1`) — authoring: references + requested pins.

They compose **through components**, never by being conflated. A knowledge graph is
*a component* whose state is the ABox.

### 1. Contracts (additive, versioned, signed, content-addressed)

| Contract | Role |
| --- | --- |
| `ontology.v1` | **TBox** — classes, properties, axioms, and the declared rule profile + shape references. Shared context; parent-provisioned. |
| `semantic-graph.v1` | **ABox** — the instance triples. Component-owned state (single-writer SQLite, WAL, integrity-checked). |
| `shapes.v1` | **Closed shapes** (SHACL-style) used as deterministic, fail-closed gates. |
| `closure.v1` | A **pinned materialized entailment closure**: `{graph hash, ontology hash, rule-profile id}` → `closure hash`. |

- **Identity → IRIs:** component identity (`name` + `commit_sha` + `tree_sha256`,
  [`contracts/component.py`](../src/agent_centric/contracts/component.py)) maps to
  stable IRIs under a fixed base (`<urn:cbp:>` **PROPOSED**); provenance is carried
  per fact.
- **The ontology is a contract.** Changing it changes meaning and verification, so
  it is **constitutional-level** (spec + review), exactly like a lock change
  ([`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) §7.4) — never a
  runtime side effect. Once frozen it is **additive-only**.

### 2. Rule profile (**PROPOSED** — operator decision #1)

- **Core:** positive **Datalog + stratified negation** (semi-naive fixpoint).
- **Gates:** **SHACL** shapes over the pinned closure.
- **Optional named profile:** **OWL 2 RL**, offered as a profile id, *not* the
  foundation. Full **OWL DL is out** (expense/decidability).
- **Evaluation:** least fixpoint, stratified negation, a total order over rules,
  and a **canonical sort** of the result set — so evaluation order never affects
  the content address (essential for cross-runtime parity).
- Rationale: decidable, deterministic, set-semantic, and cheap to reimplement
  byte-identically in the Rust host (the `pro` edition).

### 3. Parent-subgraph semantics (the core question)

- The **parent owns the shared TBox fragment** and provisions it as context, the
  same way it provisions a catalog today.
- The **child owns its ABox** (single-writer); siblings never write each other.
- The parent holds a **derived closure** over `{child identity → child graph
  content address}` + its ontology + rule profile. It never stores or mutates a
  child's raw state.
- Facts rise **only as verified proposals** ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
  §3); the parent incorporates them **by content reference**, not by absorbing
  state. The parent re-verifies.
- Roll-up crosses a boundary **only through declared external ports** (the
  boundary guard); interior stays private.
- **Fractal and bounded:** a composite's closure is itself an ABox fragment that
  can be published to *its* parent, bounded by nesting depth and closure size
  (mirroring `_MAX_SUBNET_DEPTH` / `MAX_DIAGRAM_NODES`).

### 4. The OWA / fail-closed clamp

Reasoning is open-world (absence ≠ falsity); gates are closed (fail-closed). The
clamp: open-world inference **may derive** facts for use, but an operational
**gate is satisfied only by a closed shape over the pinned entailment closure** —
**never by an unproven entailment** ([`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md) §2).

### 5. Content addressing and canonicalization (the sharp risk)

A graph containing blank nodes has **no canonical byte form**, so its closure hash
is nondeterministic across runtimes. **PROPOSED: blank nodes are disallowed at the
contract boundary** (skolemize to IRIs), which makes canonical serialization
trivial and cross-runtime parity realistic. If blank nodes are ever admitted, the
W3C **RDF Dataset Canonicalization (RDFC-1.0)** algorithm is required. This is
load-bearing and must be settled before vectors are authored.

### 6. Determinism ⊗ non-determinism

The **deterministic closure is control-plane** and conformance-certified
([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md)). Non-deterministic
inference (embeddings, LLM linking) is a **quarantined `model` component** with
`confidence`, enqueued to `review.v1` when sub-threshold — never a silent control
input (Law 2; [`SPEC-0002`](SPEC-0002-cbp-component-architecture.md) §4).

### 7. Conformance seam

The deterministic closure gets a cross-runtime suite (Python ⇄ Rust), exactly like
`network.v1`/`ui.v1`: vectors map `(graph, ontology, rule profile)` → `closure
hash` + selected entailments. Identical inputs ⇒ identical closure hash. This is
the sole proof the editions share one semantics ([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md),
[`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)).

### 8. Distribution & confidentiality (**OPEN** — operator raised "secret sauce")

The operator may want to keep the ontology out of the public `core`. Constraint
([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)): editions differ only in
**capacity / performance / deployment / governance / support / UI** — **never in
semantics**. Any edition that ships the ontology must ship the *same* semantics and
pass the *same* conformance suite. "Secret sauce" may live only in implementation
internals (indexing, performance, curation of the shipped vocabulary) — never in
entailment meaning. Candidate shapes:

- **(a) Public contracts, private runtime/content** — `ontology.v1` etc. in
  `core`; the concrete ontology and the tuned runtime in `pro`/`infra`.
- **(b) Private end-to-end** — then the public certifier cannot independently
  certify it against a reference host.
- **(c) Public contracts + operator-private ontology content** — semantics public,
  the specific vocabulary private.

**Recommended: (a) or (c).** Decision deferred to the operator; recorded here so
the seam is designed to allow it.

## Status

| Capability | Status |
| --- | --- |
| Three-graph separation; ontology-as-contract; shapes-as-gates | **roadmap** |
| `ontology.v1` / `semantic-graph.v1` / `shapes.v1` / `closure.v1` contracts | **roadmap** |
| Semantic-graph component over local single-writer state | **roadmap** |
| Parent-held **derived** subgraph closure | **roadmap** |
| Decidable closure (Datalog + stratified negation) + SHACL gates | **roadmap** |
| Cross-runtime conformance suite for the closure | **roadmap** |
| Non-deterministic inference as recorded `model` components | **roadmap** |
| Full OWL DL | **out** |
| Autonomous / non-additive ontology mutation | **out** |
| Parent owning or mutating child ABox | **out** |

## Scope

- **In:** the three-graph separation; `ontology.v1` / `semantic-graph.v1` /
  `shapes.v1` / `closure.v1`; ontology-as-contract; parent-held derived closure;
  the OWA/fail-closed clamp; content-addressing + blank-node policy; per-fact
  provenance + confidence; the cross-runtime closure suite; the
  distribution/confidentiality seam.
- **Out (non-goals):** full OWL DL; unbounded/autopoietic ontology mutation; any
  change to `PRINCIPLES.md`; changing Manager orchestration/verification/policy/
  envelope/accounting semantics; self-editing of verification; a new execution
  backend; cloud/network at run time; **any implementation before ratification**.

## Acceptance criteria

- [ ] A semantic graph is a component over local **single-writer** state.
- [ ] An ontology is a versioned, signed, content-addressed, **additive** contract;
      an ontology change is gated (spec + review).
- [ ] A gate passes **only** on a closed shape over a **pinned** entailment closure
      — never on an unproven open-world entailment (test-enforced).
- [ ] Identical `(graph, ontology, rule profile)` ⇒ **identical closure hash**,
      cross-runtime (Python ⇄ Rust).
- [ ] A parent's held subgraph is a **derived closure keyed by child content
      addresses**: recomputable after a child change, and containing **no copy** of
      child ABox state (test-enforced).
- [ ] Upward facts enter the parent only as **verified proposals**; the parent
      re-verifies.
- [ ] Non-deterministic inference is a recorded `model` component with
      `confidence`, enqueued to `review.v1` when sub-threshold.
- [ ] Two editions compute the **same closure hash** for the same inputs
      (`SPEC-0016` invariant).
- [ ] The convention guard records this spec.

## Risks / invariants

- **Graph canonicalization.** Blank nodes have no canonical form. Clamp: disallow
  blank nodes at the boundary (or adopt RDFC-1.0). §5.
- **Sovereignty.** The parent must not own/absorb child state. Clamp: derived
  closure by content reference; children remain single-writer (§3; SPEC-0009 §3).
- **Determinism loss.** Clamp: the closure is a pure function of pinned inputs and
  is content-hashed; non-deterministic inference is quarantined (§6; Law 2).
- **OWA softening the spine.** Clamp: open-world derives; gates need closed shapes
  (§4).
- **Closure blow-up.** Clamp: stratified, bounded, per-composite closure;
  lazy-compute / pin-on-use; a size ceiling like `MAX_DIAGRAM_NODES`.
- **Ontology drift.** Clamp: versioned/signed, additive-only contracts (§1).
- **Ambient authority.** Clamp: the held subgraph is evidence + projection, never a
  decider (Law 10; `brain-charter.md` Q6).
- **Cross-runtime divergence.** Clamp: set-semantics + canonical sort; a shared
  conformance suite is the sole proof (§7; SPEC-0013).
- **Confidentiality vs one semantics.** Clamp: private only in
  capacity/perf/curation, never meaning (§8; SPEC-0016).

Respects Laws 1, 2, 3, 4, 5 (amended), 6, 7, 8, 10, 11, 12, 13. Target
architecture: it changes no `src/` semantics on acceptance of the spec alone.

## Open decisions (operator)

| # | Decision | Default proposed |
| --- | --- | --- |
| 1 | Rule profile | Datalog + stratified negation core; SHACL; OWL-RL optional |
| 2 | Triple model | RDF 1.1 (Turtle / N-Triples) |
| 3 | Blank nodes | Disallowed at the boundary (skolemize) |
| 4 | Provenance on assertions | RDF 1.2 / RDF-star triple terms, else a reification graph |
| 5 | Closure scope | Per-composite, bounded, lazy-compute / pin-on-use |
| 6 | Ambition | Deterministic closure certified; linking quarantined in `model` |
| 7 | First use case | **needs operator input** — the minimum useful automation |
| 8 | Confidentiality | (a) or (c) in §8 |

## Next step

The operator ratifies decisions #1–#8 (a **grilling session** is the intended
venue). Then a focused session authors the conformance vectors and the
`ontology.v1`/`closure.v1` schemas — still no implementation. Only after that does
an implementation plan open, below the gate.
