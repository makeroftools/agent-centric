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

> **Status: draft design spec. The design is ratified and its decision tree is
> complete** (S1–S20; the operator delegated the remaining branches and closed the
> grilling session). The [open frontier](#open-frontier) is **empty**. **No
> implementation** lands until the operator confirms this shared understanding.
> This spec changes no `src/` semantics on acceptance of the spec alone.

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
duplication, or mutation of child state; and — in M2 — as a **semantic subnet**,
a composite of fragment components that composes exactly like the process
subnets it mirrors. This formalizes [`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md)
and its verification sibling [`SPEC-0017`](SPEC-0017-frontier-workstreams.md)
workstream 1; it is constitutional-level because it defines *meaning*.

**House constraints found in the environment** (they drive the choices below):
the entire type universe today is **8 scalars** plus ports/edges and kind tags
(`contracts/handoff.py`, `cbp/network.py`); the certifier is **generic** (a new
category needs a reference-host branch + vectors + `compare()` fields +
`certify.sh` wiring); the Rust host **hand-rolls** its graph code and has **no
RDF/OWL/SHACL/Datalog dependency**. Cross-runtime byte-identical parity
([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md)) is the dominant
constraint.

## Decision

### 0. Three graphs stay distinct

- **Process graph** (`network.v1`) — FBP control/data-flow (ports, connections).
- **Semantic graph** — knowledge: subject/predicate/object assertions.
- **Design graph** (`design.v1`) — authoring: references + requested pins.

They compose **through components**, never by being conflated. A knowledge graph
is *a component* whose state is the semantic graph. **Non-conflation is an
invariant:** a semantic relation is never encoded as a process edge, and a process
edge is never an assertion (test-enforced, §Risks).

### 1. Contracts (additive, versioned, signed, content-addressed)

| Contract | Role |
| --- | --- |
| `ontology.v1` | **TBox** — declared classes/properties, the pinned rule set, imports, shape references. Composable; shared context; parent-provisioned. |
| `semantic-graph.v1` | **ABox** — the assertions. In M1 a **derived projection** (§9); component-owned state in M3. |
| `shapes.v1` | **Closed shapes** used as deterministic, fail-closed gates. |
| `closure.v1` | A **pinned materialized entailment closure**: `{graph hash, ontology hash, rule-profile id}` → `closure hash`. |

Contracts are versioned, signed, content-addressed, and **additive-only**.
Changing an ontology changes meaning and verification, so it is
**constitutional-level** (spec + review), exactly like a lock change
([`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) §7.4).

An **ontology fragment** is `name@version` + its content hash, and its `imports`
pin `{id, version, hash}`. Versioning is **additive-only**: a new version may add
terms, rules, and shapes but never changes or removes a term's meaning;
deprecation is an annotation. Every closure pins the exact ontology hashes, so
historic closures stay reproducible (S15).

### 2. Representation — bespoke canonical assertion model (S4)

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
  *export* adapter may be added later (M3); it is out of scope here.

### 3. Term identity and namespacing (S8)

- Base namespace `urn:cbp:core/`; each fragment gets `urn:cbp:<fragment>/`.
- A **fragment id** is its declared `name+version`, or a short content hash.
- **Declaration-before-use, fail-closed:** an undeclared term is an error,
  mirroring `Registry.resolve`/`ComponentCatalog` (Law 10).
- Terms are **additive-only**: a term's meaning never changes; deprecation is an
  annotation, never a redefinition.

### 3a. TBox expressiveness (S17)

Minimal: declared **classes** and **properties**, with **acyclic `subClassOf`**
(multiple inheritance allowed) and property **`domain`/`range`** used as
*validation*, not inference. Subsumption is entailed by a **pinned Datalog rule**,
not a separate reasoner. Everything richer — property hierarchies, disjointness,
equivalence, cardinality axioms — is **out**.

### 4. Ontology composition (S5)

Ontologies are **composable fragments with explicit imports**: a base ontology,
plus each composite may add terms and import its parent's; the parent assembles
its subtree's TBox. This is fractal, like subnets ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
§2), and is the prerequisite for the parent-held subgraph (§6). There is no
single global ontology and no flat namespace.

### 5. Rule profile (S9) and bounds (S16)

- **Positive Datalog + stratified negation.** The rules are a small set
  **explicitly enumerated and pinned in `ontology.v1`**; no OWL import and no rule
  outside the pinned set.
- Evaluation is a **semi-naive least fixpoint**; stratified negation; the result
  is **canonical-sorted**, so evaluation order never affects the content address.
