# AC Router — *"It's Electric!"* (design idea)

**Status:** idea / proposal — not yet implemented. Captured so it can be
sharpened against the design north star before any code.

## The one-line thesis

An OpenRouter-style model gateway, but built on the **deterministic-platform-first**
north star: the **router is a deterministic selector, never a correctness
authority.** It decides *which* model runs; it never decides whether the
answer is right.

## What it is

A smart, guarded router that:

1. Is **inherently deterministic as much as possible** — the *selection* is a
   pure, auditable decision; the model *output* remains a hint that is
   re-verified by the parent.
2. Is **dynamically aware of the model landscape** — the good open-source
   models, and the closed-source ones too (but discouraged).
3. **Associates models with purpose and domain** — a model is not a blob; it
   is a capability tagged by what it is good for.
4. Makes those models **available to LLM agents that need them** — the router
   is itself an agent in the tree (and a standalone callable).
5. Has **multiple inputs *and* multiple outputs** — each output consumer is
   allowed to select and use its preferred model for its particular task.

## Why it fits the north star (and where it must be disciplined)

The idea is aligned with the core rules, but only if we hold three lines:

- **Determinize the routing; never the output.**
  The routing decision is a deterministic function of
  `(task, domain, policy, catalog)` → `{model_id, reason, sources}`.
  The chosen model's output is still a **hint** — re-verified by the parent,
  carrying source references. A router's self-claimed "best model" is not
  conclusive on its own, exactly like a child's self-claimed `verified`.

- **The catalog is a durable, reviewed resource — not a live-trusted feed.**
  "Dynamically updated and aware of all models" must not mean *trust an
  untrusted live fetch*. A live model feed is non-deterministic and changes.
  The deterministic pattern already in the codebase applies: the live feed is
  a **hint** → derive a deterministic method → only irreducible residue goes
  to a human → the human's authorization becomes a **durable rule** that runs
  unattended after restart. So: a versioned, pinned `ModelCatalog` snapshot,
  with a reviewed ingestion path (authorize-once), not a raw scrape.

- **"Closed source discouraged" is a policy gate, not a soft preference.**
  "Discouraged" is too weak for a mission-critical system. Make it an explicit
  allowlist/denylist policy (cost, latency, provenance, license) that the
  router enforces deterministically and fails closed on.

## Architecture sketch (mapped to existing code)

**The key structural decision: separate the knowledge-base from the router.**
They have opposite natures and must not be fused:

- **The model knowledge-base (catalog) is *state*** — durable, versioned, changes
  over time, needs a single writer with a reviewed promotion path. State that
  needs a writer → it is an **agent** (the ``StoreAgent`` pattern).
- **The router is *logic*** — a pure, stateless, deterministic function. Logic
  that holds no state → it is a **capability** (like CPM), not an agent.

**But the router is *both* — an agent *and* a standalone callable.** These are
not in tension, because they are two *facets* of the same thing, not two
competing natures:

- **As a standalone callable** it is the pure, deterministic selection function
  `route(task, domain, policy, eligible_models) → {model_id, reason, sources}`.
  This is what makes it unit-testable, embeddable, and reusable outside the
  tree.
- **As an agent** it is that same function wrapped in the directive/response
  protocol — a first-class node in the tree that other agents delegate to over
  the normal channel. The agent facet adds *governance* (grants, audit,
  delegation, re-verification) without adding *judgment*.

The rule that keeps it safe: **the agent facet is a thin protocol wrapper over
 the pure callable.** The callable holds no state and no authority; the agent
facet only mediates access to it. The router never decides whether an answer is
right — it only selects which model runs, and the parent re-verifies the
output on the upward path.

