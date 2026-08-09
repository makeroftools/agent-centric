# KERNEL — v0 Kernel Freeze Note (Volley 017)

**Authority:** Lead Architect
**Classification:** Mission-Critical
**Status:** Freeze candidate — the kernel is stabilised as a coherent v0 surface.

This note records what the v0 kernel guarantees, what is intentionally out of
scope, and how future work should proceed. It is the short architecture/status
freeze document for the kernel milestone.

## What the v0 kernel guarantees

The kernel is a deterministic, Manager-orchestrated control plane for governed,
verifiable agents. It guarantees:

- **Correctness first.** No result is accepted without passing the mandatory
  verification gate. There is no ambiguous third terminal state: a task
  terminates in a verified result or an explicit, audited failure.
- **Deterministic control plane.** The Manager alone governs agents: it
  sequences, hands off data, enforces resource envelopes hard, mediates every
  tool and model call, evaluates policy before any work begins, and records a
  full append-only trajectory. Agents never gain the ability to spawn or
  directly invoke one another.
- **Composition.** Sequential pipelines (`pipeline.v1`–`v4`), parallel fan-out /
  join (`parallel.v1`), and nested composition (a sequential stage as a parallel
  group, `pipeline.v4`) are Manager-orchestrated and share one coherent,
  reconstructible trajectory.
- **Full auditability.** Every step, tool/model call, policy decision, resource
  consumption, and cancellation is recorded durably and reconstructible after a
  restart.
- **Fail-closed.** Any failure — verification, policy, envelope exhaustion,
  cancellation, tool denial, child crash, or store error — is an explicit,
  audited failure, never an unverified success.
- **Observational aids.** Read-only summary, replay verification, and critical
  path (CPM) analysis are available over durable trajectories without mutating
  the audit record.
- **Local-first.** The kernel runs locally with no network services and no
  required credentials; the stub model provider is deterministic and replayable.

## What is intentionally out of scope for the v0 kernel

The following are explicitly **not** part of the v0 kernel and are not planned
as changes to Manager semantics:

- Messaging fabric (ZeroMQ / NATS / etc.) and any network transport.
- MCP / A2A integration.
- A Rust core or native reimplementation.
- Deep workflow graphs, cyclic or dynamic workflows, or durable workflow
  engines / FBP networks.
- Multi-tenancy, distribution, or cloud concerns.
- UI.
- CPM-driven scheduling (analysis exists; execution stays Manager-driven and
  CPM remains observational).
- Production provider credential management.

## How future work should proceed

Further work should **prefer adapters and backends over changing Manager
semantics.** New capabilities should be added as:

- **Backends** (e.g. execution backends, model providers, trajectory stores)
  behind the existing interfaces, and
- **Adapters** over the existing public surface,

rather than by altering the core Manager orchestration, verification, policy,
envelope, or accounting invariants. The public surface and the versioned
contracts are the stable interface; internal helpers are not part of the
guaranteed surface and may change.

## Versioning

The package is versioned as a kernel milestone aligned with volley depth:
**0.19.0** (19 volleys delivered). The scheme is documented in `STATUS.md`.