---
id: SPEC-0022
title: Front end as components — UI contract, projections, and development model
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Front end as components — UI contract, projections, and development model

## Context

The platform already has a real, dependency-free front door: `cbp/web.py` — a
stdlib `http.server` landing page over an in-process `CbpDriver`, loopback-only,
read-only by default, with explicit grants for any persistence, security headers,
SSE streaming, and a catalog of the external edge transports (ACP/MCP) at
`/surfaces`. It reflects the live tree (`/state.json`), the durable ledger
(`/ledger`), the operator activity feed (`/activity`), the domain registry and
artifact vault (`/domains`, `/artifacts`), expert costing (`/experts`), the model
catalog (`/catalog`), saved component networks and their deterministic diagrams
(`/network/*`, `/network/diagram/<name>`), and it runs typed artifacts and
networks through the verified spine (`/orchestrate`, `/network`). The landing page
is the "easy UX" the architecture always intended; it is also growing
organically.

That growth now needs a **contract**. Two facts frame it:

1. **"Everything is a component"** ([`PRINCIPLES.md`](../PRINCIPLES.md) Laws 3,
   2a). The UI cannot be a privileged exception with bespoke, ungated logic; each
   surface must be an ordinary, typed, content-addressed component, and the front
   end must be a **composition** of them.
