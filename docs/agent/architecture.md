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

## Boundaries (non-negotiable)

- **Run time is local-first**; distribution happens only at **acquisition time**,
  pinned, signed, verified, and offline-capable (Law 5, amended).
- **No floating versions at run time**; pins are `commit_sha` + `tree_sha256`.
- **Fail closed** on any mismatch, absence, or unknown contract version.
