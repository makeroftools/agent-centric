# agenda — an example atomic component (referenced child)

A minimal, deterministic CBP component: it projects a bills **calendar/agenda**
for a date window from a validated registry mapping. It is the
**pointed-to** (`ref`) child of the example
[`bills_registry`](../bills_registry) composite, pinned as its own
`components.lock` entry (its own `commit_sha` + `tree_sha256` + signature) —
the "points to an independently versioned component" mode of
[`SPEC-0007`](../../../specs/SPEC-0007-harness-shell-component-distribution.md).

- `component.json` — the `component.v1` manifest.
- `agenda.py` — the bundled reference behavior (the allowlisted in-tree entry is
  `agent_centric.fbp.bills_component:project_agenda`).

Due dates and amounts are read from registry data only — never invented.
