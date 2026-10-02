---
id: SPEC-0022
title: Component-provided UI — a target-agnostic UI ABI, dynamic assembly, and security
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Component-provided UI — a target-agnostic UI ABI, dynamic assembly, and security

## Context

The platform already has a real, dependency-free front door: `cbp/web.py` — a
stdlib `http.server` landing page over an in-process `CbpDriver`, loopback-only,
read-only by default, with explicit grants for persistence, security headers, SSE,
and a catalog of the external edge transports (ACP/MCP) at `/surfaces`. It
projects the live tree, ledger, activity feed, domain registry, artifact vault,
expert costing, model catalog, saved component networks and their deterministic
diagrams, and it runs typed artifacts and networks through the verified spine.
The diagram ([`cbp/diagram.py`](../src/agent_centric/cbp/diagram.py)) already
proves a surface can be a **pure projection of a pinned document**.

Two forces now shape the next step:

1. **"Everything is a component"** ([`PRINCIPLES.md`](../PRINCIPLES.md) Laws 3,
   2a). A surface must be an ordinary, typed, content-addressed component, and the
   front end must be a **composition** of them. Components should be able to
   **bring their own UI** so that discovered/appointed components present
   themselves without shell changes.
2. **Targets beyond the browser.** We must be able to serve **any UI target**:
   **CLI, web, native OS, and embedded** (and future targets). A UI must therefore
   be **target-agnostic** and bind to a chosen target at the edge.
