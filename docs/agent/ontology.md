# Ontology and semantic graphs (SPEC-0023)

> **Status:** design spec, **decisions ratified**, no implementation yet.
> Authoritative source: [`specs/SPEC-0023-ontology-semantic-graphs.md`](../../specs/SPEC-0023-ontology-semantic-graphs.md).
> Study charter: [`brain-charter.md`](brain-charter.md). Entry point: [`AGENTS.md`](../../AGENTS.md).

This is the summary of the ontological knowledge base — *the Brain*. It answers a
single question and records the ratified design around it. It is a **spec-level
design**; nothing here executes yet.

## The question

Can CBP carry ontological knowledge, and can a **parent hold the ontological
subgraph of its children**?

## The answer

**Yes — as a derived, pinned, recomputable closure, never as state ownership.**
A parent owns the shared **TBox** fragment and holds a **derived closure** over
`{child identity → child graph content address}`. The child owns its **ABox**
(single-writer); facts rise only as verified proposals; the parent re-verifies and
references them by content address. It never stores, duplicates, or mutates child
state.

## The model

| Contract | Role |
| --- | --- |
| `ontology.v1` | TBox — declared classes/properties, the pinned rule set, imports, shape references. Composable. |
| `semantic-graph.v1` | ABox — the assertions (M1: a pure projection). |
| `shapes.v1` | Closed shapes used as deterministic, fail-closed gates. |
| `closure.v1` | Pinned entailment closure: `(graph hash, ontology hash, rule profile) → closure hash`. |

Three graphs stay distinct — **process** (`network.v1`), **design**
(`design.v1`), **semantic** — composing only through components.

## Representation (bespoke — no RDF stack)

- **Assertions** are `{s, p, o}` canonical JSON; **terms** are `urn:cbp:` names.
- **No blank nodes**; **declaration-before-use fails closed**; terms are
  **additive-only**.
- Hashed with `sha256(canonical_json_bytes(...))`, reusing the system's one
  canonicalization (`cbp/network.py`).
- **No external RDF/OWL/SHACL/Datalog dependency** — the Rust host reimplements
  the same semantics byte-identically.

## Composition and reasoning

- Ontologies are **composable fragments with explicit imports**; a parent assembles
  its subtree's TBox.
- Reasoning is **positive Datalog + stratified negation**, rules pinned in
  `ontology.v1`, evaluated as a **semi-naive fixpoint**, canonical-sorted, and
  **bounded**.
- **Open-world derives; gates are closed** — a gate passes only on a closed shape
  over the pinned closure.

## Delivery stages

1. **M1 — vocabulary + closed shapes + capability discovery & composition** (pure
   projection; no entailment).
2. **M2 — pinned entailment closure** (Datalog + stratified negation) and the
   parent-held derived subgraph.
3. **M3 (deferred) — authored/instance facts; a standard export adapter.**

## Storage

Per-component **SQLite** (`cbp/component_state.py`: WAL, single-writer,
integrity-checked, atomic) holds the assertions and a **disposable** closure cache.
**Contracts and content hashes live outside the DB**; an append-only canonical
assertion log is the recommended source of truth. The Rust host need not use
SQLite.

## Distribution

Public, implemented in **both `core` and `pro`**, one semantics proven by the
shared conformance suite. **No secret sauce**: edition differences are limited to
capacity/performance/deployment/governance/support/UI, never meaning
([`SPEC-0016`](../../specs/SPEC-0016-editions-distribution-cloud.md)).

## Clamps (mission-critical)

- Deterministic control plane; **no `model` components in M1/M2** (Law 2).
- Child state sovereignty; derived closure by content reference (Law 10).
- Content-address + verify before use; nothing trusted before verified
  ([`SPEC-0015`](../../specs/SPEC-0015-provenance-assurance-labels.md)).
- Ontology change is constitutional-level and additive-only.

## Where to go next

- Full design and ratified decisions:
  [`SPEC-0023`](../../specs/SPEC-0023-ontology-semantic-graphs.md) (see its
  *Decisions log* and *Open frontier*).
- Intent: [`SPEC-0010`](../../specs/SPEC-0010-extensibility-knowledge-graphs-rsi.md).
- Verification sibling: [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md)
  workstream 1.
- Conformance: [`SPEC-0013`](../../specs/SPEC-0013-cross-runtime-conformance.md).
