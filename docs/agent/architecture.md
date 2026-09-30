# docs/agent — architecture: harness vs tree

The system has exactly **two layers**. The target architecture, contracts, and
phased plan are [`specs/SPEC-0007-harness-shell-component-distribution.md`](../../specs/SPEC-0007-harness-shell-component-distribution.md).

| Layer | Is a component? | Owns |
| --- | --- | --- |
| **Harness — `agent-centric`** | **No** | boot, runtime/executor, contract definitions, the passive registry, the resolver/pinner, `components.lock`, signing/verify, constitution, CI |
| **Tree — components** | **Yes** | the root **shell component**, domain components, LLM components (`kind: model`), each self-contained with local SQLite state |

The harness **executes** the tree; it is not a node in it. The tree's root is the
**shell component** — an ordinary component, like every other node. This is the
CBP ontology of [`SPEC-0002`](../../specs/SPEC-0002-cbp-component-architecture.md)
§1 realized: one contract, everything is a component, no privileged type.

## Components: contain or point

A **composite** component may:

- **contain** its children (embed them — they are covered by the composite's own
  `tree_sha256`), or
- **point to** them (reference pinned external components, each with its own
  entry in `components.lock`).

A **skill** is a component's **directive** (`SKILL.md`) and maps to a component
of the same name (see [`components.md`](components.md)). An **LLM** is a
component of kind `model`.

## Adding a component

1. Scaffold the directive: [`tools/new-skill.sh`](../../tools/new-skill.sh)
   (see [`skill-authoring`](../../.agents/skills/skill-authoring/SKILL.md)).
2. Declare the component (`component.v1`) and, if it is external, pin it in
   `components.lock` — signed and content-hashed.
3. Validate:
   ```sh
   uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py
   ```

## Examples

Every runnable example lives under an [`examples/`](../../examples) directory —
the top-level one in the harness, and an `examples/` inside each component's
layout. The guard ensures `examples/**` is never mistaken for a component.

## Resolving a component (Phase 1a)

`components.lock` pins each component by `commit_sha` + `tree_sha256`. The
harness resolves a component **offline**:

1. fetch the deterministic bundle from the source (a `DirectorySource` mirror,
   or `GitSource` over a local repo at the pinned commit);
2. verify `tree_sha256` and the detached signature (minisign/gpg) — fail closed;
3. load the `component.v1` manifest from the verified bundle;
4. resolve the entry **only if allowlisted** (Phase 1a) — a fetched bundle's own
   code is not imported until the subprocess isolation backend exists.

Runnable demo:
[`examples/component_distribution.py`](../../examples/component_distribution.py).
Example component source:
[`examples/components/counter/`](../../examples/components/counter).

## Resolving a composite (Phase 1b)

A **composite** may *contain* children (embed them) and/or *point to* children
(pinned external components). The harness resolves the graph **deterministically,
children first**, and fails closed:

1. a **referenced** child must be its own `components.lock` entry and is verified
   exactly like any other component (hash + signature + contracts);
2. an **embedded** child must be present inside the verified parent bundle and
   must itself be a real `component.v1` (covered by the parent's `tree_sha256`);
3. resolution order is post-order (children before parents) with a deterministic
   tie-break; a reference **cycle** fails closed;
4. a composite's declared **SQLite state** is materialized atomically (WAL,
   `integrity_check`, path-safe) under a caller-supplied state root — mutable
   state never lives inside the immutable bundle cache.

Runnable demo:
[`examples/component_graph.py`](../../examples/component_graph.py).
Example composite:
[`examples/components/bills_registry/`](../../examples/components/bills_registry)
(embedded `bills_rules`, referenced `agenda`).

## Boundaries (non-negotiable)

- **Run time is local-first**; distribution happens only at **acquisition time**,
  pinned, signed, verified, and offline-capable (Law 5, amended).
- **No floating versions at run time**; pins are `commit_sha` + `tree_sha256`.
- **Fail closed** on any mismatch, absence, or unknown contract version.
