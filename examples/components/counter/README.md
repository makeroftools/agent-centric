# counter — an example component

A minimal, deterministic CBP component: it counts occurrences of a target
character in a string. It is the first component pinned in a
[`components.lock`](../../../specs/SPEC-0007-harness-shell-component-distribution.md)
during Phase 1a of the distribution plan.

- `component.json` — the `component.v1` manifest (the component's declaration).
- `counter.py` — the reference behavior (a pure, deterministic function).

## Phase 1a note

Execution is **allowlist-only**: the manifest's `entry`
(`agent_centric.agents.counter:create_counter_agent`) resolves to code already
installed in the harness. A fetched bundle's own code is **not** imported until
the subprocess isolation backend exists — a declared gate (see
[`SPEC-0007`](../../../specs/SPEC-0007-harness-shell-component-distribution.md)).
