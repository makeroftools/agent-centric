# The Agent→Component adapter and the parent-provisioned catalog

SPEC-0002 Phase-1 (items 1, 2, 6) is delivered **adapter-first**, under L1. This
page is the concise, current map; the full design and progress record is
[`spec-0002-phase1-adapter-design.md`](spec-0002-phase1-adapter-design.md), and
the target architecture is
[`SPEC-0002`](../../specs/SPEC-0002-cbp-component-architecture.md). The entry
point remains [`AGENTS.md`](../../AGENTS.md).

## What exists

- **Adapter (item 1).** [`component_adapter.py`](../../src/agent_centric/cbp/component_adapter.py)
  — `AgentComponent` adapts an `Agent` onto the single `Component` contract:
  lifecycle passthrough, an entry bridge (`run_once`), and a pure `component.v1`
  manifest projection. Equivalence with the `CbpDriver` round-trip is proven.
- **Catalog (item 2).** [`component_catalog.py`](../../src/agent_centric/cbp/component_catalog.py)
  — a **passive** `ComponentCatalog` (with `RegistryEntry.from_callable`) plus
  `AgentConfig.catalog`. A parent **provisions** the catalog; a child resolves
  **only** what its parent provisioned.
- **Backend (item 6).** `ADMITTED_BACKENDS = {"inproc"}`; `CbpDriver(backend=…)`
  refuses anything else (`BackendNotAdmitted`).

## What was removed

- The **module-level registry global** (`_REGISTRY` / `_resolve_entry` /
  `register_callable`). `CbpDriver` owns the catalog and provisions it to the
  root; children inherit it; replay provisions the replay tree's catalog and seeds
  a fresh driver from the ledger manifest. A guard test asserts no global remains.
- The central **`_child_class_for`** map. `Agent.declare_child_kind` is
  provisioned by the tree builder (`CbpDriver` declares `store` / `bills` /
  `model`); an undeclared kind fails closed **before any socket bind** (no partial
  side effect).

## Invariants

- **No ambient resolution.** After the cut-over, production resolution never falls
  back to a module global; the absence of a provisioned entry is an explicit
  refusal (Laws 7 and 8), not a silent default.
- **Idempotent.** The catalog is passive (no writes on resolve) and the adapter
  projection is pure, so repeated calls are byte-identical.
- **Additive-only.** `component.v1` is consumed; any needed change is
  `component.v2`.
