# `registry` (example component)

The **passive catalog** as an ordinary component: metadata only (a name and a
location), no fetch, no compile, no run. Declares local SQLite state
(`state/registry.db`, single-writer).

- kind: `atomic`
- entry: `agent_centric.cbp.registry_component:catalog_snapshot`
- capability: `registry`
- state: `sqlite` at `state/registry.db`

`catalog_snapshot` is pure: it validates a `{"catalog": [...]}` payload and
returns it sorted by name, so the snapshot is deterministic and replayable. The
shipped `registry.py` is reference code; execution is the allowlisted in-tree
entry (see `../../../src/agent_centric/cbp/component_runtime.py`).
