---
id: SPEC-0024
title: Learning and bounded recursive self-improvement — the proposal loop, pinned bounds, and the L3 gate
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Learning and bounded recursive self-improvement — the proposal loop, pinned bounds, and the L3 gate

> **Status: draft design spec. The design is ratified (grilling decisions L1–L16)
> and the open frontier is empty.** This is a **design-only** session: no part of
> the learning layer is implemented, and implementation of any unit here is
> **post-L3 and operator-gated**. The [learning charter](../docs/agent/learning-charter.md)
> defers self-improvement to the post-L3 frontier; this spec records the deviation
> explicitly (the operator directed a design session now) and keeps the
> implementation gate intact. This spec changes no `src/` semantics on acceptance
> of the spec alone. Human summary: [`docs/agent/learning.md`](../docs/agent/learning.md).
> Home: [`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md) §3;
> frontier sibling: [`SPEC-0017`](SPEC-0017-frontier-workstreams.md) workstream 5.

## Context

[`SPEC-0010`](SPEC-0010-extensibility-knowledge-graphs-rsi.md) §3 already fixes the
**principles** of bounded recursive self-improvement: learning is quarantined;
every update is recorded and pinned; self-improvement **proposes, it does not
promote**; improving verifiers is declared-gated; and reward-gaming is clamped by
immutable evidence and an external holdout. [`SPEC-0017`](SPEC-0017-frontier-workstreams.md)
workstream 5 parks the workstream, requiring it be promoted to its own spec before
implementation. The [learning charter](../docs/agent/learning-charter.md) records
the study agenda and the decisions already aligned.

The missing artifact is the **design**: the shape of the proposal loop, what may
improve and in what order of risk, what reward is safe, how limits are made
non-self-widening, what is recorded, and how the deterministic platform replays a
non-deterministic learner. This spec supplies it.

The gate that makes any of it safe is the **isolated holdout validator**
([`SPEC-0019`](SPEC-0019-isolated-holdout-validator.md)). It exists and is
L3-ready, but L3 is **not flipped**: no operator has produced an
`isolation: enforced` green run on a principal/host distinct from the author.
Until that holds, self-improvement is **declared-gated and not built**.

The mechanisms this layer must compose with already exist and are delivered:

- **`review.v1` + confidence** — the quarantine (`contracts/review.py`,
  `cbp/review.py`): `needs_review(verified, confidence, threshold)` is fail-closed
  (default threshold `1.0`); `ReviewItem`/`ReviewResolution` are content-addressed
  and append-only.
- **The `model` boundary** — `cbp/model_record.py`: `ModelRecordLog` is
  append-only and idempotent; a model's confidence is capped strictly below `1.0`
  and is never conclusive alone.
- **`work.v1`** — `contracts/work.py`: a recipe compiles purely to a `component.v1`
  and generated work is untrusted until `assert_work_executable` passes.
- **The distribution spine** — `cbp/trust.py` (overlapping trust windows) and
  `cbp/lockfile.py` (`design/network → pin → components.lock`).
- **The semantic layer** — [`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md)
  (`ontology.v1`, `semantic-graph.v1`, `shapes.v1`, the pinned closure).

## Decision

### 0. Post-L3, always (L1)

The learning layer is **never built before the isolated holdout validator exists
and is green** on a principal/host distinct from the author, with `[levels.L3]`
flipped by the operator as a deliberate act. This spec is design only.

### 1. The learning layer is a component (L5)

