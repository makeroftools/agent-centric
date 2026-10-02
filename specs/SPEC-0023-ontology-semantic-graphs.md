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

> **Status: draft design spec.** The dedicated-session output the
> [Brain charter](../docs/agent/brain-charter.md) calls for. Its **design
> decisions are ratified** (the operator delegated them in a recorded grilling
> session — see [Decisions log](#decisions-log-grilling-session)); the remaining
> [open frontier](#open-frontier) is listed at the end. **No implementation**
> lands before the operator confirms shared understanding. This spec changes no
> `src/` semantics on acceptance of the spec alone.

## Context

The system already separates **three graphs** ([`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md)
§0): the **process graph** (`network.v1`), the **design graph** (`design.v1`), and
the **semantic graph** (knowledge). It already makes a parent the **shared
context** of its children: `cbp/context.py` narrows an immutable hierarchy
downward; `cbp/component_catalog.py` is an explicit, **parent-provisioned**
passive catalog (Law 10); `cbp/network.py` scopes a composite's children with a
`subnet` body and declared `external` ports, rejecting cross-boundary flow
fail-closed ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md) §2–§3). And the
projection family — `cbp/diagram.py`, the `ui.v1` targets — already demonstrates
**pure, deterministic, content-addressed, bounded, read-only projections** of a
graph.

The question this spec answers: **can CBP carry ontological knowledge, and can a
parent hold the ontological subgraph of its children?** Yes — as a **derived,
pinned, recomputable closure** over child-published facts, never ownership,
duplication, or mutation of child state. This formalizes
[`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md) and its
verification sibling [`SPEC-0017`](SPEC-0017-frontier-workstreams.md) workstream
1; it is constitutional-level because it defines *meaning*.

**House constraints found in the environment** (they drive the choice below):
the entire type universe today is **8 scalars** plus ports/edges and kind tags
(`contracts/handoff.py`, `cbp/network.py`); the certifier is **generic** (a new
category needs a reference-host branch + vectors + `compare()` fields +
`certify.sh` wiring); the Rust host **hand-rolls** its graph code and has **no
RDF/OWL/SHACL/Datalog dependency** — no such library exists anywhere in the
monorepo. Cross-runtime byte-identical parity ([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md))
is therefore the dominant constraint.

## Decision

### 0. Three graphs stay distinct

- **Process graph** (`network.v1`) — FBP control/data-flow (ports, connections).
- **Semantic graph** — knowledge: subject/predicate/object assertions.
- **Design graph** (`design.v1`) — authoring: references + requested pins.

They compose **through components**, never by being conflated. A knowledge graph
is *a component* whose state is the ABox.

### 1. Contracts (additive, versioned, signed, content-addressed)

| Contract | Role |
| --- | --- |
| `ontology.v1` | **TBox** — declared classes/properties, the pinned rule set, imports, and shape references. Composable; shared context; parent-provisioned. |
| `semantic-graph.v1` | **ABox** — the assertions. In M1 it is a **derived projection** (§9); component-owned state. |
| `shapes.v1` | **Closed shapes** used as deterministic, fail-closed gates. |
| `closure.v1` | A **pinned materialized entailment closure**: `{graph hash, ontology hash, rule-profile id}` → `closure hash`. |

Contracts are versioned, signed, content-addressed, and **additive-only**.
Changing an ontology changes meaning and verification, so it is
**constitutional-level** (spec + review), exactly like a lock change
([`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) §7.4).

### 2. Representation — bespoke canonical assertion model (**RATIFIED**)

- An **assertion** is `{s, p, o}` canonical JSON; a **term** is a `urn:cbp:` name.
- **No blank nodes.** Every term is named, so the graph always has a canonical
  byte form.
- **Literals** come from the existing type universe (`str,int,float,bool,dict,
  list,null,any`; `contracts/handoff.py`) extended by declared classes and
  properties.
- **Canonical ordering and hash:** assertions are sorted by `(s,p,o)` and hashed
  with `sha256(canonical_json_bytes(...))`, reusing `cbp/network.py`'s canonical
  JSON so the whole system canonicalizes one way.
- **No external RDF/OWL/Datalog dependency.** An optional one-way RDF/Turtle
  *export* adapter may be added later if external interop is ever required; it is
  out of scope here.

### 3. Term identity and namespacing (**RATIFIED**)

- Base namespace `urn:cbp:core/`; each fragment gets `urn:cbp:<fragment>/`.
- A **fragment id** is its declared `name+version`, or a short content hash.
- **Declaration-before-use, fail-closed:** an undeclared term is an error,
  mirroring `Registry.resolve`/`ComponentCatalog` (Law 10).
- Terms are **additive-only**: a term's meaning never changes; deprecation is an
  annotation, never a redefinition.

### 4. Ontology composition (**RATIFIED**)

Ontologies are **composable fragments with explicit imports**: a base ontology,
plus each composite may add terms and import its parent's; the parent assembles
its subtree's TBox. This is fractal, like subnets ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
§2), and is the prerequisite for the parent-held subgraph (§6). There is no
single global ontology and no flat namespace.

### 5. Rule profile (**RATIFIED**)

- **Positive Datalog + stratified negation.** The rules are a small set
  **explicitly enumerated and pinned in `ontology.v1`**; no OWL import and no rule
  outside the pinned set.
- Evaluation is a **semi-naive least fixpoint**; stratified negation; the result
  is **canonical-sorted**, so evaluation order never affects the content address.
- The closure is **bounded** by an explicit ceiling (mirroring
  `MAX_DIAGRAM_NODES` in `cbp/diagram.py`), refusing oversized materialization
  fail-closed.
- Decidable, deterministic, and cheap to reimplement identically in the Rust host.

### 6. Parent-subgraph semantics (the core question)

- The **parent owns the shared TBox fragment** and provisions it as context, as
  it provisions a catalog today.
- The **child owns its ABox** (single-writer); siblings never write each other.
- The parent holds a **derived closure** over `{child identity → child graph
  content address}` + its ontology + rule profile. It never stores or mutates a
  child's raw state.
- Facts rise **only as verified proposals** ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
  §3); the parent incorporates them **by content reference** and re-verifies.
- Roll-up crosses a boundary **only through declared external ports**; interior
  stays private. Composition is **fractal and bounded** (nesting depth + closure
  ceiling).

### 7. The OWA / fail-closed clamp

Reasoning is open-world (absence ≠ falsity); gates are closed (fail-closed). The
clamp: open-world inference **may derive** facts for use, but an operational
**gate is satisfied only by a closed shape over the pinned entailment closure** —
**never by an unproven entailment** ([`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md)
§2).

### 8. Shapes (gates)

Shapes are **bespoke deterministic validators** over the pinned closure (not
SHACL, since the model is bespoke). A gate is satisfied only by a closed shape
over the pinned closure. Shape expression syntax is on the [open frontier](#open-frontier).

### 9. Assertion source — pure projection (**RATIFIED**)

M1's assertions are **derived by a pure projection** from existing
content-addressed contracts — `component.v1` manifests, `Capability`,
`port_types`, `ToolDescriptor` — exactly as `cbp/diagram.py` projects a network.
The graph is a deterministic, content-addressed **view**, never a second source
of truth (Law 10). Authored/instance facts are an M3 concern (§Delivery stages).

### 10. Storage (**RATIFIED**)

- Per-component **SQLite** state, via `cbp/component_state.py` (WAL,
  `single_writer`, `integrity_check`, atomic publish, namespace sovereignty).
- Tables: `assertions(s,p,o,graph)`, `assertion_meta(assertion_id, source,
  confidence, content_addr)`, `imports(graph, imported, ontology_hash)`, and a
  **disposable** `closure(...)` cache.
- **Contracts and content hashes live outside the DB.** A graph's hash is
  computed from canonical assertion records, never the DB file (SQLite does not
  promise byte-identical files — `component_state.py`). Queries always
  `ORDER BY` a canonical key.
- Optional (recommended for Law-10 alignment): an **append-only canonical
  assertion log** is the source of truth, with SQLite a derived index/closure
  cache.
- The Rust host need not use SQLite at all — it certifies the canonical bytes;
  SQLite is a core-side persistence convenience.

### 11. Determinism ⊗ non-determinism (**RATIFIED**)

M1/M2 contain **no model components**. The deterministic closure is control-plane
and conformance-certified. Any future fuzzy linking is a separate, recorded
`model` component with `confidence`, enqueued to `review.v1` — out of scope here.

### 12. Distribution (**RATIFIED**)

The ontology layer is **public and implemented in both `core` and `pro`**, with
one semantics proven by the shared conformance suite. There is **no "secret
sauce"**: "secret" is limited to capacity/performance/deployment/governance/
support/UI, never meaning ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)).

### 13. Conformance seam

A new certifier category is added for the vocabulary projection (M1) and the
closure (M2), following the existing mechanics: a `ReferenceHost.run_case` branch
(+ the Rust `run_case` branch), `vectors/*-{fixtures,suite,lock}.json`, any new
`compare()` observation fields, and a `certify.sh` invocation. Identical inputs ⇒
identical content/closure hash, cross-runtime ([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md)).

## Delivery stages

- **M1 — vocabulary + closed shapes + capability discovery & composition.** Pure
  projection (§9); no entailment. The first useful automation.
- **M2 — pinned entailment closure.** Datalog + stratified negation (§5) and the
  parent-held derived subgraph (§6).
- **M3 (optional, deferred) — authored/instance facts; a standard export
  adapter.**

## Status

| Capability | Status |
| --- | --- |
| Three-graph separation; ontology-as-contract; shapes-as-gates | **roadmap** |
| `ontology.v1` / `semantic-graph.v1` / `shapes.v1` / `closure.v1` contracts | **roadmap** |
| Bespoke canonical assertion model (no blank nodes, `urn:cbp:`) | **roadmap (M1)** |
| Pure-projection semantic graph over existing contracts | **roadmap (M1)** |
| Capability discovery & composition | **roadmap (M1)** |
| Decidable closure (Datalog + stratified negation) + closed-shape gates | **roadmap (M2)** |
| Parent-held **derived** subgraph closure | **roadmap (M2)** |
| Cross-runtime conformance category | **roadmap (M1/M2)** |
| Authored/instance facts; standard export adapter | **deferred (M3)** |
| Non-deterministic linking / full OWL DL / non-additive mutation | **out** |
| Parent owning or mutating child ABox | **out** |

## Scope

- **In:** the three-graph separation; `ontology.v1` / `semantic-graph.v1` /
  `shapes.v1` / `closure.v1`; the bespoke canonical assertion model; term
  identity + import composition; the Datalog + stratified-negation closure;
  pure-projection assertion source; per-component SQLite storage; the
  OWA/fail-closed clamp; parent-held derived closure; the cross-runtime
  conformance category.
- **Out (non-goals):** full OWL DL; any external RDF/OWL/SHACL/Datalog
  dependency; blank nodes; authored/instance facts (M3); non-deterministic
  inference in M1/M2; unbounded/autopoietic ontology mutation; any change to
  `PRINCIPLES.md`; changing Manager orchestration/verification/policy/envelope/
  accounting semantics; self-editing of verification; a new execution backend;
  cloud/network at run time; **any implementation before shared understanding is
  confirmed**.

## Acceptance criteria

- [ ] The semantic graph is a **pure projection** of existing content-addressed
      contracts; identical inputs ⇒ identical graph hash.
- [ ] An ontology is a versioned, signed, content-addressed, **additive**
      contract; an ontology change is gated (spec + review).
- [ ] **No blank nodes**; every term is a declared `urn:cbp:` name;
      declaration-before-use fails closed.
- [ ] A gate passes **only** on a closed shape over a **pinned** closure — never
      on an unproven open-world entailment (test-enforced).
- [ ] Identical `(graph, ontology, rule profile)` ⇒ **identical closure hash**,
      cross-runtime (Python ⇄ Rust).
- [ ] A parent's held subgraph is a **derived closure keyed by child content
      addresses**: recomputable after a child change, containing **no copy** of
      child ABox state (test-enforced).
- [ ] M1/M2 contain **no** `model` component; all reasoning is deterministic.
- [ ] Two editions compute the **same** graph/closure hash for the same inputs
      ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)).
- [ ] The convention guard records this spec.

## Risks / invariants

- **Determinism loss.** Clamp: canonical ordering + content hash; bounded,
  pinned closure; no model components in M1/M2 (Law 2).
- **Sovereignty.** Clamp: parent holds a derived closure by content reference;
  children stay single-writer (§6; SPEC-0009 §3).
- **OWA softening the spine.** Clamp: open-world derives; gates need closed
  shapes (§7).
- **Closure blow-up.** Clamp: stratified, bounded, per-composite closure; a size
  ceiling like `MAX_DIAGRAM_NODES`.
- **Ontology drift.** Clamp: versioned/signed, additive-only contracts;
  declaration-before-use (§1, §3).
- **Ambient authority.** Clamp: the held subgraph is evidence + projection, never
  a decider (Law 10; `brain-charter.md` Q6).
- **Cross-runtime divergence.** Clamp: one canonicalization, set-semantics +
  canonical sort; a shared conformance suite is the sole proof (§13; SPEC-0013).
- **DB-as-truth.** Clamp: contracts and hashes live outside SQLite; a canonical
  assertion log is the recommended source of truth (§10).

Respects Laws 1, 2, 3, 4, 5 (amended), 6, 7, 8, 10, 11, 12, 13. Target
architecture: it changes no `src/` semantics on acceptance of the spec alone.

## Decisions log (grilling session)

| ID | Decision | Chosen | Source |
| --- | --- | --- | --- |
| S1 | Scope & capability target | Capability discovery & composition; M1 vocabulary+shapes, M2 closure | grill R1/Q1 |
| S2 | Distribution & confidentiality | Public, implemented in both `core` and `pro`; no secret sauce | grill R1/Q2 |
| S3 | Build timing & level | Contracts + conformance vectors now (below gate); runtime after ratification | grill R1/Q3 |
| S4 | Representation | Bespoke canonical assertion model (no blank nodes, `urn:cbp:`, canonical JSON + sha256) | grill R2/Q4 |
| S5 | Composition | Composable fragments with explicit imports; per-composite assembly | grill R2/Q5 |
| S6 | M1 vocabulary | Component, capability, port + compatibility relations | grill R2/Q6 |
| S7 | Storage | Per-component SQLite + disposable closure cache; contracts/hashes outside; optional canonical log | user Q + grill R2 |
| S8 | Term identity | `urn:cbp:core/` + per-fragment namespaces; declaration-before-use; additive-only | grill R3/Q8 |
| S9 | Rule profile | Positive Datalog + stratified negation, rules pinned in `ontology.v1` | grill R3/Q7 |
| S10 | Assertion source | Pure projection of existing contracts; no authored second source of truth | grill R3/Q9 |
| S11 | Non-deterministic inference | None in M1/M2; any future linking is a separate recorded `model` component | grill R3/Q10 |

## Open frontier

Remaining design branches (to be grilled to empty before implementation):

1. **Shape expression** — the concrete syntax/format of `shapes.v1` and its
   deterministic validator contract.
2. **Discovery-query semantics** — how a capability-discovery query is pinned and
   evaluated deterministically in M1.
3. **Component integration** — where the runtime lives (module/branch in `core`
   and `pro`), and whether the semantic graph is itself a `component.v1` (and of
   which kind).
4. **Versioning mechanics** — the precise `ontology.v1` fragment/term version
   scheme and additive-migration rules.
5. **Bounds & performance** — the closure ceiling and per-fragment term limits.
6. **Conformance vectors** — the M1/M2 suite, fixtures, and lock set.
7. **M1 definition of done** — the observable that proves capability discovery
   works.

## Next step

Continue the grilling until the frontier is empty; then author the M1 conformance
vectors and the `ontology.v1`/`closure.v1` schemas. No implementation before the
operator confirms shared understanding. The study output is a **spec** (and, if
needed, a refinement of `SPEC-0010`), not an implementation.
