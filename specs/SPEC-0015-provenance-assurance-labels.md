---
id: SPEC-0015
title: Provenance, trust gates, and Assurance Labels
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Provenance, trust gates, and Assurance Labels

## Context

The full version admits components from several origins: authored releases,
appointed sources (web/RAG), generated/compiled/translated artifacts, and
discovered functionality from an internet-wide index. None of these may be
trusted implicitly. "Safe" is an overloaded word that a mission-critical platform
must never overclaim: you can place a **scoped, evidence-backed** label on
arbitrary code, never an **absolute** one.

## Decision

### Provenance classes and their trust gates

**Nothing executes before it is content-addressed; nothing is trusted before it is
verified or sandboxed-and-reviewed.**

- **Static** — signed and content-addressed at release. In the launch path.
- **Appointed** (web/RAG) — untrusted bytes from an untrusted origin. Verify
  against a signature/source allowlist, or run sandboxed with no capabilities.
  Optional at launch.
- **Generated / compiled / translated** — produced by a stochastic or
  transformation process (SPEC-0013). **Quarantine**; run only in the sandbox
  with **zero capabilities**; may not be pinned into a network until it passes
  deterministic verification and `review.v1` human review. Gated at launch.
- **Discovered** (internet index) — a **candidate only**. Realize by
  **wrap → content-address → sandbox → conformance → `review.v1`**. License and
  provenance are first-class recorded facts. Never auto-trusted; never in the
  launch path.

### Assurance Labels — a scoped, evidence-backed claim

"Safe" becomes a **tiered label scoped to a stated property**, carrying its
evidence; the label and evidence are content-hashed and recorded.

| Tier | Claim | Evidence |
| --- | --- | --- |
| **A0** | *Contained, unverified* | sandboxed, zero capabilities |
| **A1** | *No dangerous capabilities* | sound type/effect/capability analysis (no net/FS/exec in reachable code) |
| **A2** | *Conforms to contract* | conformance vectors pass on the typed interface |
| **A3** | *Property P proven* | SMT/Z3 proof under model M (bounded/decidable fragment), proof artifact stored |
| **A4** | *Human-attested* | `review.v1` approval |

Rules:

- **No artifact executes above A0 until it earns its level.**
- A label is a **claim plus evidence**, never an absolute "safe."
- **Auto-labeling at scale requires the isolated validator (L3).** Before it
  exists, promotion beyond A0/A1 is **human-gated** via `review.v1`.
- The reducer/analyzer/verifier toolchain is part of the TCB: pinned, signed,
  version-recorded, or the label is non-reproducible.

## Scope

- **In:** the provenance classes and their gates; the Assurance Label tiers and
  their evidence; the rule that labels are scoped and evidence-backed; the
  `review.v1` gate for promotion; license/provenance recording.
- **Out:** the specific analysis/reduction/verification algorithms and tools
  (SPEC-0017); the ABI (SPEC-0012); network trust (SPEC-0014); the isolated
  validator implementation (L3).

## Acceptance criteria

- [ ] Each provenance class has a documented, fail-closed trust gate.
- [ ] The A0-A4 Assurance Label tiers and their required evidence are frozen.
- [ ] Promotion above A0/A1 is human-gated until the L3 isolated validator
      exists.
- [ ] A label is recorded as (property, tier, evidence hash); no absolute "safe"
      claim is accepted.
- [ ] License and provenance are recorded for every non-static artifact.

## Progress

- **Appointed launch slice delivered (Layer 4).**
  `../conformance/contracts/appointed-v1.md` (revision 1) freezes the appointed
  provenance class and its **ordered, fail-closed trust gate**: an appointed
  component is admitted only through a host-configured **source allowlist** and a
  detached **Ed25519** signature over its exact bytes, runs **contained with zero
  capabilities** (the A0 tier), and records a scoped, evidence-backed **Assurance
  Label** (property, tier, source, license, artifact hash). Admission is
  idempotent. Implemented by the Python reference host and the Rust host;
  `../conformance/vectors/appointed-{fixtures,suite,lock}.v1.json` (**8 cases**)
  pass **8/8** on both (cross-runtime equivalence).
- **Out of scope here:** tiers A1–A4, `review.v1` promotion, and the
  generated/compiled/translated/discovered gates (SPEC-0017).

## Risks / invariants

- Respects Laws 1, 2, 5, 8, 11; fail-closed; no unverified success.
- Reduced/translated/generated artifacts are new artifacts and are untrusted
  until re-verified; reduction is not assumed semantics-preserving.
- Additive-only; no `src/` semantics change on acceptance of the spec alone.
