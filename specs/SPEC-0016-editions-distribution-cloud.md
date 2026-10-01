---
id: SPEC-0016
title: Editions, distribution, and the cloud offering
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Editions, distribution, and the cloud offering

## Context

The MVP planning session fixed an edition model and a distribution posture. The
current distribution spine (SPEC-0007) already provides signed, content-addressed
component bundles, a deterministic resolver, offline directory and git sources,
and boot-from-lock. There is no edition concept, no committed umbrella
`components.lock`, and no cloud offering. "One verified semantics across editions"
must be preserved rather than assumed.

## Decision

### Editions

- **Core** — the public GitHub repository. A **Python reflection** of the Rust
  core: headless, dev-facing, no UI; a **proof of concept** that accompanies the
  whitepaper (written last). It demonstrates the system and links to the demo.
- **Pro** — a **private** edition geared to **optimization** (the Rust core).
- **Enterprise** — a **private** edition with enterprise-specific, correlated
  components.
- **Cloud offering** — a hosted **demo** on operator infrastructure; users sign
  up and get a demo. It is a **deployment adapter** that **filters components**
  for use-case needs. Multi-tenancy, accounts, isolation, billing, and compliance
  are **Enterprise-layer** concerns and must never enter core verification
  semantics.

### Differentiation axes

Editions differ only along these axes:

1. **UI / wizards** — Core: none; Demo/Pro/Enterprise: richer.
2. **Formal-semantics richness** — Core: deterministic schema validator at the
   model boundary; Pro/Enterprise: ontology + closed shapes + entailment closure
   (SPEC-0017).
3. **Runtime / scale** — Core: Python, single-node; Pro+: Rust core,
   parallel/multi-node.
4. **Distribution / trust** — Core: offline directory source + local signing;
   Pro/Enterprise: live private mirror.
5. **Governance** — Core: single operator; Enterprise: multi-tenant,
   policy/audit federation, SLA.
6. **Licensing / support**.

**Invariant: identical verified semantics at every tier.** Tiers differ only in
capacity, performance, deployment, governance, support, and UI richness — never
in correctness behavior.

### How an edition is expressed

**An edition is a manifest that selects and binds components — not a flag inside
a component.** One version of each component, one semantics; the edition manifest
chooses the set. In-component feature flags that hide divergence are **rejected**.
Private forks may add components, UI, edition manifests, and configuration, but
**core files stay upstream-pinned by `lock_hash`**. A Rust reimplementation is a
**contract-conformant reimplementation** verified by the shared conformance
vectors (SPEC-0013), never a free fork of core internals.

## Scope

- **In:** the Core/Pro/Enterprise/cloud model; the six differentiation axes; the
  "edition = manifest selecting components" rule; the fork policy; the
  one-semantics invariant; the cloud offering as a deployment adapter.
- **Out:** billing/auth/multi-tenancy implementation (Enterprise); the live
  mirror implementation; the whitepaper; the conformance suite itself
  (SPEC-0013).

## Acceptance criteria

- [ ] Core / Pro / Enterprise / cloud are defined, including the Core-is-a-
      reflection posture and the whitepaper-comes-last stance.
- [ ] The six differentiation axes are frozen.
- [ ] An edition is expressed as a **manifest selecting components**; in-component
      feature flags are documented as disallowed.
- [ ] The one-verified-semantics invariant is stated and is testable via the
      conformance suite.
- [ ] The cloud offering is defined as a deployment adapter over one semantics,
      with multi-tenancy confined to the Enterprise layer.

## Risks / invariants

- Respects Laws 1, 2, 5, 8; local-first; additive-only.
- A private fork that alters core verification semantics is a violation, not a
  feature; the conformance suite is the guard.
- The public repo must never contain private keys, private origins, or pro code.
- No `src/` semantics change on acceptance of the spec alone.