3. **UI is a deployment axis, never semantics**
   ([`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)). Editions differ only
   in capacity/performance/deployment/governance/support/**UI**; a view can never
   change meaning or weaken verification. **Core stays headless**; pro/enterprise
   carry the UI build.

Edge transports (MCP/ACP) are for human/authoring interaction and are **never the
execution path**; a browser or CLI is an edge, not a node in the execution tree
([`SPEC-0002`](SPEC-0002-cbp-component-architecture.md)). Component-supplied UI
code is **untrusted code**, and the governing order is fixed: **security first,
dynamicism second** — where they conflict, security wins (fail-closed).

## Decision

1. **The UI is an edge, not a node.** The tree is unchanged: renderers,
   transports, and the assembly shell are **harness-resident edges** that observe
   the tree and submit directives. They are never components executing inside the
   tree and never a privileged node type.

2. **`ui.v1` — a target-agnostic UI contract.** A **UI component** is a declared
   presentation with a typed, canonical, content-addressable manifest:
   - `id`, **UI-ABI version**, and the **projections** it consumes (by document
     schema: `cbp.diagram.v1`, `review.v1`, ledger, activity, transparency,
     provider evidence, domain/artifact readouts, …);
   - the **directives** it may emit and the gate each requires;
   - **capability grants** (least privilege; default deny) and **secret refs
     only**;
   - **slots** (composition points) and **design tokens** (theming by reference);
   - zero execution capability and no ambient authority.
   It is **target-neutral**: the same manifest binds to any **UI target**.

3. **UI targets are pluggable bindings.** A **UI target** is a renderer +
   sandbox for a medium:
   | target | medium | binding example |
   | --- | --- | --- |
   | `web` | browser | standard **web components** (e.g. a Stencil build output) |
   | `cli` | terminal | a text/ANSI renderer (declarative tables/forms) |
   | `os` | native window | a native toolkit adapter |
   | `embedded` | constrained device | a minimal/declarative renderer |
   A target implements the UI ABI; it does not change the UI component's meaning.
   A component may ship one or more **target bundles** (each content-addressed) or
   a target-neutral declarative descriptor compiled to a target.

4. **Components bring their own UI — additively.** A behavior component may
   declare a **pinned reference** to one or more UI components (its default
   presentation per target). The UI is a **separate** artifact: changing a view
   never changes the behavior component's content address or verified semantics
   ([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md), SPEC-0016). A component
   at rest still has no execution edges; the UI ref is presentation metadata.

5. **Dynamic assembly, pinned before render.** The front end serves a
   **content-addressed UI composition** of the UI components applicable to the
   requested target. Dynamic/admitted components may contribute UI, but a UI
   composition is **pinned before it renders** (the SPEC-0014 pin rule): the same
   pinned composition renders identically. Unknown or ABI-incompatible UI is
   **skipped, not executed**; the surface degrades to the raw projection rather
   than failing the whole UI.

6. **Surfaces are pure projections.** Rendering a view is a **pure function of a
   pinned document**. Identical pinned state yields a byte-identical projection; a
   view never recomputes platform semantics and never invents state. Every
   projection carries its **verification/provenance status**, which the renderer
   must display honestly (an unverified claim never renders as verified).

7. **Interactions are directives.** Every interactive action is a
   **content-addressed intent** submitted to the edge and executed through the
   **same verified spine** (validate → canonicalize → pin → verify → run). A UI
   component may submit **only** the directives its manifest declares (default
   deny). Irreversible acts (accept, author, provision, publish) keep their
   existing operator gates; the target never mutates platform state directly.

8. **SECURITY IS PARAMOUNT — mandatory sandboxing, tiered by provenance.** UI code
   runs behind a **capability sandbox** with **zero ambient authority** (no DOM
   beyond its own boundary, no network, no filesystem, no host API). The tier is
   set by provenance ([`SPEC-0015`](SPEC-0015-provenance-assurance-labels.md)):
   - **trusted/static** (operator-signed): may hold tightly-scoped declared
     capabilities, still no ambient authority;
   - **appointed** (allowlist + detached signature): **A0** — zero capabilities,
     all I/O mediated by the edge;
   - **discovered/generated**: strongest sandbox, gated, never auto-trusted.
   Shadow DOM / web components are **encapsulation, not isolation**; the target's
   host boundary (e.g. an iframe with `sandbox` and no `allow-same-origin`, a
   worker/wasm capability runtime, or an OS-level sandbox) is the real boundary.

9. **Verify before execute; strict negotiation.** A UI bundle is
   content-addressed and signed (or attested) and is **verified before it loads**;
   an unknown **UI-ABI version** or a failed signature/hash check **refuses**
   (never a guessed render). The renderer is version-pinned as TCB and recorded.

10. **No secrets, ever, in UI.** Secret material never enters a projection, a
    manifest, a snippet, or a record; the edge resolves secret **refs** server-side
    and injects only the minimum non-secret projection. A UI component cannot read
    the platform's private inventory.

11. **Transports are explicit and separate.** Read + stream over target-native
    transports (HTTP/SSE for web, pipes/stdio for CLI, IPC for native); MCP/ACP
    for agent hosts. Transports are passive edges: they carry projections and
    directives and never decide. No transport is on the execution path.

12. **Least privilege and honesty.** Capability grants are declared and enforced;
    a projection the manifest did not declare is withheld. Assurance labels are
    surfaced as scoped, evidence-backed facts, never as absolute trust.

13. **Deployment, editions, branding.** Multi-tenant/branding are configuration
    ([`SPEC-0018`](SPEC-0018-service-host-provisioning-and-continuous-deployment.md)
    §8); theming is design tokens. Core remains headless and dependency-free; the
    UI build (framework, bundler, target bindings) lives in pro/enterprise.

14. **Development model.** UI is developed as a `ui.v1` component against the
    **local CD substrate** (loopback Gitea + OpenBao), rendered by a target in a
    sandbox, tested offline against pinned projection fixtures, then wrapped,
    content-addressed, and pinned in a UI composition. The component CI stays
    **hermetic and network-free**; no cloud API and no framework dependency enters
    the core gates; the UI build runs in its own pipeline.

15. **Additive migration.** The current `cbp/web.py` is the **`web` v0** target
    surface: the spec extracts its surfaces into declared UI components and a
    pinned composition without changing existing routes or behavior.

## Artifacts

- `conformance/contracts/ui-v1.wit` — the typed, target-agnostic UI world
  (`cbp:ui@1.0.0`); `conformance/contracts/UI-ABI.md` — the normative companion
  (targets, projections/directives, sandbox, assembly); `ui.lock.v1.json` — the
  content address via the pinned `wasm-tools` resolver.
- `core/src/agent_centric/contracts/ui.py` — the pure, canonical,
  content-hashable `ui.v1` manifest and the default-deny boundary helpers.

## Scope

- **In:** the target-agnostic `ui.v1` contract; UI targets (web/cli/os/embedded)
  and their binding/sandbox requirements; component-provided UI (pinned refs);
  dynamic, pin-before-render assembly; the projection boundary (pinned,
  provenance-honest documents); the directive boundary (content-addressed intents
  through the verified spine); mandatory provenance-tiered sandboxing; verify-
  before-execute and strict ABI negotiation; the local-substrate development
  workflow; additive extraction of `cbp/web.py` surfaces.
- **Out:** any new node kind or execution capability in the UI; changing
  verification semantics; a framework dependency in **core**; cloud APIs or
  network in the component CI; authN/Z, billing, or branding implementation
  (deployment/enterprise, SPEC-0016/SPEC-0018); MCP/ACP on the execution path;
  changing any existing route's meaning; trusting UI code by default.

## Acceptance criteria

- [ ] `ui.v1` exists in `contracts/`, is additive, canonical, content-hashable, and
      **target-agnostic**.
- [ ] At least **two** UI targets (e.g. `web` and `cli`) implement the UI ABI and
      render the same UI component to their own medium deterministically.
- [ ] A UI component manifest declares projections, directives, capability grants,
      slots, tokens, and secret **refs only**; it can hold no execution capability.
- [ ] A behavior component can declare a **pinned UI ref** without changing its own
      content address or verified semantics.
- [ ] Component-supplied UI runs behind a **capability sandbox** with zero ambient
      authority; the provenance tier (trusted/appointed/discovered) is enforced and
      recorded.
- [ ] A UI bundle is content-addressed and signature/attestation-verified **before**
      it loads; an unknown UI-ABI version or a failed check refuses.
- [ ] Rendering is a pure function of a pinned document: identical pinned state
      yields a byte-identical projection (proven by test).
- [ ] Every interactive action is a content-addressed intent through the verified
      spine; an undeclared directive is refused; the target mutates nothing directly.
- [ ] No secret material appears in any projection, manifest, snippet, or record.
- [ ] A UI composition is pinned before it renders; an unknown/incompatible UI is
      skipped and the surface degrades to the raw projection.
- [ ] The edge binds loopback by default; the component CI stays hermetic (no
      cloud, no browser framework, no new network dependency).

## Risks / invariants

- **Security over dynamicism.** When the two conflict, refuse (fail-closed). UI
  code is untrusted until proven otherwise by provenance + sandbox.
- **Laws 2/2a/3.** Deterministic control plane; one abstract component concept; the
  UI is a pure projection plus declared directives.
- **Law 6.** Every surface carries the content hash of what it projects; screens
  are reconstructible from the ledger.
- **Law 7.** UI components hold capability grants and refs; targets hold no ambient
  authority.
- **Law 8.** An undeclared directive, an unknown ABI, or a failed verification
  fails explicitly, never a partial or guessed success.
- **SPEC-0013/SPEC-0016.** One verified semantics; UI never affects execution or
  cross-runtime equivalence; editions differ only in UI/deployment.
- **SPEC-0014.** A (dynamic) UI composition is pinned before it renders.
- **SPEC-0015.** Provenance drives the sandbox tier; assurance labels are scoped
  and evidence-backed.
- **SPEC-0018 §8/§11.** Branding/tenancy are configuration; the component CI stays
  hermetic.
- **Laws 11/12/13.** Whole-file replacement, faithful test reporting, continuous
  commit/push; no gate bypass.
