---
id: SPEC-0011
title: Minimum Viable Product — definition and roadmap
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Minimum Viable Product — definition and roadmap

## Context

"Minimum Viable Product" is used with **three different meanings** across the
specs and there is no single milestone of record:

- **SPEC-0002 §7** MVP = Phase 1 of the CBP component architecture (contracts,
  the `Agent`→`Component` adapter, globals removed, CPM/replay holdout,
  `confidence` + Review, `inproc`-only). **0/6** acceptance items are built.
- **SPEC-0007** MVP = the distribution spine (boot-from-lock + process isolation
  + signing). Mostly delivered; the **live self-hosted mirror** and a committed
  umbrella `components.lock` are open.
- **SPEC-0009** MVP = FBP/ABM conformance. **8/9** items built (slices 1–6); only
  recorded/confidence-scored `model` components are open.

Spec `status` is `draft | accepted | implemented` (`specs/README.md`); `accepted`
means the **design is approved**, not built. So SPEC-0002 is honestly `accepted`
with unchecked boxes, while SPEC-0009 — **8/9 built** — is understated as
`draft`.

This spec is the **contract of record** for the MVP. Its definition is **TBD**:
it is to be fixed in a dedicated planning session that grills the operator. The
settled parts and the open questions are below.

## Decision

**MVP means Minimum Viable Product, literally.** Its scope is fixed by deciding,
together, what is *viable* (the outcome and its judge) and what is *minimum* (by
exclusion).

**Settled (planning pass).**

- **The demo that proves it.** Author a `network.v1` document → boot a shell from
  a **signed lock** → execute **typed-port** dataflow → every edge **verified** →
  **replay** yields an identical trajectory; a `model` component in the graph is
  **recorded and confidence-scored**.
- **Session outputs.** (1) correct the status record; (2) this spec; (3) refresh
  `HANDOFF.md`.
- **Evidence bar.** Parked to the planning session (suite vs holdout/L3).

**Open questions (the planning session's agenda).**

1. **"Viable"** — to do what, and judged by whom? (the demo on a clean offline
   checkout; a second operator reproducing it; or a real user task.)
2. **Distribution element of the demo (⚠ contradiction).** The demo says "signed
   lock", but the committed umbrella `components.lock` is blocked on the **live
   mirror**, which does not exist. Choose: a signed lock via the delivered
   **offline directory source**; the live mirror; or drop distribution from the
   demo.
3. **"Minimum" by exclusion** — confirm OUT of MVP: SPEC-0002 items 2–3 (the
   `Agent`→`Component` adapter; removal of the `_REGISTRY` global and the
   `_child_class_for` map), SPEC-0002 item 6 (`inproc`-only), SPEC-0008 (n8n,
   pinning, envelope limits), SPEC-0010 (all), `skill.v1`.
4. **`review.v1` + `confidence` semantics** — the design-heavy core the demo
   drags in (also SPEC-0002 item 5 and SPEC-0009's last item).

## Scope

- **In:** the MVP definition, its acceptance criteria, and the roadmap to it.
- **Out:** implementation during the planning session; any change to
  `PRINCIPLES.md`; any change to Manager orchestration/verification/policy/
  envelope/accounting semantics.

## Acceptance criteria

- [ ] The MVP is defined: *viable* (outcome + judge) and *minimum* (the explicit
      OUT list) are both fixed.
- [ ] The MVP demo (above) is frozen as the acceptance scenario, with its
      distribution element resolved (open question 2).
- [ ] Every MVP capability has a deterministic test; the operator's run and CI
      are the record (Law 12).
- [ ] The spec status record is corrected (SPEC-0009 understated; the
      `implemented` specs' checkboxes un-ticked).
- [ ] This spec moves `draft` → `accepted` once the definition is fixed.

## Risks / invariants

- **Do not trade determinism for scope.** Every MVP capability stays deterministic
  and fail-closed (Laws 1, 2, 8).
- **Additive-only.** MVP advances by adding the new CBP spine; the legacy path
  stays untouched unless a step proves equivalence (SPEC-0002 §13).
- **No MVP hostage to L3.** The isolated holdout validator is a separate,
  declared-gated program; the evidence bar is decided in the planning session.
- Respects Laws 1–13; changes no `src/` semantics on its own.