| Piece | Role | Existing anchor |
|-------|------|-----------------|
| `ModelCatalogAgent` (the **side-car**) | Durable, versioned registry of models: id, provider, open/closed, purpose/domain tags, cost/latency, source refs. Single-writer, grant-bound reads, reviewed promotion path. Serves the router *and* operators/other agents/audit. | `StoreAgent` (single-writer, grant-bound) + `Rule`/`RuleSet` durable-rule pattern for the promotion path |
| `Router` (agent **and** standalone callable) | `route(task, domain, policy, eligible_models)` → deterministic `{model_id, reason, sources}`. Pure callable for embedding/testing; agent facet wraps it in the protocol for in-tree delegation. Fail-closed on no eligible model. No state, no authority. | a read-only capability (like CPM) exposed as a callable, plus an `Agent` subclass (like `ModelAgent`) for the protocol facet |
| `ModelAgent` | Executes the chosen model; output re-verified by parent, carries model id + routing source refs. | existing `ModelAgent` (kind `model`), `ModelProvider` opt-in hook |
| Policy | Allowlist/denylist; closed-source discouraged via policy; cost/latency constraints. | explicit grants, fail-closed |

```
caller agent
   │  query catalog side-car (delegate → grant-bound read)
   ▼
ModelCatalogAgent ──► eligible models
   │
   ▼
Router agent (wraps the pure route() callable)
   │  delegate to execute
   ▼
ModelAgent ──► output (a HINT) → parent re-verifies
```

### Why separate the KB from the router

1. **Independent lifecycle** — the catalog can be reviewed/promoted/retired
   without touching routing logic, and vice versa.
2. **Reuse** — the catalog is a shared resource: operators, other agents, and
   audit all query it, not just the router.
3. **Testability** — the router (pure) is unit-testable against a fixed catalog;
   the catalog (state) is testable for durability/idempotency/review.
4. **Security, structurally** — the side-car controls what models are even
   *known* (grant-bound reads, reviewed entries); the router can only select
   from what the catalog reveals.
5. **It structurally enforces "not a correctness authority"** — the catalog
   holds *facts*, the router holds *logic*, neither holds *judgment*.
   Correctness lives only in the parent's verifier on the upward path.

## The real differentiator vs OpenRouter

OpenRouter is a single request/response proxy. The AC Router is a **fan-out
arbiter**: multiple inputs and multiple outputs, where each output consumer
declares its preferred model for its task, and the router mediates the
selection deterministically. That is a genuinely different shape — a
marketplace/scheduler of model capabilities inside the governed tree, not a
pass-through.

## Open questions (to sharpen before building)

- **Catalog provenance:** where does the live model feed come from, and what
  is the reviewed ingestion path that turns a live hint into a durable,
  pinned entry in the side-car?
- **Deterministic arbitration:** when multiple output consumers have
  conflicting model preferences for a shared task, what deterministic rule
  resolves it (priority, cost, latency, explicit tie-break)?
- **Router as agent vs capability (resolved):** the router is **both** — a
  standalone, pure callable *and* an agent whose facet is a thin protocol
  wrapper over that callable. The callable holds no state and no authority;
  the agent facet only mediates access. The catalog is an **agent** (state
  needs a single writer).
- **Standalone package / own repo:** the router + smart model manager are
  being developed as a **separate package** — their own app, eventually its
  own repo — built and tested here first, then extracted. The deterministic
  core stays additive and protocol-clean so it can be lifted out cleanly.
- **Closed-source policy:** define the explicit gate (license, data-residency,
  cost) rather than a soft "discouraged."
- **Determinism ceiling:** the *selection* is deterministic; the *output*
  never is. Guardrails: never let the router's selection be conclusive on its
  own; the parent's verifier is the only authority on correctness.

## Honest limits / non-goals (for now)

- No live, untrusted model scraping — the catalog is a reviewed, durable
  resource.
- No relaxation of the correctness spine — a routed model's output is still
  re-verified by the parent.
- No auto-generated ids; the catalog is single-writer and fingerprint-idempotent.
- Additive-only public surface; the router is a selector, not a new trust
  authority.
- Built and tested in-tree first, then extracted to its own package/repo.