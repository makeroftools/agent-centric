# Learning and bounded recursive self-improvement (SPEC-0024)

> **Status:** design spec, **decisions ratified and the design tree complete**
> (L1–L16), no implementation. Authoritative source:
> [`specs/SPEC-0024-learning-bounded-rsi.md`](../../specs/SPEC-0024-learning-bounded-rsi.md).
> Study charter: [`learning-charter.md`](learning-charter.md). Entry point:
> [`AGENTS.md`](../../AGENTS.md).

This is the summary of the **learning layer** — reinforcement learning / active
inference and **bounded** recursive self-improvement (RSI). It is a **spec-level
design**; nothing here executes, and implementation is **post-L3 and
operator-gated**.

## The intent

Let the system improve its *proposals* over time, and improve *itself* within
limits it cannot widen on its own — safely, because a verification gate comes
first.

## The gate that makes it safe

The **isolated holdout validator** ([`SPEC-0019`](../../specs/SPEC-0019-isolated-holdout-validator.md))
is the hard prerequisite. Learning is never built until the validator is green on
a principal/host **distinct from the author**, with `[levels.L3]` flipped by the
operator. L3 is currently **not** flipped.

## The model

| Artifact | Role |
| --- | --- |
| the policy `model` component | the learner — recorded, confidence-scored, **no new node kind**, off the control plane |
| `learning.proposal/v1` | an immutable, content-addressed proposal (recipe / plan / additive ontology fragment) + reward evidence |
| `learning.bounds/v1` | pinned, read-only limits the loop cannot widen |
| `review.v1` | the existing quarantine the proposal enters when unverified or below threshold |

## The loop

`propose → review → sign → lock`. The learner only **appends** proposals and
evidence; promotion needs a `ReviewResolution`, a signature, and a lock change. It
never mutates trusted state.

## The clamps (mission-critical)

- **Reward is the deterministic verifier's verdict** over pinned inputs — never a
  self-reported score; an auxiliary scalar can never override a failed verifier.
- **Bounds cannot self-widen**; widening is an operator act; exceed ⇒ stop +
  escalate.
- **Pre-L3** the improvable set is recipes, plans, and **additive** ontology
  fragments; rule profiles and verifiers are **out**. **Post-L3** they are allowed
  only through the external holdout validator.
- **Replay** runs the deterministic verifier on the pinned artifact, never the
  learner; records are wall-clock-free and idempotent.
- Learner is **A0-contained**; no secrets, no holdout content, no ambient
  authority.
- Unbounded/autopoietic self-modification is **permanently out**.

## Delivery stages (all post-L3)

1. **K1** — `learning.proposal/v1` + `learning.bounds/v1` contracts and the
   policy-component boundary.
2. **K2** — the propose → review loop over `review.v1`.
3. **K3** — recorded active-inference signals feeding `confidence`/`review.v1`.
4. **K4** — verifier self-improvement (the highest gate).

## Where to go next

- Full design and ratified decisions:
  [`SPEC-0024`](../../specs/SPEC-0024-learning-bounded-rsi.md) (see its
  *Decisions log*).
- Intent: [`SPEC-0010`](../../specs/SPEC-0010-extensibility-knowledge-graphs-rsi.md).
- Verification sibling: [`SPEC-0017`](../../specs/SPEC-0017-frontier-workstreams.md)
  workstream 5.
- The gate: [`SPEC-0019`](../../specs/SPEC-0019-isolated-holdout-validator.md).
