---
id: SPEC-0021
title: Components as dynamic work — the work.v1 recipe and the task-load protocol
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# Components as dynamic work — the `work.v1` recipe and the task-load protocol

## Context

[`SPEC-0002`](SPEC-0002-cbp-component-architecture.md) makes **everything a
component**: a component is abstract and blank at rest, and its `run` performs a
task. SPEC-0002 records an explicit open item — the **blank-component → dynamic
task-load protocol** and its bind timing. Separately,
[`contracts/task.py`](../src/agent_centric/contracts/task.py) already defines
`task.v1`–`v6`, but that is **Manager-era vocabulary** (agent/capability selection,
resource envelope, tools, pipeline/parallel, policy) and is *not* the concept
here; it must not be overloaded.

Meanwhile [`cbp/orchestrate.py`](../src/agent_centric/cbp/orchestrate.py) already
demonstrates the seam this spec formalizes: a model's free text is canonicalized,
pinned as a **typed artifact**, then mapped by a **pure deterministic function**
to a CBP plan — and orchestration never bypasses verification.

The desired capability: a task is a **generalization of anything that can be
evaluated or executed**; it can be dynamically designed, constructed, and tested;
and it is delivered to a component through a **declarative interface** carrying
the instructions to create, obtain, or generate its code. This must be added
without weakening determinism or the frozen ABI.

## Decision

1. **`work.v1` — a declarative recipe for a unit of work.** A `work.v1` record is
   content-addressed and signed, and **compiles to a `component.v1`** (or a task
   within one). It is an *intent* layer, not a rival ontology: the compiled
   artifact **is** a component.

2. **Recipe fields (proposed).**
   - **obtain** — a pinned source reference, *or* **create/generate** — a
     generator reference with prompt and seed.
   - **build** — the recipe to construct it (language, runtime, toolchain pins).
   - **tests/verifier** — the acceptance the result must pass.
   - **provenance + grant** — where it came from and what it may do.
   - **envelope** — the hard bounds it runs under.

3. **Deterministic compile seam.** A **pure** compiler maps `work.v1` to a
   `component.v1`/plan, reusing the `orchestrate.py` seam. Identical recipe ⇒
   byte-identical artifact hash. The **only** non-deterministic step is
   generation, and it is confined to a `model` component whose output is a
   **suspect**: canonicalized, pinned, then verified before it is used.

4. **Generation is gated.** Generated, compiled, or translated work is
   **untrusted until it passes the declared verifier** and the conformance suite
   ([`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md)); it runs at most at
   **A0** until it earns an Assurance Label
   ([`SPEC-0015`](SPEC-0015-provenance-assurance-labels.md)). Automatic labeling
   requires the isolated validator and stays operator-gated
   ([`SPEC-0019`](SPEC-0019-isolated-holdout-validator.md)).

5. **Dynamic task-load protocol (SPEC-0002 open item #2).** Bind timing is
   explicit and fail-closed: a component announces its channels and tasks over the
   **initial hard-coded channels** ([`SPEC-0012`](SPEC-0012-component-abi.md));
   a task's identity is its **signed content hash**; at rest a component has **no
   edges**. A dynamically loaded task is content-addressed and verified **before**
   `run` executes it; an unpinned or undeclared task refuses before any bind,
   with no partial side effect.

6. **Additive-only ABI.** `component-abi.v1` is frozen and consumed; it is
   unchanged. A genuinely new wire need is `component-abi.v2`. `work.v1` is added
   additively to `contracts/`; the legacy `task.v1` semantics are untouched.

## Scope

- **In:** the `work.v1` recipe contract; the `work.v1 → component.v1` pure compile
  seam; the dynamic task-load and bind protocol; generation gating; deterministic
  acceptance criteria.
- **Out:** implementing the contract or compiler (this spec only); any change to
  `component-abi.v1`; overloading the legacy `task.v1`; automatic labeling
  (SPEC-0019 / L3); the ontological knowledge base (SPEC-0010) and recursive
  self-improvement (SPEC-0017 WS5); concrete generators or model prompts.

## Acceptance criteria

- [ ] `work.v1` exists, is additive, canonical, and content-hashable.
- [ ] A recipe compiles to a `component.v1`/plan via a **pure** function:
      identical recipe ⇒ identical artifact hash.
- [ ] A generated (`create`/`generate`) work item is untrusted until it passes its
      declared verifier; it cannot execute otherwise.
- [ ] The dynamic task-load protocol is explicit and fail-closed: an unpinned or
      undeclared task refuses **before** any bind/run, with no partial side
      effect.
- [ ] A component at rest has no edges; tasks and channels are announced only over
      the initial hard-coded channels.
- [ ] `component-abi.v1` is unchanged (additive-only), and the legacy `task.v1`
      semantics are untouched.
- [ ] A task's identity is its signed content hash; ports are never identity.

## Risks / invariants

- **Law 1 / Law 2 (correct, deterministic).** Compilation is pure; the sole
  non-deterministic step (generation) is confined to a recorded `model` component.
- **Law 7 / Law 8 (least privilege, explicit failure).** An unpinned or undeclared
  task is an explicit refusal, not a silent default.
- **Law 4 / Law 10 (progressive disclosure, passive catalogs).** The recipe layer
  is minimal; catalogs record, they do not decide.
- **SPEC-0002 (fractal).** Every task is a component; there is no privileged task
  type. `work.v1` is an intent that compiles onto that ontology.
- **SPEC-0013 / SPEC-0015.** Generated artifacts are gated and untrusted until
  verified.
- **The frozen ABI is consumed.** Changes are additive only.
