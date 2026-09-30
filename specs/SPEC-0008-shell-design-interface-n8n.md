---
id: SPEC-0008
title: Shell design interface and the n8n authoring adapter
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Shell design interface and the n8n authoring adapter

**Operator intent (recorded).** The **shell** (the tree's root node) is also the
human-facing **interface to the system**, and humans will design CBP process
graphs with **n8n**. This spec fixes the boundary: n8n is a **design-time
authoring surface**; the harness stays the **local-first, deterministic
executor**. It is target architecture with phases; no claim exceeds what the
operator's suite proves.

## Context

- The shell is already the root **component** of the tree — not an external
  orchestrator — see [`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md)
  §1 and [`docs/agent/architecture.md`](../docs/agent/architecture.md). Phase 2
  boots a tree from a pinned lock (`cbp/component_boot.py`).
- The target architecture already names the graph document **`network.v1`** —
  authored statically or dynamically, then frozen and content-hashed before
  execution — see [`SPEC-0002`](SPEC-0002-cbp-component-architecture.md) §3/§6.
- n8n is a widely used, self-hostable workflow editor and a good **human design
  surface**, but it is a network application with its own runtime and
  credentials; it must never sit on the execution path (Law 5).
- Missing: a defined **design interface** (what a designer submits, what the
  shell validates and returns) and an **adapter contract** (how n8n maps to
  `network.v1`), so implementation can proceed without inventing scope.

## Decision

### 1. Two planes, one boundary

| Plane | Runs where | Artifact | Never does |
| --- | --- | --- | --- |
| **Design** (authoring) | human tools, incl. n8n | a `design.v1` document | execute components; hold runtime credentials; bypass validation |
| **Execution** | the harness, local-first | a verified `components.lock` + booted tree | call n8n; require network; accept an unvalidated design |

The **shell** is the single boundary: it is the root component *and* the surface
through which a design is validated, compiled, pinned, signed, and booted.

### 2. The design artifact is declarative and content-hashed

A `design.v1` document wraps a `network.v1` graph and a list of *requested*
component references (name + tag/range/commit). Tags/ranges are resolved to
immutable commits **only at pin time** (the SPEC-0007 tag rule); a design carries
no execution authority and never floats at run time.

### 3. The n8n adapter is out-of-process and optional

A separate adapter translates n8n workflow JSON ⇄ `design.v1`. It is an
**adapter** (the non-goals prefer adapters over core changes): the harness never
imports n8n, and n8n never enters the execution path. It is self-hosted, and CI
tests it with fixtures — never the network.

### 4. Fail-closed validation

A design is untrusted input. Validation (schema, graph acyclicity, port/edge
integrity, component availability, envelope limits) is deterministic and
fail-closed; an invalid design is an explicit error, never a partial plan. A
design can only **reference** components that resolve, verify, and sign under
SPEC-0007 — it can never introduce executable code, an unsigned artifact, or a
grant the operator did not pin.

### 5. One canonical round-trip

`design.v1 → n8n → design.v1` (and the reverse) must be **stable**: the same
design re-imports to the same canonical bytes (`design_hash`), so a designer's
work is reproducible and diffable.

## Scope

- **In:** `design.v1` (additive contract); the shell design interface
  (`validate` / `compile` / `pin` / `record`); the n8n adapter mapping and
  round-trip contract; validation and its fail-closed gates; the design/execution
  boundary and its guard.
- **Out (explicit non-goals):** putting n8n (or any network tool) on the run path
  or giving CI network access; a new execution backend; changing Manager
  orchestration, verification, policy, envelope, or accounting semantics; new
  agents; operating or hosting n8n for the operator; cloud/remote n8n; granting a
  design any authority beyond the operator's pinned, signed components.

## Interface sketch (to be finalized in Phase 1)

```
design.v1: {
  schema: "design.v1",
  id, title,
  network: <network.v1>,                 # canonical graph (SPEC-0002 §6)
  components: [ {name, requested} ],     # tag|range|commit — resolved at pin time
  metadata: { ... }                      # non-executable annotations (author, notes)
}
design_hash = sha256(canonical_serialization)
```

Shell design interface (harness-owned, local):

1. `validate(design) -> report` — deterministic, fail-closed.
2. `compile(design) -> network.v1` — canonical document + `design_hash`.
3. `pin(design, source) -> components.lock` — resolve requested refs to commits
   (SPEC-0007 §4), verify, and build the lock.
4. `record(lock, signer) -> transparency entry` — sign + append (SPEC-0007 §6).
5. `boot` — Phase 2 `boot_from_lock`.

n8n mapping (adapter-owned): n8n node ⇄ component; n8n connection ⇄ edge; n8n
workflow setting ⇄ envelope/grant — all validated against `design.v1`.

## Phases

| Phase | Status | Deliverable |
| --- | --- | --- |
| 0 | **delivered** | this spec; the design/execution boundary and non-goals |
| 1 | roadmap | `design.v1` contract + `validate`/`compile` (offline, deterministic) |
| 2 | roadmap | n8n adapter import/export + canonical round-trip; fixture tests |
| 3 | roadmap | `pin`/`record` wiring: design → signed lock → boot |
| 4 | declared-gated | live self-hosted n8n integration (needs operator infra) |

## Acceptance criteria

- [ ] `design.v1` exists, is additive, canonical, and content-hashable.
- [ ] `validate` is deterministic and fail-closed on schema, cycle, port/edge,
      absence, and envelope violations — proven by tests.
- [ ] An identical design compiles to the same `design_hash`; pinning resolves
      refs to commits and never floats at run time.
- [ ] The n8n round-trip is stable (same canonical bytes) and tested with
      fixtures only — no network in CI.
- [ ] No execution path imports n8n; a design cannot introduce unsigned code.
- [ ] The convention guard asserts this spec is recorded.

## Risks / invariants

- **Untrusted design** → validate fail-closed; reference-only; pin/sign gates.
- **Adapter drift** → one canonical round-trip; fixture tests; no core coupling.
- **Credential leakage** → n8n credentials stay in n8n; the adapter passes only
  design documents; the harness never stores n8n secrets.
- **Silent network / cloud** → self-hosted, design-time only; CI hermetic.
- **Scope creep into core** → adapter-first; Manager-semantics changes remain
  forbidden.

Respects Laws 1, 2, 3, 4, 5 (amended: acquisition separate from run time), 7, 8,
10, 11, 12, 13. It changes no `src/` semantics in Phase 0 and no KERNEL invariant;
like SPEC-0007 it extends the harness adapter-first.