- The closure is **bounded** by explicit, test-enforced, fail-closed ceilings —
  `MAX_CLOSURE_ASSERTIONS = 100_000` and `MAX_FRAGMENT_TERMS = 10_000`
  (tunable), refusing oversized materialization before it begins.
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

### 8. Shapes (S12)

Shapes are **bespoke deterministic validators** over the pinned closure (not
SHACL, since the model is bespoke). A shape is a **pinned JSON document** —
`{id, target (class/term pattern), requirements: [{relation, cardinality,
datatype, allowed}], closed: bool}` — evaluated deterministically; it returns
pass/fail plus the **specific violated requirement**. An undeclared shape or
relation fails closed. `closed: true` means only facts asserted or derived
*within the pinned profile* may satisfy the gate.

### 8a. Capability discovery (M1) (S13)

A discovery query is a **pinned JSON document** over a fixed, tiny algebra: match
a class (and, via `subClassOf`, its subclasses), require capabilities, and check
port-type compatibility (reusing `types_compatible`). It names its **scope** (a
composite/fragment) and resolves **only within that subtree** (sovereignty). The
result is a canonically-sorted list of satisfying components; query + result are
content-addressed, so the same query over the same graph yields the same result
hash. M1 does **no inference** beyond subsumption — it is exact matching over the
projected graph.

### 9. Assertion source — pure projection (S10)

M1's assertions are **derived by a pure projection** from existing
content-addressed contracts — `component.v1` manifests, `Capability`,
`port_types`, `ToolDescriptor`, and the scope's `network.v1` document — exactly as
`cbp/diagram.py` projects a network. The graph is a deterministic,
content-addressed **view**, never a second source of truth (Law 10).
Authored/instance facts are an M3 concern (§Delivery stages).

### 10. Storage (S7)

- Per-component **SQLite** state, via `cbp/component_state.py` (WAL,
  `single_writer`, `integrity_check`, atomic publish, namespace sovereignty).
- Tables: `assertions(s,p,o,graph)`, `assertion_meta(assertion_id, source,
  confidence, content_addr)`, `imports(graph, imported, ontology_hash)`, and a
  **disposable** `closure(...)` cache.
- **Contracts and content hashes live outside the DB.** A graph's hash is
  computed from canonical assertion records, never the DB file (SQLite does not
  promise byte-identical files). Queries always `ORDER BY` a canonical key.
- Optional (recommended for Law-10 alignment): an **append-only canonical
  assertion log** is the source of truth, with SQLite a derived index/closure
  cache.
- The Rust host need not use SQLite at all — it certifies the canonical bytes.

### 11. Determinism ⊗ non-determinism (S11)

M1/M2 contain **no model components**. The deterministic closure is control-plane
and conformance-certified. Any future fuzzy linking is a separate, recorded
`model` component with `confidence`, enqueued to `review.v1` — out of scope here.

### 12. Distribution (S2)

The ontology layer is **public and implemented in both `core` and `pro`**, with
one semantics proven by the shared conformance suite. There is **no "secret
sauce"**: edition differences are limited to capacity/performance/deployment/
governance/support/UI, never meaning ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)).

### 13. Conformance seam (S20)

A new certifier category is added, following the existing mechanics: a
`ReferenceHost.run_case` branch (+ the Rust `run_case` branch),
`vectors/*-{fixtures,suite,lock}.json`, new `compare()` observation fields, and a
`certify.sh` invocation.

- **M1 category (`ontology`):** cases over provided canonical `semantic-graph.v1`
  documents — `hash` (graph content address), `query` (capability discovery ⇒
  canonical result), and `validate` (closed shape ⇒ verdict or `error.kind`);
  error cases for the taxonomy (§15). `project_graph` (the projection glue) is
  covered by core tests; the certified cross-runtime surface is the pure
  semantics over a canonical graph. Delivered: vectors +
  `certifier/reference_host.py` + `pro/src/ontology.rs`, 10/10 both hosts.
- **M2 addition:** `closure` (graph + ontology + profile ⇒ `closure_sha256`).
- Fixtures: a tiny fixture tree (component manifests + a `network.v1` scope doc +
  a lock subset) + a base ontology fragment + shapes.
- Identical inputs ⇒ identical hashes, cross-runtime.

### 14. Component integration (S14, amended)

- Contracts live in `contracts/ontology.py`; the pure runtime lives in
  `cbp/ontology_runtime.py` (core) and is reimplemented in `pro/src/ontology.rs`.
