---
name: cbp-architecture
description: Use when working on the execution architecture. CBP (Component-Based Programming) - everything that is a process is a component; a skill is a component; a parent is the shared context of its children; CPM schedules the network. Target spec is SPEC-0002.
license: MPL-2.0
compatibility: opencode
metadata:
  component: cbp-architecture
  determinism: suspect
---

# CBP — Component-Based Programming

Heritage: **Flow-Based Programming** (J. Paul Morrison). The branded paradigm is
**CBP**; FBP is credited in documentation only.

## The model

- **Everything that is a process is a component** — agent, task, function,
  executable, skill, node, and a whole network. Fractal and self-contained as
  far as practicable.
- **An agent** is a component that perceives and acts on its environment.
- **A skill is a component** (in LLM terms): its reasoning is non-deterministic
  and **suspect**; it must terminate in a call to a deterministic process.
- **Parent as shared context.** A child holds its own state; effects reach
  siblings only as verified proposals applied at deterministic barriers.
- **CPM schedules** a composite's reduced network deterministically; it never
  bypasses a verifier (Law 9).
- **The network is a document** (`network.v1`), content-hashed, compiled to a
  backend. MVP ships the deterministic in-process backend; subprocess, native,
  and remote backends are declared-gated.
- **Non-determinism is never silent:** confidence + a persistent `review.v1` queue.

## Deterministic termination

`uv run agent-centric fbp-check` proves the verified spine; the convention guard
enforces this layer. The target architecture is
[`specs/SPEC-0002-cbp-component-architecture.md`](../../../specs/SPEC-0002-cbp-component-architecture.md).

## Non-determinism (suspect)

Model and skill outputs are suspect until verified; dynamic networks are frozen
and hashed before execution. Determinism is paramount, or the outcome is
traceable and confidence-scored for review.
