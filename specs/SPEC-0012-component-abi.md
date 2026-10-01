---
id: SPEC-0012
title: Component ABI v1 — abstract components, typed contracts, dynamic task loading
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Component ABI v1 — abstract components, typed contracts, dynamic task loading

## Context

The active CBP subsystem can already bundle, sign, content-address, resolve, boot
and process-isolate a component (SPEC-0007 phases 0.5a/1a/1b/2), and it enforces
typed ports across a network document (SPEC-0009). But the only runner today is
**Python-specific**: `cbp/component_process.py` spawns `python -I`
(`_component_runner.py`) with a single-shot JSON request/response. There is no
language- and runtime-agnostic contract that a component written in any language
can implement and that any host can execute.

The full version must run components written in **any language, in any runtime**,
wired dynamically just before execution. That requires one frozen ABI.

## Decision

**A component is a special abstract object.** It is **virtual and blank at
rest**: it carries a typed interface contract and a language/runtime requirement,
but the concrete **task it invokes is loaded dynamically**. A component therefore
depends only on **its language and its runtime** — never on a specific baked-in
implementation.

### Module surface (frozen ABI)

A component is realized as a module exporting exactly three entrypoints:

- **`init(boot_config)`** — invoked once, before anything else. Receives the
  typed **pre-init boot config** (identity, declared contract, environment).
- **`run(event_loop)`** — the long-lived loop. All body data arrives over the
  loop; **it receives everything except the pre-init already given to `init`**.
  **All channels (ports) and all tasks are announced over the initial,
  hard-coded input channels**, not over ad-hoc ones.
- **`kill()`** — deterministic teardown.

**Transport: ZeroMQ** (`pyzmq` is already a repository dependency). The event
loop is a ZeroMQ socket loop; channels are ZeroMQ endpoints.

### Identity

Identity is **two-level** and is **never** the ports:

- **Abstract component** — identity is the hash of its **typed interface
  contract** (WIT, below). This is language- and runtime-agnostic and is what a
  network binds against.
- **Concrete task** — identity is its **signed content hash**, with provenance
  from its registry source (SPEC-0015). This is the artifact that executes.
- **Ports** are **dynamic runtime endpoints** instantiated from the contract;
  they are added/removed at runtime and are not identity.
- **At rest, a component has no edges** (SPEC-0009 slice 6); wiring exists only
  in a network document (SPEC-0014).

### Contract language

The typed interface is expressed in **WIT** (the WASM Component Model interface
description): language-agnostic, reusable by the Rust host, the Python reference
host, and the browser. Inputs and outputs are contractual types; `config` is a
distinct typed slot (design-time) from run-time packet inputs.

### Registry sources

A component's registry entry may point to an **open, extensible** set of
sources — a **git repo, a directory, a binary, …** — anything content-addressable.
Source kind does not change identity: the resolved artifact is always
content-hashed and signed.

### Goals

**Autonomy and flexibility.** The ABI is deliberately minimal so that a host can
execute components it was never built against.

## Scope

- **In:** the frozen component ABI (surface, transport, identity, contract
  language, registry-source kinds); the abstract/blank component model; the
  dynamic task-loading model.
- **Out:** any host implementation (SPEC-0013); network/wiring semantics
  (SPEC-0014); provenance and trust gates (SPEC-0015); editions (SPEC-0016);
  runtime provisioning and conformance vectors (SPEC-0013).

## Acceptance criteria

- [ ] A language-agnostic ABI document exists stating `init` / `run` / `kill`,
      the ZeroMQ event loop, the pre-init/body split, and the rule that all
      channels and tasks are announced over the initial hard-coded channels.
- [ ] Two-level identity is frozen: abstract = contract hash; concrete task =
      signed content hash + provenance; ports are explicitly **not** identity.
- [ ] The typed contract is expressed in WIT and consumed by at least two
      independent hosts.
- [ ] Registry sources are enumerable and all resolve to a signed,
      content-addressed artifact.
- [ ] "At rest, no edges" holds for the ABI (a component manifest carries no
      wiring).

## Risks / invariants

- Respects Laws 1, 2, 5, 8, 11; changes no `src/` semantics by itself.
- The ABI is fail-closed: a module missing any of `init`/`run`/`kill`, or
  announcing channels outside the initial channels, is rejected.
- Additive-only: the legacy in-process/args path is untouched until equivalence
  is proven (SPEC-0002 §13).
- The ABI must not encode any language or runtime assumption, or it silently
  reintroduces the Python-only limitation it exists to remove.