- **M1:** the semantic graph is an **atomic pure kernel** (projection + shapes +
  discovery). Its authority is the **projection**; any SQLite is a cache. No new
  component kind, no new backend.
- **M2:** the semantic graph is a **composite — a semantic subnet** (§14a) whose
  body wires one pure `fragment` child per member into a `closure` child, exposing
  `project` / `query` / `validate` as **external ports**. Wiring is **DAG-only**.
  The entailment **fixpoint runs inside the `closure` component**, bounded and
  stratified — *not* as a network, because `network.v1` rejects cycles and a
  fixpoint is recursive.
- The graph participates in the tree normally, honouring SPEC-0010's "a
  knowledge graph is a component whose state is the semantic graph."

### 14a. Semantic subnet port contract (S19)

- A semantic subnet is a `component.v1` **composite**; its external ports are
  `fragment` (in: an `ontology.v1`/`semantic-graph.v1` content address), `query`
  (in: a pinned query), `result` (out: canonical result), and `closure` (out:
  the pinned closure hash) — all typed with the existing type universe and
  checked with `types_compatible`.
- A fragment child is a pure **project/lift** function; it may lift assertions
  across its boundary **only through declared external ports** (the SPEC-0009
  boundary guard). Interior fragments stay private; roll-up is child → parent
  only.
- Every out-port value is **canonical-sorted and content-addressed**; the
  network is the *composition*, the assertions are the *payload* (no conflation).
- Because the composition is a network, a scope's closure is a **verified,
  ledgered, replayable run** through the driver's spine — not opaque code.

### 15. Error taxonomy (S18)

Fixed, fail-closed error kinds asserted by the certifier's `compare()` surface:
`undeclared-term`, `missing-import`, `version-conflict` (import hash mismatch),
`non-additive-change`, `shape-violation` (carrying the violated requirement id),
`closure-overflow`, `fragment-overflow`, `malformed-document` (canonical-JSON
violation), and `scope-escape` (query/roll-up outside the named scope).

## Delivery stages

- **M1 — vocabulary + closed shapes + capability discovery & composition** (pure
  projection; no entailment). The first useful automation. Certified as a new
  `ontology` conformance category.
- **M2 — pinned entailment closure** (Datalog + stratified negation) and the
  composite **semantic subnet** so parent-subgraphs compose and execute.
- **M3 (deferred) — authored/instance facts; a standard export adapter.**

## Status

| Capability | Status |
| --- | --- |
| Three-graph separation; ontology-as-contract; shapes-as-gates; non-conflation | **roadmap** |
| `ontology.v1` / `semantic-graph.v1` / `shapes.v1` / `closure.v1` contracts | **roadmap** |
| Bespoke canonical assertion model (no blank nodes, `urn:cbp:`) + minimal TBox | **delivered (M1)** |
| Pure-projection semantic graph over existing contracts | **delivered (M1)** |
| Capability discovery & composition + `ontology` conformance category | **delivered (M1)** |
| Decidable closure (Datalog + stratified negation) + closed-shape gates | **delivered (M2)** |
| Composite **semantic subnet** + parent-held derived closure | **delivered (M2)** |
| Authored/instance facts; standard export adapter | **deferred (M3)** |
| Non-deterministic linking / full OWL DL / non-additive mutation | **out** |
| Parent owning or mutating child ABox | **out** |
| Conflating the semantic graph with the process graph | **out** |

## Scope

- **In:** the three-graph separation and non-conflation; `ontology.v1` /
  `semantic-graph.v1` / `shapes.v1` / `closure.v1`; the bespoke canonical
  assertion model; minimal TBox; term identity + import composition; the Datalog
  + stratified-negation closure with bounds; pure-projection assertion source; the
  semantic subnet + port contract; per-component SQLite storage; the
  OWA/fail-closed clamp; the error taxonomy; the cross-runtime conformance
  category.
- **Out (non-goals):** full OWL DL; any external RDF/OWL/SHACL/Datalog
  dependency; blank nodes; property hierarchies/disjointness/equivalence/
  cardinality; authored/instance facts (M3); non-deterministic inference;
  unbounded/autopoietic ontology mutation; conflation of the semantic and process
  graphs; any change to `PRINCIPLES.md`; changing Manager orchestration/
  verification/policy/envelope/accounting semantics; self-editing of
  verification; a new execution backend; cloud/network at run time; **any
  implementation before the operator confirms this shared understanding**.