2. **UI is a deployment axis, never semantics**
   ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)). Editions differ only
   in capacity/performance/deployment/governance/support/**UI**; a view can never
   change what the platform *means* or weaken verification.

Edge transports (MCP/ACP) are for human/authoring interaction and are **never the
execution path**; a browser is an edge, not a node in the execution tree
([`SPEC-0002`](SPEC-0002-cbp-component-architecture.md),
[`SPEC-0018`](SPEC-0018-service-host-provisioning-and-continuous-deployment.md)).
The diagram ([`cbp/diagram.py`](../src/agent_centric/cbp/diagram.py)) is the
existing proof that a surface can be a **pure projection of a pinned document**.

## Decision

1. **The front end is an edge adapter, not a node.** The tree is unchanged: the
   browser, the HTTP/SSE transport, and the rendering server are **harness-resident
   edges** that observe the tree and submit directives. They are never components
   executing inside the tree, and never a privileged node type.

2. **`ui.v1` — an additive UI component contract.** A **UI component** is a
   declared surface with a typed, canonical, content-addressable manifest:
   - `id`, `contract` version, and declared **read projections** (by document
     schema: `cbp.diagram.v1`, `review.v1`, ledger, activity, transparency,
     provider evidence, domain/artifact readouts, …);
   - declared **directives** (the content-addressed intents it may submit) and the
     gates each requires;
   - **capability grants** (least privilege; default deny) and **secret refs only**
     (never secret material);
   - no direct access to component internals and no execution capability.
   `ui.v1` is added additively to `contracts/`; no new node kind is introduced.

3. **The front end is a pinned UI composition.** The shell serves a
   **content-addressed composition of UI components** (a UI network): adding or
   changing a surface is adding or changing a component, pinned and signed like
   any other ([`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md)).
   The server hosts the composition; it does not hard-code views.

4. **Surfaces are pure projections.** Rendering a view is a **pure function of a
   pinned document**. Identical pinned state yields a byte-identical projection;
   a view never recomputes platform semantics and never invents state. The read
   path is read-only w.r.t. durable state, exactly like the diagram route.

5. **Interactions are directives.** Every interactive action is a
   **content-addressed intent** submitted to the edge and executed through the
   **same verified spine** as everything else (validate → canonicalize → pin →
   verify → run). The browser **never mutates directly**; irreversible acts
   (accept, author, provision, publish) keep their existing operator gates. A UI
   component may submit only the directives its manifest declares (default deny).

6. **Determinism and replay.** A rendered surface carries the content hash of the
   document(s) it projects and its own projection hash. The system records every
   UI-relevant transition, so a screen is reconstructible from the ledger
   ([`PRINCIPLES.md`](../PRINCIPLES.md) Laws 2, 6).

7. **Transports are explicit and separate.** Read + stream over HTTP/SSE for
   browsers; MCP/ACP for agent hosts. Transports are passive edges: they carry
   projections and directives and never decide. No transport is on the execution
   path.

8. **Security posture is inherited, not invented.** Loopback by default;
   read-only by default; persistence only by **explicit grant**; conservative
   response hardening; **no secrets on the page**; capability-scoped directives;
   and at the multi-tenant/deployment boundary, authN/Z and branding are
   configuration ([`SPEC-0018`](SPEC-0018-service-host-provisioning-and-continuous-deployment.md)
   §8), never a code fork and never part of verification semantics.

9. **Development model.** Front-end development runs against the **local CD
   substrate** (`hosts/local/`, loopback Gitea + OpenBao) with `cbp-web --reload`.
   A surface is developed as a UI component, wrapped, content-addressed, tested
   offline against fixtures, and pinned in the UI composition. The component CI
   stays **hermetic and network-free**; no cloud API and no framework dependency
   enters the gates.

10. **Additive migration.** The current `cbp/web.py` is `ui.v1`'s **v0**: the spec
    extracts its surfaces into declared UI components and a pinned composition
    without changing existing routes or behavior. Existing routes become the v0
    component set; new surfaces are added only as components.

## Scope

- **In:** the `ui.v1` UI-component contract; the "front end is a pinned UI
  composition" model; the projection boundary (pinned documents only); the
  directive boundary (content-addressed intents through the verified spine); the
  edge/transport split (HTTP/SSE, MCP/ACP); the security posture (loopback,
  read-only default, explicit grants, no secrets); the local-substrate development
  workflow; additive extraction of `cbp/web.py`'s surfaces into UI components.
- **Out:** any new node kind or execution capability in the UI; changing
  verification semantics; a JavaScript framework dependency in the core; cloud
  APIs or network in the component CI; authN/Z implementation, billing, or
  branding (deployment/enterprise, SPEC-0016/SPEC-0018); MCP/ACP on the execution
  path; changing any existing route's meaning.

## Acceptance criteria

- [ ] `ui.v1` exists in `contracts/`, is additive, canonical, and content-hashable.
- [ ] A UI component manifest declares read projections, directives, capability
      grants, and secret **refs only**; it can hold no execution capability.
- [ ] Each current `cbp/web.py` surface is described as a UI component; the set is
      pinned in a signed UI composition that resolves and boots deterministically.
- [ ] A surface's rendering is a pure function of a pinned document: identical
      pinned state yields a byte-identical projection (proven by test).
- [ ] Every interactive action is a content-addressed intent executed through the
      verified spine; the browser mutates nothing directly; an undeclared
      directive is refused (default deny).
- [ ] The read path performs no durable mutation; persistence requires an explicit
      grant.
- [ ] No secret material appears in any projection, manifest, page, or record.
- [ ] The server binds loopback by default; the component CI remains hermetic
      (no cloud, no browser framework, no new network dependency).
- [ ] Adding a surface requires no change to the harness — only a new UI component
      and a re-pin.

## Risks / invariants

- **Laws 2/2a/3 (deterministic, abstract, agent-centric).** The UI is a pure
  projection plus declared directives; determinism is preserved end to end.
- **Law 6 (auditability).** Every surface carries the content hash of what it
  projects; screens are reconstructible from the ledger.
- **Law 7 (least privilege).** UI components hold capability grants and refs; the
  browser holds no ambient authority.
- **Law 8 (explicit, audited failure).** An undeclared/ungated directive and an
  unknown projection fail closed, never a partial or guessed success.
- **SPEC-0016.** UI is a **deployment** axis; a view never changes meaning.
- **SPEC-0015.** Assurance labels (tier/evidence) are surfaced honestly as
  scoped, evidence-backed facts — never as absolute trust.
- **SPEC-0018 §8/§11.** Multi-tenant/branding are configuration; the component CI
  stays hermetic.
- **Laws 11/12/13.** Whole-file replacement, faithful test reporting, continuous
  commit/push; no gate bypass.
