# bills_registry — an example composite component

A **composite** CBP component introduced in Phase 1b of the distribution plan
([`SPEC-0007`](../../../specs/SPEC-0007-harness-shell-component-distribution.md)).
It demonstrates the two things a composite adds over an atomic component:

- **state** — a self-contained, single-writer **SQLite** store, declared in the
  manifest as `state: {kind: sqlite, path: state/bills.db, single_writer: true}`
  (materialized by the harness via
  [`agent_centric.cbp.component_state`](../../../src/agent_centric/cbp/component_state.py));
- **children**, in both modes:
  - **contains** — the embedded [`children/bills_rules/`](children/bills_rules/component.json)
    component (a real `component.v1`, not a data folder) is bundled inside this
    component and is therefore covered by its `tree_sha256`;
  - **points-to** — the [`agenda`](../agenda) component is a separately pinned
    external component with its own `components.lock` entry.

## Files

- `component.json` — the composite `component.v1` manifest.
- `bills_registry.py` — the bundled reference behavior (documentation; the
  allowlisted in-tree entry is `agent_centric.cbp.bills_component:registry_snapshot`).
- `children/bills_rules/` — the embedded child component.

## Phase 1b note

Resolution is **allowlist-only** and offline: the graph resolver verifies the
parent's `tree_sha256` + signature, checks that the embedded child is a real
component inside the bundle, and resolves the referenced `agenda` child from the
lock. Nested composites are not supported yet (they land with the shell
component in Phase 2).
