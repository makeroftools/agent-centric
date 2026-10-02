# The Brain — a study charter (deferred)

> **Status: study charter. No design freeze.** This page records an intent and the
> questions to answer — it is **not** a spec and commits to no architecture. The
> eventual home is [`SPEC-0010`](../../specs/SPEC-0010-extensibility-knowledge-graphs-rsi.md)
> (the ontological knowledge base), with
> [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md) workstream 1
> (formal semantics) as its verification sibling. The dedicated session has now
> produced the **draft design spec**
> [`SPEC-0023`](../../specs/SPEC-0023-ontology-semantic-graphs.md); it carries no
> design freeze until the operator ratifies its open decisions. Entry point:
> [`AGENTS.md`](../../AGENTS.md).

## The intent

**The Brain** is the system's ontological knowledge base: a place where meaning —
not just data — is represented, so the tree can reason about *what things are*
and *how they relate*, and so automation and generation can draw on that structure
instead of starting from nothing each time.

The emphasis is **automation and generation**: the knowledge base should help the
system *construct* new knowledge, components, and tasks — not merely store facts.

This is a deep topic. It is deliberately deferred to a dedicated session, because
a rushed ontology would become a permanent, load-bearing mistake.

## Why it is deferred (the honest reason)

- Ontology is **constitutional**: [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md)
  treats an ontology change as a constitutional-level change, not a feature.
- The frontier strictly separates the **semantic graph** (meaning), the
  **ontological commitments** (what is allowed to mean), and **shapes-as-gates**
  (the constraints that make entailment decidable). Deciding the subset is the
  whole problem.
- Full OWL DL is explicitly **out**; a **decidable subset** lands first. Choosing
  that subset (a rule profile) is the first real decision — and it is not one to
  make in passing.
- Non-deterministic inference must be confined to recorded `model` components;
  the deterministic part is a pinned, decidable entailment closure.

## Questions to answer (the study agenda)

1. **Scope:** what is the *minimum* ontology that makes the first useful
   automation possible? What is explicitly out?
2. **Decidability:** which decidable subset / rule profile (e.g. a Datalog-like
   core, RDFS-plus, OWL-RL-style) gives useful entailment while staying
   deterministic and pinned?
3. **Representation:** how does the semantic graph sit on the existing durable
   state (SQLite, single-writer, append-only evidence) without breaking state
   sovereignty?
4. **Contract:** what is the ontology-as-contract (`ontology.v1`?) and the
   semantic-graph component; how is it content-addressed, versioned, and gated?
5. **Generation:** how does the knowledge base feed *construction* — deriving
   components, tasks, and `work.v1` recipes — while keeping generated artifacts
   untrusted until verified?
6. **Boundaries:** where does the ontology stop, and how do we prevent it from
   becoming an ambient authority? A registry is a passive catalog; the topology
   decides ([`PRINCIPLES.md`](../../PRINCIPLES.md) Law 10).
7. **Alignment:** how does this connect to [`SPEC-0010`](../../specs/SPEC-0010-extensibility-knowledge-graphs-rsi.md)
   and [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md) WS1 without
   duplicating either?

## Boundaries / non-goals (for now)

- **Out:** full OWL DL; an open-ended crawler; any ontology that changes the
  meaning of existing contracts (that would be additive-only at best).
- **Out:** using the Brain as an authority. It records meaning; the topology and
  the verifier decide.
- **Out:** non-deterministic inference in the control plane — that belongs to
  recorded `model` components.
- **Not now:** implementation of any kind. This is a charter.

## Constitutional constraints (must hold when it is specified)

- Deterministic control plane; non-determinism confined and recorded (Law 2).
- Content-address and verify before use; nothing trusted before verified
  (SPEC-0015).
- Passive catalogs; evidence immutable (Law 10).
- The ontology change is constitutional-level and, once frozen, additive-only.

## Design spec (draft)

[`SPEC-0023`](../../specs/SPEC-0023-ontology-semantic-graphs.md) records the
dedicated-session output: the `ontology.v1` / `semantic-graph.v1` / `shapes.v1` /
`closure.v1` contracts; a decidable **Datalog + stratified-negation** closure
(SHACL gates; OWL-RL optional); and the answer to the parent-subgraph question —
**a parent holds a derived, pinned closure over its children's published facts, and
never owns or mutates child state**. It also records the operator's open
"secret sauce" question (distribution vs `SPEC-0016`'s one-semantics invariant) and
marks every choice **PROPOSED** until ratified.

## Next step

The operator ratifies `SPEC-0023`'s open decisions — a **grilling session** is the
intended venue — then a focused session authors the conformance vectors and the
`ontology.v1`/`closure.v1` schemas. No implementation before ratification; the
output of the study is a **spec** (and, if needed, a refinement of `SPEC-0010`),
not an implementation.