Learning lives in a **`model`/policy component** ([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md)
§4's recorded, confidence-scored `model` boundary; `cbp/model_record.py`). It
introduces **no new node kind** and is **never privileged**; every task is a
component, and learning is no exception. Its output is a **suspect** until a
deterministic verifier accepts it, and it never runs on the deterministic control
plane (Law 2).

### 2. The proposal loop (L4)

`propose → review → sign → lock`.

- A **proposal** is a content-addressed, immutable artifact (`learning.proposal/v1`, §6).
- It enters the existing **`review.v1`** queue when unverified or below threshold
  (`needs_review`; default threshold `1.0`, so only a fully certain, verified
  outcome bypasses the queue).
- Promotion requires a `ReviewResolution` (authority per §5), then a signature
  (`cbp/trust.py`), then a **lock change** (`cbp/lockfile.py`).
- The learner only **appends** proposals and evidence. It never mutates trusted
  state directly, and no new cryptography is introduced.

### 3. The improvable set — a risk ladder (L3)

| Class | Pre-L3 | Post-L3 |
| --- | --- | --- |
| `work.v1` recipes; network/plan documents; **additive** ontology fragments | proposable; promoted only via review→sign→lock (**human**) | may auto-promote on an `isolation: enforced` green holdout |
| rule-profile components; verifiers/gates; the constitution | **out** | only through the isolated validator **and** an operator act (K4) |

A proposal may cross a boundary **only through declared external ports**
([`SPEC-0009`](SPEC-0009-fbp-abm-conformance.md) §2–§3). Changing a rule profile or
a verifier changes *meaning* and *verification*, so it is constitutional-level
([`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md) §1).

### 4. Reward (L8)

- Reward is the **deterministic verifier's verdict** over **pinned** inputs: the
  conformance suite, closed shapes ([`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md) §8),
  and — post-L3 — the holdout. **No self-reported reward.**
- An auxiliary scalar (cost/latency/coverage) is a **recorded tie-breaker** and
  may **never** override a failed verifier.
- The learner **cannot read or define the holdout**.
- The signal is recorded evidence that feeds `confidence`/`review.v1`; it is never
  a control-plane input.

### 5. Promotion authority (L10)

- **Pre-L3:** *every* promotion requires an operator `ReviewResolution` (human gate).
- **Post-L3:** only non-verifier, non-constitutional classes (recipes, plans,
  additive ontology fragments) may auto-promote, and **only** on an
  `isolation: enforced` green holdout. Rule-profile/verifier/constitution changes
  always require the holdout **and** an operator act.
- Promotion is **per-proposal**, signed, and pinned (a lock change). A
  `rehearsal` record never authorizes a merge (SPEC-0019 §1).

### 6. Evidence and records (L12)

`learning.proposal/v1` is an immutable, canonical-JSON, `sha256`-addressed record:

```
{ schema, id, kind, subject_hash,
  generator: { model_id, model_record_hash },
  reward_evidence: { check_hash: verdict },
  bounds_hash, confidence, provenance, prior_proposal_hash }
```

- `kind` ∈ `{recipe, plan, ontology-fragment}`.
- Stored in a **write-once, idempotent** log (the `ModelRecordLog` pattern),
  deterministically ordered, **wall-clock-free**.
- **No secret material** and **no holdout content** — only a verdict hash.
- `learning.bounds/v1` (§7) is referenced by digest (`bounds_hash`).

### 7. Bounds — non-self-widening (L9)

Limits are a pinned, content-addressed, **read-only** `learning.bounds/v1`:

```
{ allowed_proposal_classes, max_proposals_per_epoch,
  max_attempts, max_steps, max_wallclock, max_blast_radius }
```

- It is referenced by the policy component's manifest and **re-derived and
  verified before each epoch**.
- The learner **cannot author or mutate** it; it is a read-only pinned input.
- **Widening a bound is an operator/constitutional act** (new pinned bounds +
  review).
- Exceeding a bound ⇒ **stop + escalate** (Law 8), never a partial success.
- Attempt/token caps mirror the `.agentfactory.toml` L3 caps.

### 8. Reward-gaming clamps (L13)

(a) immutable evidence (Law 10); (b) the external holdout is the *real* reward;
(c) the no-override rule (§4); (d) recorded reward provenance for post-hoc audit;
(e) idempotency kills reward from duplicate proposals; (f) deterministic verifiers
over pinned inputs the learner cannot influence. Verifier self-improvement is the
K4 gate (§13).

### 9. Replay and idempotency (L14)

Non-determinism is confined to the learner. Replay re-runs the **deterministic
verifier** on the **pinned** applied artifact, **never** the learner. The applied
policy is reproducible from a **pinned artifact** (seed + model ref recorded) or
its hidden learner state is excluded from the control plane. Re-recording a
proposal is a **no-op**. Every effect is a lock change with a recorded
proposal → review → signature chain. Records are wall-clock-free.

### 10. Isolation (L12, L15)

The learner runs **process-isolated, A0-style**: zero capabilities, reading only
pinned projections, submitting proposals through the verified spine. Capabilities
are declared **by refs only**, default-deny ([`SPEC-0015`](SPEC-0015-provenance-assurance-labels.md),
Law 7). It never holds ambient authority.

### 11. Brain and `work.v1` interaction (L6)

The learning layer **consumes** the [`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md)
ontology **read-only** (queries and projections as shared context) and **emits**
candidate `work.v1` recipes ([`SPEC-0021`](SPEC-0021-components-as-dynamic-work.md);
gated by `assert_work_executable`) and **additive** ontology-fragment
**proposals**. It never writes the ontology directly — that is a constitutional,
reviewed act.

### 12. Loop invocation (L15)

Operator-invoked/manual **pre-L3**; a bounded **cadence** per the pinned bounds
post-L3. The learner is declared with capabilities by refs only, default-deny,
and is never a privileged kind.

### 13. "Who verifies the improver?" (L7)

Verifier/gate change is the **single highest-risk class**. It is allowed **only
post-L3**, **only** when the [`SPEC-0019`](SPEC-0019-isolated-holdout-validator.md)
validator is green on a principal/host distinct from the author, and the
validator lives **outside** the composition it validates (bootstrap circularity).

## Delivery stages (all post-L3)

| stage | deliverable |
| --- | --- |
| **K1** | `learning.proposal/v1` + `learning.bounds/v1` contracts and the policy-component boundary. |
| **K2** | the propose → review loop over `review.v1` (the deterministic parts tested offline). |
| **K3** | recorded active-inference signals (prediction error / expected free energy) feeding `confidence`/`review.v1`. |
| **K4** | verifier self-improvement — the highest gate, behind the enforced holdout. |

The learner is non-deterministic, so there is **no cross-runtime conformance
category**; canonicalization/hash and bounds enforcement are **core-offline**
tests. `component-abi.v1` stays frozen; legacy `task.v1` untouched.

## Scope

- **In:** the learning/policy component boundary; the propose → review → sign →
  lock loop; the improvable set and its risk ladder; the reward definition; pinned
  non-self-widening bounds; the `learning.proposal/v1`/`learning.bounds/v1`
  contracts; reward-gaming clamps; replay/idempotency; isolation; the Brain/`work.v1`
  interaction; the verifier self-improvement gate; the delivery stages.
- **Out (non-goals):** unbounded/autopoietic self-modification (**permanently
  out**); any learning on the deterministic control plane or any mutation of state
  without a recorded, reviewed proposal; self-improvement of verifiers before L3;
  any change to `PRINCIPLES.md`; changing Manager
  orchestration/verification/policy/envelope/accounting semantics; a new execution
  backend; cloud/network at run time; any implementation in this session.

## Acceptance criteria

- [ ] The learning layer is a `model`/policy **component**; no new node kind; it is
      recorded and confidence-scored and never runs on the deterministic control
      plane.
- [ ] A proposal is content-addressed, immutable, append-only, and idempotent, and
      is promoted **only** via review → sign → lock; the learner mutates nothing
      directly.
- [ ] Pre-L3 the improvable set excludes rule profiles and verifiers; verifier
      self-improvement is post-L3 and gated behind an `isolation: enforced` green
      holdout.
- [ ] Reward is a **deterministic verifier verdict** over pinned inputs; there is
      no self-reported reward; an auxiliary scalar can never override a failed
      verifier; the holdout is unreadable to the learner.
- [ ] Bounds are a pinned, read-only `learning.bounds/v1`; the loop cannot widen
      them; exceeding a bound stops and escalates (fail-closed).
- [ ] Replay re-runs the deterministic verifier on the pinned applied artifact;
      records are wall-clock-free and idempotent.
- [ ] No secret material and no holdout content appear in any proposal or record.
- [ ] `component-abi.v1` stays frozen; legacy `task.v1` untouched; no cross-runtime
      conformance category is added; deterministic parts have core-offline tests.
- [ ] Implementation is post-L3; nothing is built by this spec.
- [ ] The convention guard records this spec.

## Risks / invariants

- **Verifier recursion ("who verifies the improver?").** Clamp: verifier change is
  the highest-risk class, allowed only post-L3 through the external isolated
  validator (§13).
- **Reward-gaming.** Clamp: immutable evidence + external holdout + the
  no-override rule + recorded provenance + idempotency (§8).
- **Determinism loss.** Clamp: the learner is a recorded `model` component;
  non-determinism is confined; replay uses pinned artifacts (Law 2) (§1, §9).
- **Bound widening.** Clamp: bounds are pinned and read-only; widening is an
  operator act (§7).
- **Holdout leakage.** Clamp: the learner cannot read or define the holdout; only
  a verdict hash is recorded (§4, §6).
- **Sovereignty / conflation.** Clamp: proposals cross only declared external
  ports; the semantic graph is never conflated with the process graph
  ([`SPEC-0023`](SPEC-0023-ontology-semantic-graphs.md) §0).
- **Unbounded self-modification.** Permanently out; never a feature.

Respects Laws 1, 2, 2a, 3, 4, 5 (amended), 6, 7, 8, 10, 11, 12, 13. It changes no
`src/` semantics on acceptance of the spec alone, and it is not implemented.

## Decisions log (grilling session)

| ID | Decision | Chosen | Source |
| --- | --- | --- | --- |
| L1 | Session, gate & output | Proceed now as a **design-only** spec; implementation post-L3 and operator-gated | grill R1/Q1 |
| L2 | Home & numbering | New additive `SPEC-0024`; `SPEC-0010` stays the index; charter flipped; `docs/agent/learning.md` summary | grill R1/Q2 |
| L3 | Improvable set | Recipes, plans/networks, additive ontology fragments pre-L3; rules/verifiers out pre-L3, post-L3 via the validator | grill R1/Q3 |
| L4 | Loop shape & `review.v1` seam | `propose → review → sign → lock`; reuse `review.v1`/`trust.py`/`lockfile.py`; learner appends only | grill R1/Q4 |
| L5 | Learning posture | A `model`/policy component; recorded, confidence-scored; no new node kind; off the control plane | grill R1/Q5 |
| L6 | Brain/`work.v1` interaction | Consume `SPEC-0023` read-only; emit candidate `work.v1` recipes and additive ontology-fragment proposals | grill R1/Q6 |
| L7 | Verifier self-improvement | Highest-risk class; post-L3 only, via the external isolated validator | grill R1/Q7 |
| L8 | Reward | Deterministic verifier verdict over pinned inputs; no self-report; no override; holdout unreadable | grill R2/Q8 |
| L9 | Bounds | Pinned, read-only `learning.bounds/v1`; non-self-widening; operator act to widen; exceed ⇒ stop+escalate | grill R2/Q9 |
| L10 | Promotion authority | Pre-L3 human `ReviewResolution` for all; post-L3 auto-promote non-verifier classes on an enforced green holdout | grill R2/Q10 |
| L11 | Milestones & conformance | Staged K1–K4, post-L3; no cross-runtime category; core-offline deterministic tests | grill R2/Q11 |
| L12 | Evidence & isolation | `learning.proposal/v1`, write-once, idempotent, wall-clock-free; learner A0-contained | grill R3/Q12 |
| L13 | Reward-gaming clamps | Immutable evidence + external holdout + no-override + provenance + idempotency + uninfluencable verifiers | grill R3/Q13 |
| L14 | Replay & idempotency | Replay the deterministic verifier on the pinned artifact; applied policy pinned; re-record is a no-op | grill R3/Q14 |
| L15 | Loop invocation & isolation | Operator-invoked pre-L3; bounded cadence post-L3; capabilities by refs only, default-deny | grill R3/Q15 |
| L16 | Acceptance criteria | The ten criteria above | grill R3/Q16 |

## Open frontier

**None.** Every branch of the design tree has been visited and settled (L1–L16).
This is the shared understanding.

## Next step

Implementation begins **post-L3**, starting at **K1**, and is operator-gated. No
implementation lands before the isolated holdout validator is green on a
principal/host distinct from the author and the operator flips `[levels.L3]`.
