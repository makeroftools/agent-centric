# Learning, RSI, RL, and Active Inference — a study charter (deferred)

> **Status: study charter. No design freeze.** This page records an intent, the
> decisions already aligned, and the questions to answer. It is **not** a spec. The
> eventual home is [`SPEC-0010`](../../specs/SPEC-0010-extensibility-knowledge-graphs-rsi.md)
> (bounded RSI) with [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md)
> workstream 5 (bounded recursive self-improvement). Entry point:
> [`AGENTS.md`](../../AGENTS.md).

## The intent

Give the system a **learning layer** — reinforcement learning and/or active
inference — so it can improve its *proposals* over time, and a **bounded
self-improvement** loop so it can improve *itself* within limits it cannot widen
on its own.

This is the most powerful and most dangerous part of the roadmap. It is deferred
to the **post-L3 frontier**, and it is only safe because of the gate that comes
first.

## The gate that makes it safe

**The isolated holdout validator ([`SPEC-0019`](../../specs/SPEC-0019-isolated-holdout-validator.md))
is the hard prerequisite.** A system may not improve its own verifiers until an
independent, authored-blind validator exists and is green on a principal/host
distinct from the author, with `[levels.L3]` flipped deliberately by the operator.
Until then, self-improvement is **declared-gated and not built**.

## Decisions already aligned

These are settled by the current session and constrain any future spec:

1. **Post-L3, always.** No self-improvement of verification before the isolated
   validator exists and is green.
2. **The learning layer is a component** — a recorded, confidence-scored
   component (a `model`/policy kind). It is **never a privileged kind**; every
   task is a component, and learning is no exception.
3. **It proposes; the verifier disposes.** A learned proposal is a **suspect**
   until a deterministic rule, the conformance suite, or the validator accepts it.
   Nothing a model produces is authoritative.
4. **Bounds cannot self-widen.** The loop may improve within declared limits; it
   may not change those limits. Widening a bound is an operator act.
5. **Every proposal is content-addressed and reviewed** before it affects the
   tree, and every effect is recorded and replayable.

## Questions to answer (the study agenda)

1. **Shape of the loop:** propose → review → sign → lock (`SPEC-0010`/`SPEC-0017`
   WS5)? How does it compose with the existing review queue
   (`review.v1`, [`SPEC-0002`](../../specs/SPEC-0002-cbp-component-architecture.md)
   §4/§6)?
2. **What may improve:** which artifacts can the loop touch (rules, recipes,
   plans, verifiers) and in what order of risk?
3. **Reward:** if learning optimizes a signal, what signal is safe? (The verifier
   is the reward — which is exactly why verifier self-improvement is gated.)
4. **Active inference:** is it a planner, a policy component, or a distinct kind —
   and how does it stay off the deterministic control plane?
5. **Bounds:** how are the limits expressed, pinned, and enforced so the loop
   cannot widen them? What is the cap on attempts and blast radius?
6. **Evidence:** what is recorded per proposal so a run is replayable and
   auditable, and so reward-gaming is detectable?
7. **Interaction with the Brain:** does learning consume the ontology
   ([brain charter](brain-charter.md)), and does it generate `work.v1` recipes
   ([`SPEC-0021`](../../specs/SPEC-0021-components-as-dynamic-work.md))?

## Boundaries / non-goals (for now)

- **Out:** unbounded or autopoietic self-modification — permanently out.
- **Out:** any learning that runs on the deterministic control plane or mutates
  state without a recorded, reviewed proposal.
- **Out:** self-improvement of verifiers before L3.
- **Not now:** implementation of any kind. This is a charter.

## Constitutional constraints (must hold when it is specified)

- "Verification is the constraint, not generation" — the learning layer is
  constrained by verification, not the reverse.
- Determinism is a property of the platform; the learning layer is a recorded,
  non-deterministic input (Law 2).
- Least privilege and explicit, audited failure (Laws 7, 8).
- Content-address and verify before use (SPEC-0015).
- Bounded RSI only; unbounded RSI stays out.

## Next step

A dedicated study session **after** the L3 gate is satisfied. Its output is a
spec (and, if needed, a refinement of `SPEC-0010`), not an implementation.
