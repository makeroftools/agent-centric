# Agent Construction Law — dynamic composition, deterministic execution

**Status:** design principle (working). Captured to inform the AC Router /
smart-model-manager standalone package and the FBP subsystem generally.

## The one-line law

> **Dynamic in the decision, deterministic in the execution — dynamic where it
> buys value, pinned where reproducibility is required.**

This is the resolution of two forces that pull against each other:

- The north star: **deterministic platform first.**
- The core endeavour: **everything dynamic that is practicable.**

They are not in conflict once we are precise about *which layer* is dynamic and
*which* is deterministic.

## The tension, named

| Layer | Nature | Why |
|-------|--------|-----|
| **Recipe** (composition spec) | **Dynamic** — produced at plan time, per task/context | A decision made fresh; adapts to the task |
| **Selection** (routing, decomposition) | **Dynamic** | A decision made fresh; adapts to domain/context |
| **Enactment** (of a given recipe) | **Deterministic** | Must be reproducible or "no unverified success" collapses |
| **Re-verification** (of every facet's value) | **Deterministic** | The correctness spine; never relaxed |
| **Compiled network** | **Deterministic artifact** | A reproducible derivation, auditable and re-verifiable |
| **Audit trail** | **Deterministic** | Proof, not hope |

The law is not "be dynamic" or "be deterministic" — it is **dynamic in the
decision, deterministic in the execution.** The same law appears at every scale:
the router (dynamic selection, deterministic verification), the catalog
(dynamic feed, deterministic reviewed promotion), the fractal decomposition
(dynamic plan, deterministic re-verification).

## "As practicable" is load-bearing

It is the discipline that stops "dynamic" from becoming "non-deterministic":

- Dynamic **where it buys value** — a recipe that adapts to the task, a route
  that adapts to the domain, a composition that adapts to context.
- Pinned **where reproducibility is required** — the compiled form, the audit,
  the verification, the cache key.

**The test for any layer:** *"If I make this dynamic, can I still reproduce
it?"* If yes → dynamic. If no → pin it.

## Agent construction law

> **An agent is a component: a thin protocol wrapper over a pure decision core,
> where the wrapper mediates the irreducible side-effects and the delegation. A
> task decomposes into a CPM sub-network; the parent is its orchestrator,
> re-verifying each facet's value on the upward path. Decompose a facet into a
> child agent when independent verification, audit, or reuse is worth the hop.**

Three disciplines keep it from becoming cargo-cult:

1. **Pure-core/thin-wrapper is the ideal for the *decision* facet, not every
   agent.** Stateful agents (store, catalog) and I/O agents (intake, email)
   have irreducible side effects. The rule: *decompose until the decision core
   is pure and deterministic; the agent wrapper is precisely where the
   irreducible side-effects live.*

2. **Decompose where it buys governance, not reflexively.** Every child agent
   adds protocol hops, latency, and tree complexity. Push a facet down to a
   child when independent verification/audit/reuse is worth the hop; otherwise
   keep it local. This is the "optimal" half of the mission — don't gold-plate
   the tree.

3. **The parent is the orchestrator of a composition, not a persistent
   worker-manager hybrid.** An agent is composed at network-implementation
   initialization time (at time of enacting the network composition). The
   networks are dynamically produced (a recipe). There is no persistent node
   that "happens to also work" — there is a composition that is enacted.

## The recipe → compiled network model (three tiers)

Three distinct things, needing three distinct storage/registry agents:

1. **Recipe** — the declarative composition spec: the CPM sub-network DAG, the
   facets, the connections. **Dynamic** (produced at plan time), but a
   **deterministic spec** once produced — versioned and reproducible.

2. **Enactment** — instantiating a recipe into a live network at init time
   ("network implementation initialization time"). **Dynamic** in *when* it
   happens, **deterministic** in *how*.

3. **Pre-compiled** — after first enactment, a registry/service caches a
   ready-to-go instantiation so subsequent enactments skip re-deriving.
   **Dynamic** in *that it is produced on first use*, **deterministic** in
   *that it is reproducible*.

### The sticky connection

A connection that persists across enactments — a **stable edge** in the
composition. It is part of what makes the pre-compiled form reusable. This is
FBP's "sticky connection": the connection is sticky because it survives
re-enactment of the network.

### Adaptive pre-compilation (liveness-driven, self-optimizing)

The system does not merely "pre-compile after first instance." It runs a
**feedback loop**: observe usage after first liveness → analyze (dynamic) →
decide what to pre-compile → pin it as a ready-to-go artifact.

- **Dynamic:** the usage observation, the analysis, the *decision* about what
  to pre-compile.
- **Deterministic:** the pre-compiled artifact, the cache key, the invalidation
  rule, the audit of the decision.

So "the system decides what is pre-compiled and ready-to-go" is a
**deterministic function of (observed usage, policy)** — given the same usage
stats and the same policy, it decides the same thing. That is what keeps it
reproducible.

Three disciplines keep this from becoming a trust shortcut:

1. **Usage analysis is a *hint*, not an authority.** Runtime traffic is
   non-deterministic and changes. The analysis informs the decision, but the
   decision to pin is a deterministic, auditable, **reversible** choice — the
   same "hint → determinize → durable rule" pattern as the catalog and the
   router. If usage shifts, the decision is re-derived and the pin is evicted.

2. **Pre-compiling must not bypass the correctness spine.** It skips
   *re-deriving the composition*, but the enacted network still re-verifies
   every facet's value on the upward path. Pre-compiled ≠ pre-trusted.

3. **Cache invalidation is the load-bearing detail.** The pre-compile decision
   is keyed by the recipe's fingerprint *and* the usage signature it was
   derived from. If the recipe changes, or the usage signature drifts past a
   threshold, the pin is invalidated and re-derived. A stale pre-compile is
   worse than no pre-compile — it is a wrong optimization.

The elegant result: the system is **self-optimizing but still deterministic** —
it converges on "ready-to-go" for the hot paths without ever becoming
non-reproducible. The hot path is pinned; the cold path stays dynamic. That is
"dynamic as practicable, pinned where reproducibility is required" operating
*on the optimization itself*.

### Disciplines, so "pre-compiled" doesn't become a trust shortcut

- **The recipe is a deterministic spec, not a live-trusted feed.** Same north
  star as the catalog: versioned, reproducible, reviewed.
- **Pre-compiled = a cache of a reproducible derivation, not a shortcut.** Key
  the cache by the recipe's fingerprint; if the recipe changes, the cache
  invalidates.
- **Caching the instantiation must not bypass the correctness spine.** It skips
  re-deriving the composition, but the enacted network still re-verifies each
  facet's value on the upward path. Pre-compiled ≠ pre-trusted.
- **The compiled form is itself a deterministic artifact** — auditable and
  re-verifiable like any other resource.

## The router is the canonical case, not a special case

The router (pure `route()` core + thin agent facet) and the catalog side-car
are **components** in a network. The recipe composes them. The catalog is a
registry agent; the router is a component that queries it. So the router is not
an exception to these laws — it is the canonical instance of them.

## Open questions

- **Recipe provenance:** where do recipes come from, and what is the reviewed
  path that turns a dynamically-produced recipe into a durable, pinned spec?
- **Cache invalidation:** exactly what fingerprint keys the pre-compiled form,
  and what triggers recompilation? (Recipe fingerprint + usage signature; what
  drift threshold evicts a pin?)
- **Usage analysis:** how is usage observed (deterministically), and how does
  the analysis feed the pre-compile decision without becoming an authority?
- **Registry topology:** how do the recipe registry, the compiled-network
  registry, and the live enacted-tree registry relate, and who grants access to
  each?
- **Dynamic ceiling:** which layers are *never* dynamic (verification, audit,
  cache keys) — and is that list complete?