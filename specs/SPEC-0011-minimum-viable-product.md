---
id: SPEC-0011
title: Minimum Viable Product — definition and roadmap
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# Minimum Viable Product — definition and roadmap

## Context

"Minimum Viable Product" was used with **three different meanings** across the
specs and there was no single milestone of record:

- **SPEC-0002 §7** MVP = Phase 1 of the CBP component architecture. **0/6**
  acceptance items built.
- **SPEC-0007** MVP = the distribution spine. Mostly delivered; the live mirror
  and a committed umbrella `components.lock` remain open.
- **SPEC-0009** MVP = FBP/ABM conformance. **8/9** built; recorded/confidence-
  scored `model` components remain open.

This spec was the **contract of record** for the MVP, to be fixed in a dedicated
planning session. That session has now run; its outcome is recorded below.

## Decision (planning session outcome)

**MVP means Minimum Viable Product, literally**, but the session fixed the target
as the **full version**, not a crippled subset: the intention is to make the
product live, with the Core public repo as a *reflection* that helps build it.

**Settled.**

- **"Viable" and the judge.** A **clean-checkout, offline, deterministic,
  end-to-end demonstration** reproducible by an independent operator/CI, whose run
  is the record (Law 12). The demo: author a network, boot a shell from a
  **signed lock**, execute **typed-port dataflow**, verify **every edge**, and
  **replay** an identical trajectory; a `model` component in the graph is
  **recorded and confidence-scored**.
- **Distribution element.** Signed lock via the **offline directory source** with
  **real local signing (minisign/gpg)**; the **lock-level signature is verified
  at boot**, and a **`design/network → pin → components.lock` producer** is added.
  The live self-hosted mirror is deferred (post-MVP).
- **"Minimum" by exclusion.** Out of the launch: SPEC-0002 items 2-3 (the
  `Agent`→`Component` adapter; removal of `_REGISTRY`/`_child_class_for`),
  SPEC-0002 item 6 (`inproc`-only), SPEC-0008 (n8n, pinning, envelope limits),
  SPEC-0010 (all), and `skill.v1`.
- **Editions.** Fixed in **SPEC-0016**: Core (public Python reflection) / Pro
  (private, optimization) / Enterprise (private, components) / cloud (deployment
  adapter over one semantics). Tiers differ only in capacity, performance,
  deployment, governance, support, and UI — never in correctness.
- **The new frozen architecture** is captured in the sibling specs:
  **SPEC-0012** (Component ABI), **SPEC-0013** (cross-runtime + conformance
  vectors), **SPEC-0014** (network execution and trust), **SPEC-0015**
  (provenance, trust gates, Assurance Labels), **SPEC-0017** (parked frontier
  workstreams).

**Open questions, now resolved.**

1. **"Viable"** — decided: clean-checkout offline reproducibility (above).
2. **Distribution contradiction** — decided: offline directory source + real
   local signing; live mirror deferred.
3. **"Minimum" by exclusion** — decided (above).
4. **`review.v1` + `confidence`** — parked to a dedicated formal-semantics
   session (**SPEC-0017**); the launch ships a minimal deterministic scorer and a
   deterministic formalizer at the model boundary.

## Scope

- **In:** the MVP definition, its acceptance criteria, and the roadmap to it.
- **Out:** implementation during the planning session; any change to
  `PRINCIPLES.md`; any change to Manager orchestration/verification/policy/
  envelope/accounting semantics.

## Acceptance criteria

- [x] The MVP is defined: *viable* (outcome + judge) and *minimum* (explicit OUT
      list) are both fixed.
- [x] The MVP demo is frozen as the acceptance scenario, with its distribution
      element resolved.
- [ ] Every MVP capability has a deterministic test; the operator's run and CI
      are the record (Law 12).
- [ ] The spec status record is corrected (SPEC-0009 understated; the
      `implemented` specs' checkboxes un-ticked).
- [x] This spec moves `draft` → `accepted` once the definition is fixed.

> **Status (authoring).** The "viable" demonstration is now deterministic and
> tested offline (`tests/test_viable_demo.py`, `examples/viable_demo.py`): boot a
> signed `components.lock` → typed-port `network.v1` dataflow over the verified
> entries → durable-ledger replay, with a recorded, confidence-scored `model`
> node (`cbp/boot_network.py`). The umbrella lock is committed test-signed; the
> operator re-signs it with the real key (key gate). The remaining capability
> tests are tracked under SPEC-0002/SPEC-0008/SPEC-0009; the operator/CI run
> remains the record (Law 12).

## Risks / invariants

- **Do not trade determinism for scope.** Every MVP capability stays deterministic
  and fail-closed (Laws 1, 2, 8).
- **Additive-only.** MVP advances by adding the new CBP spine; the legacy path
  stays untouched unless a step proves equivalence (SPEC-0002 §13).
- **No MVP hostage to L3.** The isolated holdout validator is a separate,
  declared-gated program (SPEC-0017); the evidence bar is the operator/CI run.
- Respects Laws 1-13; changes no `src/` semantics on its own.
