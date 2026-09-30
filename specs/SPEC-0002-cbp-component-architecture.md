---
id: SPEC-0002
title: CBP component architecture (Component-Based Programming)
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# CBP — Component-Based Programming architecture

This spec defines the target architecture for the agent-centric execution line.
It is **target, not implementation**: each capability below carries an explicit
status (`MVP`, `roadmap`, or `declared-gated`). Where the code does not yet
implement something, this document says so — no claim exceeds what the operator's
suite proves.

Heritage: this paradigm descends from **Flow-Based Programming (FBP)**, invented
by J. Paul Morrison. FBP is credited in user documentation; the architecture and
code are branded **CBP**. See §10.

## Context

The current baseline (`src/agent_centric/cbp/`) is a tree of async `Agent`
objects speaking a request/response directive protocol over a driver-owned shared
ZeroMQ context, with a separate legacy `Node`/`Shell` model, a module-level
global registry, and a compile-time `ComponentNetwork` whose "components" are
named task callables. It is **not** classical FBP: no ports, no Information
Packets, no bounded connections, no back-pressure, no scheduler.

This spec unifies that into one ontology and moves determinism to the edges.

## 0. Vocabulary

- **Component** — any process. The single ontological category.
- **Atomic component** — a leaf that owns a behavior (function, executable, skill, model, thread/process).
- **Composite component** — a component that owns children and their shared context; its children form a **network**.
- **Agent** — a component that perceives and acts on its environment (ABM definition).
- **Network document** — the declarative graph artifact (`network.v1`) that defines a composite.
- **Backend** — a runtime that executes a compiled network (in-process, subprocess, native binary, remote cluster).
- **CPM scheduler** — the deterministic scheduler of a composite's children.
- **Confidence** — a deterministic score on every response; `1.0` for a verified deterministic output.
- **Review** — the persistent, append-only queue of outcomes that need review.

## 1. Ontology — everything is a component

1. **One contract.** There is exactly one component contract; atomic and composite components implement it. Agent, task, function, process, executable, skill, and network are all components.
2. **Fractal.** A component is a component to its parent and a context to its children. A network may itself be a component in another network.
3. **Self-contained as far as practicable.** A component owns its own state and its children's shared context; it outsources only by explicit delegation to child components.
4. **No privileged type.** There is no "task" exempt from being a component. The distinction between `Node`/`Shell` and `Agent` in the baseline is retired.

## 2. Parent as shared context

- A **parent is the shared context of its children**: identity, grants, configuration, and the network document that defines them.
- A child **owns its own state**. Its effects propagate **up** as *verified proposals*; the parent applies them as a **deterministic state transition**.
- Siblings never write each other directly. They observe effects only through the parent's state at defined **barrier points** (deterministic supersteps), so sibling effects are reproducible and auditable.
- State is **versioned and single-writer at the parent** (Laws 6, 10); a proposal from a child is applied only after the child's output is verified.

## 3. Execution model

1. **The network is a document** (`network.v1`, §6): the source of truth. It is authored statically (from memory / code) or generated dynamically (agent- or user-designed), then **frozen and content-hashed before execution**, so any run is reproducible from its document hash.
2. **The document is compiled** deterministically — compile order is a function of the document, with deterministic tie-breaks — to an executable plan.
3. **CPM schedules the composite.** A parent schedules its children's network by the Critical Path Method: dependencies, durations, and slack determine a deterministic order. CPM is a **pure function** of the network; it may drive execution order but never bypasses a verifier (Law 9, amended).
4. **Ports and Information Packets (MVP).** Components expose named **inports/outports**; data flows as **Information Packets**; the parent wires ports (connections defined **externally** to components) from the document. This is the classical FBP accent.
5. **Bounded connections and back-pressure (roadmap).** Connection capacity and back-pressure are a later phase, behind the same port/IP model.
6. **Verification at the edges.** Every component's output is verified before it is accepted, ordered, or propagated (correctness spine).

## 4. Determinism and non-determinism

1. **The control plane and the schedule are always deterministic** (Law 2): identical document + identical inputs ⇒ identical trajectory and outcome.
2. **Deterministic components must reproduce exactly.**
3. **Models are special agents** — components whose computation is non-deterministic. Their output is **recorded, pinned, and verified**; it is never relied upon directly. Making models deterministic (as far as possible) is a future program, not an MVP claim.
4. **Irreducible non-determinism is never silent.** Every response carries a deterministic **`confidence`** and provenance. An outcome that cannot be made deterministic is:
   - **traceable** (full causal chain per correlation id), and
   - **confidence-scored**, and
   - enqueued to a **persistent, append-only Review queue** (`review.v1`) servable by a human or an automated tier.
5. Laws 2/6/8 are preserved; Law 9 is amended (see §11).

## 5. Document → compiler → backend

The portable seam: **network document → deterministic compiler → runtime backend**.

| Backend | Status | Notes |
| --- | --- | --- |
| `inproc` (deterministic reference) | **MVP** | Offline, replayable; the backend all tests and CI use. |
| `subprocess` (isolated local process) | roadmap | Reuses the existing isolation lessons; gated on determinism. |
| native binary (compiled) | declared-gated | Same semantics, compiled target. |
| remote cluster (k8s, etc.) | declared-gated | Distributable target. |

A backend is admitted only when its determinism and verification are provable;
until then it is **declared-gated** and fails closed, exactly like L3 in
[`.agentfactory.toml`](../.agentfactory.toml).

## 6. Contracts (proposed; added additively to `contracts/`)

Contracts remain **additive-only**. New versioned contracts:

