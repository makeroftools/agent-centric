# `shell` (example component)

The **root of the CBP tree** as an ordinary component (SPEC-0007 §1) — the tree's
root, not an external orchestrator. It owns the top-level context and delegates
work down the tree; here it **points to** the `registry` component (`ref`), which
is pinned as its own `components.lock` entry.

- kind: `composite`
- entry: `agent_centric.cbp.shell_component:plan_delegation`
- children: `ref: registry`
- capability: `shell`
- state: `sqlite` at `state/shell.db`

`plan_delegation` is pure: given a mission and its children it returns the same
mission plus the children in sorted (deterministic) order. The shipped
`shell.py` is reference code; execution is the allowlisted in-tree entry.