## Acceptance criteria

- [ ] The semantic graph is a **pure projection** of existing content-addressed
      contracts; identical inputs ⇒ identical graph hash.
- [ ] An ontology is a versioned, signed, content-addressed, **additive**
      contract; an ontology change is gated (spec + review).
- [ ] **No blank nodes**; every term is a declared `urn:cbp:` name;
      declaration-before-use fails closed; only acyclic `subClassOf` +
      `domain`/`range` are supported.
- [ ] A gate passes **only** on a closed shape over a **pinned** closure — never
      on an unproven open-world entailment (test-enforced).
- [ ] Identical `(graph, ontology, rule profile)` ⇒ **identical closure hash**,
      cross-runtime (Python ⇄ Rust).
- [ ] A parent's held subgraph is a **derived closure keyed by child content
      addresses**: recomputable after a child change, containing **no copy** of
      child ABox state (test-enforced).
- [ ] A **semantic subnet** composes only through declared external ports; the
      closure fixpoint runs inside its component (the subnet stays a DAG)
      (test-enforced).
- [ ] The semantic graph is **never** encoded as process edges and the process
      graph is never read as assertions (non-conflation, test-enforced).
- [ ] M1/M2 contain **no** `model` component; all reasoning is deterministic.
- [ ] Every failure is fail-closed with a kind from the taxonomy (§15).
- [ ] Two editions compute the **same** graph/closure hash for the same inputs
      ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)).
- [ ] The convention guard records this spec.

## Risks / invariants

- **Conflation.** Two different relations are easy to blur. Clamp: the process
  graph is composition only; semantic relations are assertions; invariant
  test-enforced (§0).
- **Fixpoint vs DAG.** `network.v1` rejects cycles; entailment is recursive.
  Clamp: the closure stays a bounded pure computation inside one component; only
  the fragment composition is a subnet (§14).
- **Determinism loss.** Clamp: canonical ordering + content hash; bounded,
  pinned closure; no model components in M1/M2 (Law 2).
- **Sovereignty.** Clamp: parent holds a derived closure by content reference;
  children stay single-writer (§6; SPEC-0009 §3).
- **OWA softening the spine.** Clamp: open-world derives; gates need closed
  shapes (§7).
- **Closure blow-up.** Clamp: stratified, bounded, per-composite closure (§5).
- **Ontology drift.** Clamp: versioned/signed, additive-only contracts;
  declaration-before-use; deprecate-not-change (§1, §3, §3a).
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
| S12 | Shape expression | Pinned JSON shape document (SHACL-inspired), deterministic validator, fail-closed | grill R4/Q11 |
| S13 | Discovery (M1) | Pinned query document; exact matching + subsumption; subtree-scoped; content-addressed result | grill R4/Q12 |
| S14 | Component integration | `contracts/ontology.py`; `cbp/ontology_runtime.py` + `pro/src/ontology.rs`; M1 atomic pure kernel (projection authority), M2 composite semantic subnet; closure fixpoint inside a component | grill R4/Q13 + R5 refinement |
| S15 | Versioning | `name@version` + hash; imports pin `{id,version,hash}`; additive-only; deprecate-not-change | grill R4/Q14 |
| S16 | Bounds | `MAX_CLOSURE_ASSERTIONS=100_000`, `MAX_FRAGMENT_TERMS=10_000`, fail-closed | grill R4/Q15 |
| S17 | TBox expressiveness | Minimal: classes/properties + acyclic `subClassOf` + `domain`/`range` (validation); richer out | grill R5/Q16 |
| S18 | Error taxonomy | The nine fail-closed kinds (§15) | grill R5/Q17 |
| S19 | Semantic subnet port contract | `fragment`/`query` in, `result`/`closure` out; DAG-only; boundary guard; canonical | grill R5/Q18 |
| S20 | Conformance vectors | New `ontology` category (M1) + `closure` (M2); `graph_sha256`/`result`/`shape_verdict`/`closure_sha256` | grill R5–R6 |

## Open frontier

**None.** Every branch of the design tree has been visited and settled (S1–S20).
This is the shared understanding. Implementation planning (M1 conformance
vectors, then M1 code) may begin once the operator confirms it.

## Next step

The operator confirms the shared understanding; then author the M1 conformance
vectors and the `ontology.v1`/`shapes.v1` schemas, and implement the M1 pure
kernel below the gate. No implementation before that confirmation. The study
output is a **spec** (and, if needed, a refinement of `SPEC-0010`), not an
implementation.
