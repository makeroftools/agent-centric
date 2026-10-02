---
id: SPEC-0020
title: Service hosting — provider abstraction, reconciliation, and self-healing
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# Service hosting — provider abstraction, reconciliation, and self-healing

## Context

[`SPEC-0018`](SPEC-0018-service-host-provisioning-and-continuous-deployment.md)
defines verify-then-apply service hosting: every operation is a named,
deterministic, idempotent task; a `service.v1` pins the host definition and the
composition; the pull/apply agent verifies before it applies. It is deliberately
**provider-agnostic** and currently targets a NixOS host definition plus a local
CD substrate. It does **not** define a provider abstraction, a reconciliation
loop, notification, or a declarative local↔cloud choice.

The next step is to make hosting **automatic and dynamic by configuration**: the
same declarative service targets a chosen provider (a mainstream VPS first) or
the local substrate, with idempotent provisioning, self-healing, notifications,
and effective authentication/authorization — without surrendering determinism.

The governing tension: provider APIs are network-facing and therefore
non-deterministic, while the control plane must stay deterministic
([`PRINCIPLES.md`](../PRINCIPLES.md) Law 2). MCP and ACP are **edge transports**
for human/authoring interaction (`src/agent_centric/cbp/mcp.py`), never the
execution path. Provider affects **deployment only**: by
[`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md) editions differ only in
capacity/performance/deployment/governance/support/UI, never in meaning.

## Decision

1. **`provider.v1` — a thin adapter contract.** A provider declares its identity,
   capabilities, and a fixed set of named tasks. Planned for the contract:
   - `plan` — **pure**: desired state in, a canonical, content-addressed plan out;
     performs **no I/O** and is byte-deterministic.
   - `apply` / `health` / `teardown` — **gated**, idempotent tasks that execute a
     pinned plan over the provider's typed API.

   The shape mirrors [`service.v1`](../src/agent_centric/contracts/service.py)'s
   task discipline: idempotency is declared; every **mutating** task is
   content-pinned and refused without a digest; a non-idempotent task must be
   explicitly gated.

2. **Deterministic boundary.** Nothing is applied before its plan is pinned and
   verified. `plan` is pure; `apply` executes a **pinned** plan, records every
   provider request/response, and is idempotent under retry. The system owns a
   `desired → observed` reconciliation, not a one-shot script.

3. **Reconciliation and self-healing.** A reconcile loop runs
   `plan → verify → apply → health`. A failed health gate triggers **deterministic
   auto-rollback** to the last healthy pinned plan. Retries are bounded. A repeat
   failure **escalates to the operator and stops** — fail-closed, never a partial
   or ambiguous success. No destructive act (destroy/replace) occurs without the
   declared, recorded approval.

4. **Notifications are a component.** Notification is an **emit-only**
   `component.v1` — capability-bounded, secret-free, configured by refs — invoked
   on state transitions (provisioned, healed, rolled back, escalated). There is
   **no notification logic in the core**; keeping it a component preserves
   "everything is a component" and least privilege.

5. **Local-first, declaratively.** One declarative service targets **local or
   cloud by configuration**. Local is the default (Law 5); cloud is an explicit,
   operator-approved target; capacity and desire are declared, not hard-coded.
   The same spec compiles to either.

6. **Authentication and authorization.** Machine identity comes from the secret
   store as **refs only**; each service receives **capability grants**; operator
   authorization is required for provisioning, enrollment, and destructive acts;
   rotation and revocation reuse the existing overlapping trust windows
   ([`cbp/trust.py`](../src/agent_centric/cbp/trust.py)). No plaintext secret ever
   appears in a plan, a record, or a repository.

7. **Additive placement.** `provider.v1` is added additively to `contracts/`; no
   new node kind is introduced ([`SPEC-0018`](SPEC-0018-service-host-provisioning-and-continuous-deployment.md)
   §3). This spec **extends** SPEC-0018 §4–§7; it does not replace them.

## Scope

- **In:** the `provider.v1` adapter contract; the pure-plan / gated-apply split;
  the reconciliation and self-healing model; the emit-only notification
  component boundary; declarative local↔cloud selection; authN/Z at the provider
  boundary; deterministic acceptance criteria.
- **Out:** implementing the contract or any adapter (this spec only); provider
  credentials, regions, host ids, or inventory (private, never in this repo);
  auto-apply without operator approval (operator-gated; L1 holds);
  multi-tenant billing/authentication; MCP on the execution path; changing any
  existing SPEC-0018 invariant.

## Acceptance criteria

- [ ] `provider.v1` exists, is additive, canonical, and content-hashable.
- [ ] `plan` is pure: identical desired state yields a byte-identical pinned plan,
      with **no** provider I/O at plan time.
- [ ] `apply` refuses an unpinned or non-idempotent task; every mutating task
      carries a content digest; apply is verify-then-apply.
- [ ] A failed health gate triggers deterministic rollback to the last healthy
      pinned plan; repeated failure escalates and stops (fail-closed, no partial
      success).
- [ ] Reconciliation is idempotent: converging already-converged state performs no
      mutating provider call.
- [ ] Notification is an emit-only component: it holds no secret and cannot mutate
      infrastructure.
- [ ] The same declarative service targets local and cloud; **local is the
      default**, cloud is explicit and approved.
- [ ] Provider changes flow only through deterministic, typed API adapters; MCP is
      not on the execution path.
- [ ] No plaintext secret appears in any plan, record, or committed file (refs
      only).

## Risks / invariants

- **Law 1 / Law 2 (correct, deterministic control plane).** Only `plan` is
  (pure) logic; the network I/O of `apply` is confined, recorded, and never
  relied upon for determinism.
- **Law 5 (local-first).** Local is the default target; cloud is explicit and
  operator-approved.
- **Law 7 (least privilege).** Capability grants; identity as refs; no ambient
  authority.
- **Law 8 (explicit, audited failure).** Rollback then escalation; there is no
  ambiguous third state.
- **SPEC-0015.** Nothing executes before it is content-addressed and verified.
- **SPEC-0016.** Provider choice is a **deployment** axis; it never changes
  semantics.
- **The provider is untrusted infrastructure.** An API's success claim is not
  trusted; observed state is verified by a health gate before it counts.
- **Laws 11/12/13.** Whole-file replacement, faithful test reporting, continuous
  commit/push; no gate bypass.