- **`component.v1`** — a component's identity, kind (atomic | composite), ports, declared capabilities, grants, and envelope.
- **`network.v1`** — components, ports, edges, grants, envelope; canonical, content-hashable; the compiled plan is derived (not stored as source).
- **`review.v1`** — a review item: correlation id, domain, confidence, provenance, status, resolution; append-only, write-once.
- **`skill.v1`** — a skill-as-component: identity, description, deterministic termination, confidence/review policy; mirrors `component.v1`.

Shapes are sketched in Appendix A and finalized in the contract code during Phase 1.

## 7. MVP scope (what we build now)

- **Phase 0 (this change):** this spec; the Law 9 amendment; the `KERNEL.md` supersede note; the naming/branding plan.
- **Phase 1 (MVP):**
  1. One **`Component` contract**, with the current `Agent` **adapted** onto it (no rewrite).
  2. **Parent-declared children** and **parent-as-shared-context**; remove the central `_child_class_for` map and the module-level registry global; registration/resolution becomes a **component** provided by the parent.
  3. **CPM deterministic scheduler** for a composite's network.
  4. **`network.v1` document** as source of truth, content-hashed, deterministically compiled.
  5. **Ports + Information Packets** and external wiring from the document.
  6. **`inproc` backend only.**
  7. **`confidence`** on every response; a minimal persistent **Review** component.
  8. Operator-run suite stays green at every step.

**MVP explicitly does not** include: bounded connections/back-pressure, async scheduler, non-inproc backends, dynamic-network persistence, model determinization, or an automated review tier.

## 8. Roadmap (declared, gated)

Backends beyond `inproc`; bounded connections and back-pressure; asynchronous deterministic scheduler; static/dynamic network documents persisted as graph config files; model determinization techniques; automated review tier; the `fbp` → `cbp` package/CLI rename (completed early, §10). Each item is admitted only with proven determinism and verification.

## 9. Migration (incremental, adapter-first)

1. Define `Component`; **adapt `Agent`** onto it; keep the existing wire protocol working underneath.
2. Convert capabilities to atomic components; registry to a component; children declared by parents; CPM scheduler added.
3. Add the document/compiler seam and ports/IPs; switch the reference backend to compiled documents.
4. Retire the legacy `Node`/`Shell` dual model and the driver-owned shared context once the adapter proves equivalent (replay + audit must match).
5. Rename `fbp` → `cbp` (§10) as its own reviewed, mechanical change. **(completed)**

Each step is its own spec-accepted change; no big-bang.

## 10. Naming and branding

- **CBP** is the branded paradigm; **FBP** appears only in documentation, with credit to J. Paul Morrison.
- The project is **Agent-centric** (Creative Development Systems umbrella). The package remains `agent_centric`, with the **CBP subsystem at `agent_centric.cbp`**. The `fbp` → `cbp` cut-over (below) was performed as its own reviewed, mechanical change at operator direction — earlier than the planned Phase 3 — and is recorded here.
- After the cut-over, code and CLI use `cbp`; the acronym `FBP` survives only in heritage documentation, with credit to J. Paul Morrison.

## 11. Constitutional changes

- **Law 9 amended** to "Critical Path Is the Deterministic Scheduler": CPM remains a pure function of the declared network and never bypasses a verifier, but it **may drive** a composite's schedule. Applied in [`PRINCIPLES.md`](../PRINCIPLES.md).
- **`KERNEL.md`** (the v0 Manager-line freeze, which declared FBP networks out of scope and CPM observational) is marked superseded for the CBP line.

## 12. Acceptance criteria and holdout

- [ ] `component.v1` / `network.v1` / `review.v1` exist and are additive.
- [ ] The `Agent` adapter satisfies the `Component` contract; no component-semantics test regresses under the operator's run.
- [ ] No module-level registry global and no central child-class map remain.
- [ ] An identical `network.v1` document schedules identically (CPM) and re-executes identically (replay) — proven by holdout scenarios in [`specs/holdout/`](holdout/README.md).
- [ ] Every response carries `confidence`; a sub-threshold outcome appears in the persistent Review queue.
- [ ] Only the `inproc` backend is enabled; others fail closed.

Holdout scenarios (author-blind, validator-run) cover: determinism/replay, the verification spine, CPM schedule reproducibility, parent-context barrier determinism, component self-containment, and absence of globals.

## 13. Risks / invariants

- The highest risk is the adapter subtly changing verification or replay semantics; each phase must prove equivalence before the old path is removed.
- Determinism must not be traded for concurrency; async is confined to I/O and recorded.
- Respects Laws 1, 2, 3, 4, 6, 7, 8, 9 (amended), 10, 11, 12; does not touch the frozen `agent-manager-version` line.

## Appendix A — contract sketches (to be finalized)

```
component.v1: {identity, kind: atomic|composite, behavior?, ports:{in:[],out:[]},
               capabilities:[], grants:{...}, envelope:{...}}

network.v1:   {id, version, components:[component.v1...],
               edges:[{from:{component,port}, to:{component,port}}],
               grants:{...}, envelope:{...}, content_hash}

review.v1:    {correlation_id, domain, confidence, provenance:[...],
               status: pending|resolved, resolution?, seq}
```

## Skills as components

A **skill** is a component (in LLM terms): an agent-oriented component whose
reasoning is non-deterministic and therefore **suspect**. A skill must terminate
in a call to a deterministic process — a command, a gate, or another component.

The canonical, industry-standard form is `SKILL.md` under
[`.agents/skills/`](../.agents/skills) (mirrored for Claude via the
`.claude/skills` symlink), carrying `metadata: {component, determinism}` — the
portable encoding of the equivalence. The `skill.v1` contract (roadmap)
formalizes this mapping. See the `cbp-architecture` skill.
