---
id: SPEC-0018
title: Service-host provisioning and continuous deployment (verify-then-apply)
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Service-host provisioning and continuous deployment (verify-then-apply)

## Context

The system needs its own **acquisition and service substrate**: a self-hosted
**Gitea** component/package mirror, a place the harness fetches versioned
components from, and — more generally — a way to **provision service hosts and
deploy to them continuously**. Today the workspace is deliberately
infrastructure-free: there are no provisioning assets, no deploy pipeline, and
the only CI (`.github/workflows/gates.yml`) is hermetic and network-free. There
is also no sanctioned home for infra code: [`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)
forbids private keys, private origins, and pro code in the public repo, and
[`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) leaves the "live
self-hosted mirror" open.

Two existing statements are reconciled here. SPEC-0007's Phase 2 says the
committed umbrella `components.lock` "needs the mirror"; [`SPEC-0011`](SPEC-0011-minimum-viable-product.md)
and SPEC-0016 say Core uses the **offline directory source + local signing** and
**defers the live mirror** (Pro/Enterprise). This spec adopts the latter: the
Core umbrella lock is offline-signed and needs no live service; the mirror is an
optional acquisition convenience for Core and a requirement for later editions.

This is greenfield. It must respect: Law 5 run-time local-first (distribution is
acquisition-time only); the hermetic-component-CI non-goal; the
public-repo prohibition (SPEC-0016 §94); operator-held, out-of-process signing;
and the loopback-by-default transport doctrine ([`docs/transport_trust_boundary.md`](../docs/transport_trust_boundary.md)).

**Design premise — "we eat our own dog food."** These infrastructure services
are, in the target, **components**: a *service* is a **composition of components**
(with `network.v1` as wiring), not a new node kind. The only exception is the
**bootstrap substrate** — the irreducible minimum required before the system can
serve its own components (a distribution mirror, a secret store, and an apply
agent cannot be fetched from the substrate they provide). Bootstrap is a
**stage**, not a permanent exemption: it **graduates** to managed components, so
the end-state is self-hosting.

## Decision

### 1. Three layers; bootstrap is the only exemption

| Layer | What | Cardinality | Status |
| --- | --- | --- | --- |
| **Harness / runtime** | the `agent-centric` harness + **host provisioning** | not a node | harness owns it |
| **Bootstrap substrate** | provisioning bootstrap, the **Gitea** mirror, the **OpenBao** secret store, the **pull/apply agent** | minimal, frozen | exempt, then graduates |
| **Components** | everything else; a *service* is a **composition** of components | unbounded | governed by the ABI |

Host provisioning is **harness/runtime** (SPEC-0007: the harness executes the
tree and is not a node). Signing and the transparency log stay **operator-side**,
not a host service, so they are not bootstrap items.

### 2. Every operation is a named, deterministic callable or executable script

**All operations in this domain are callables for determinism** — install,
configure, health, backup, restore, rollback, enroll, apply. Each is a **named,
idempotent, fail-closed unit** that either runs in the host runtime's language or
is an **executable script** invoked by name. No operation is an opaque blob.

This is the natural extension of the component model: a component is assigned a
**task** that executes in its runtime's language or **calls an executable
script**. The pull/apply agent invokes these named tasks from a pinned
`service.v1`; the same named task can later be bound to a `component.v1` node.
Determinism requires that a task's inputs, outputs, and idempotency are explicit;
a task that cannot be made idempotent must say so and be gated.

### 3. Contracts — additive, no new node kind

- **`component.v1`** — the *nodes* (self-contained packages: typed contract,
  capabilities, envelope, entry).
- **`network.v1`** — the *wiring* (components + edges, content-addressed).
- **`service.v1`** — the *signed, content-addressed host-state manifest*: host
  binding + a **pinned** reference (no floating versions) + **secret refs** +
  **health** + the named task bindings from §2. It is **not** a node type.

`service.v1` is defined and used **from the first cut**, initially bound to a
pinned **bootstrap host definition** (the NixOS system); in later phases it is
bound to a **component composition** (`components.lock` + `network.v1`). The
agent's verify-then-apply contract does not change across graduation.

### 4. Provisioning — immutable, declarative, provider-agnostic

Hosts are declared as code and built from a **pinned NixOS** image (flake-pinned;
atomic activation + rollback). An **Ansible over a pinned Ubuntu LTS** definition
is the documented fallback. The host definition is **provider-agnostic**, so the
same definition targets the first intranet host and a later VPS. The first target
is the operator's **intranet development host** (treated as **cattle**,
rebuildable from spec); a low-budget VPS is a later, prod/offsite phase.

### 5. Identity, enrollment, and secrets

- **Enrollment (fail-closed).** A host built from the pinned image generates its
  **WireGuard keypair** and an **OpenBao machine identity** on first boot. The
  operator **explicitly authorizes** the host's public identity against an
  allowlist (TOFU + explicit approval, audited). **Nothing is trusted before
  enrollment**; per-host credentials are revocable; rotation uses the existing
  overlapping trust windows (`cbp/trust.py`).
- **Secret store.** An **OpenBao** (Vault-API, Linux Foundation) store is the
  bootstrap secret store. A one-time **SOPS + age** bootstrap credential is
  delivered at host build; the host then authenticates by machine identity.
  Secret operations are **human-gated and audited at L1**. The store runs on the
  intranet host first and is split out later.
- **The component-signing private key stays offline with the operator** and is
  **never** placed in the store or any repo; only the **public** trust root is
  committed.

### 6. Continuous deployment — pull, verify, apply

A release is a **signed, content-addressed `service.v1` manifest**. The flow is:

```
private infra repo  →  CI (may use network): build + sign service.v1
                    →  publish to the private mirror/registry
                    →  host agent: pull → verify (hash + signature + transparency)
                    →  apply on explicit L1 human approval → record to transparency
```

Auto-apply arrives only when the operating level rises (L2+), per
[`.agentfactory.toml`](../.agentfactory.toml). Nothing is applied unverified
(Law 8), and publication is always signed and recorded.

### 7. Rollback and idempotence

Activation is **atomic** (NixOS generations). Applying the **same manifest hash
is a no-op** (idempotent). A failed health check **auto-rolls-back** to the prior
generation and records an explicit, audited failure. There is no partial state:
a task either completes or the host reverts.

### 8. Observability and audit

Deploy events are appended to the existing **transparency log**
(`cbp/transparency.py`). A **readiness gate** in the `cbp-check` spirit runs
before and after apply; logs are structured JSON via `journald`; a `/healthz`
endpoint reports liveness. **No cloud APM** (network) is used.

### 9. Networking and TLS

The mesh is **WireGuard, self-managed** (operator-held keys; peers declared in
the host/service manifest); **no public ports**. Every non-loopback bind is an
**explicit, audited opt-in**, per the transport doctrine. TLS is deferred to the
public phase: a **Caddy + ACME (Let's Encrypt)** read-only public endpoint, with
Gitea behind the mesh.

### 10. Gitea configuration (first cut)

Gitea is pinned by **release digest/checksum** (recorded + signed). First cut:
**SQLite** (low budget, single host), local-disk storage, **Packages**
(OCI/generic) enabled for component artifacts, and **nightly encrypted off-host
backups** of DB + repositories + LFS + config. A **tested restore (DR drill)** is
an acceptance gate. PostgreSQL and object storage are declared, non-breaking
later phases. The self-hosted server is an accepted single point of failure
(SPEC-0007), mitigated by mirror + cache + offline fallback.

### 11. CI gating — two distinct pipelines

The **component CI stays hermetic and network-free**. The **private infra repo
has its own CI that may use the network** (lint/plan → build → sign → publish);
the "no network in CI" non-goal is **narrowed explicitly to the component CI**,
and the convention guards are updated to match. Every publish is signed and
recorded to transparency.

### 12. Umbrella lock location

A **public** core umbrella `components.lock` may be committed **only if every
origin is public** (public repository URLs or the offline directory source). The
**system umbrella lock** (private components, mirror origins) lives in the
**private** infra repo, signed by the operator key. Boot verifies the lock
signature and transparency either way.

### 13. Public/private boundary — default private

**Public:** the whitepaper/architecture narrative, **origin-agnostic** specs,
schemas, and invariants (including this spec), and later the minimal
full-service implementation. **Private:** inventory, origins, secrets, deploy
configs, tenancy/branding, and any "secret sauce." **Publicizing is an explicit
act; if in doubt, keep it private.** No hostname, IP, private URL, key, tenant
name, or branding appears in a public repo (SPEC-0016 §94).

### 14. End-state seams (declared, later phases)

- **Multi-tenancy:** tenant = Gitea organization/namespace + OpenBao namespace.
- **Branding:** theme/config in `service.v1` — **never** a code fork.
- **Source-of-truth migration:** moving project repos to the self-hosted server
  is a later phase; **GitHub becomes a one-way, reviewed marketing mirror** of
  public artifacts only.
- **Invariant:** tenancy and branding **never enter verification semantics**
  (SPEC-0016 — one verified semantics at every tier).

### 15. Parked

Container/cloud sandbox backends (e.g. Docker Sandboxes) and the OCI
sandbox-package format are a **separate, declared-gated spec**: a **local-first
A0 sandbox backend** and a `component.v1 ⇄ sandbox-package` adapter. They are
**not** part of this spec and never enter the launch path (Law 5).

## Scope

- **In:** the layered model and bootstrap boundary; the callable/script task
  model; the `service.v1` contract; immutable provisioning; enrollment/identity;
  the OpenBao secret store; pull-based verify-then-apply CD; rollback/idempotence;
  deploy audit; mesh/TLS posture; Gitea configuration; the two-pipeline CI split;
  umbrella-lock placement; the public/private boundary; and the declared
  end-state seams.
- **Out (explicit non-goals):** changing `PRINCIPLES.md`; any `src/` verification
  semantics; putting a private key, private origin, tenant name, or branding in a
  public repo; giving the **component** CI network access; auto-apply below the
  declared level; adopting cloud sandboxes or a new execution backend here; HA
  and multi-tenancy in the first cut (declared later phases).

## Acceptance criteria

- [ ] The layered model, the **bootstrap set**, and its **graduation** to managed
      components are stated and testable.
- [ ] Every operational task is a **named, idempotent, fail-closed callable or
      executable script**; a non-idempotent task is explicitly gated.
- [ ] `service.v1` is defined (additive) as a signed, content-addressed
      host-state manifest with **no floating references**, and is verified before
      apply.
- [ ] A host is provisioned from a **pinned** host definition and reaches
      readiness **idempotently**; re-applying the same manifest hash is a no-op.
- [ ] Enrollment is **fail-closed**: nothing is trusted before explicit operator
      authorization; credentials are per-host and revocable.
- [ ] Secrets are served by OpenBao via machine identity; the **bootstrap
      credential is the only SOPS/age secret**; the signing private key is never
      in-repo or in the store.
- [ ] A release flows **private repo → signed `service.v1` → verify → apply on
      L1 approval → transparency**, and a failed health check **auto-rolls-back**
      with an audited failure.
- [ ] Deploy events are reconstructible from the transparency log.
- [ ] The **component CI remains hermetic**; the infra CI is separate and its
      network use is documented; the non-goal is narrowed and guards updated.
- [ ] A **public** umbrella lock is committed only with public origins; private
      origins appear only in the private repo.
- [ ] The **DR drill** (backup → restore of DB + repos + LFS + config) passes.
- [ ] No public repo contains a hostname, IP, private URL, key, tenant name, or
      branding.

## Phases

| Phase | Status | Deliverable |
| --- | --- | --- |
| 0 | draft | this spec; the private companion (instantiation); repo split decided |
| 1 | plan | bootstrap on the **intranet host**: pinned NixOS, native Gitea (SQLite, Packages) + OpenBao, WireGuard + enrollment, install the pull/apply agent, define and use `service.v1` bound to the pinned host definition, verify-then-apply with generation rollback, transparency-logged deploys, **DR drill** |
| 2 | plan | **graduate** the bootstrap services to `component.v1` wrappers (wrap → content-address → sandbox → conformance); `service.v1` binds the composition; self-hosting |
| 3 | plan | **low-budget VPS** prod/offsite + public **read-only** mirror (Caddy + ACME); offsite backups; same host definition, parameterized |
| 4 | plan | **multi-tenant + branding** (Gitea orgs + OpenBao namespaces; `service.v1` config; PostgreSQL) |
| 5 | plan | **source-of-truth migration**; GitHub becomes a one-way marketing mirror |
| 6 | plan | hardening: HA, mirror-outage, rotation rehearsal |

## Risks / invariants

- Respects [`PRINCIPLES.md`](../PRINCIPLES.md) Laws 1–13. No `src/` verification
  semantics change on acceptance of this spec alone.
- **Law 5** is preserved: distribution/services are **acquisition-time**; run
  time stays local-first. Cloud execution is out of the launch path.
- **Least privilege (Law 7) / explicit failure (Law 8):** fail-closed enrollment,
  verified-before-apply, atomic rollback, no silent success.
- **No secrets in git, ever** (Law 13.3; SPEC-0016 §94); signing keys stay
  operator-side and out-of-process.
- **Single point of failure** (SPEC-0007): accepted and mitigated by mirror +
  cache + offline fallback, with a DR drill as a gate.
- **Additive-only** contracts (`component.v1` / `network.v1` / `service.v1`);
  tenancy and branding never alter verification semantics.
- **Bootstrap TCB is permanent:** keep the bootstrap set as small as possible;
  every item in it is trusted code.

## References

- [`SPEC-0007`](SPEC-0007-harness-shell-component-distribution.md) — harness/shell
  split; distribution phases; the server-as-SPOF risk register.
- [`SPEC-0011`](SPEC-0011-minimum-viable-product.md) / [`SPEC-0016`](SPEC-0016-editions-distribution-cloud.md)
  — Core uses offline directory + local signing; the live mirror is deferred; no
  private keys/origins/pro code in public.
- [`SPEC-0012`](SPEC-0012-component-abi.md) — the component ABI and dynamic
  task-loading the callable/script model extends.
- [`SPEC-0013`](SPEC-0013-cross-runtime-conformance.md) — a foreign runtime is
  TCB (pinned, signed, recorded).
- [`SPEC-0015`](SPEC-0015-provenance-assurance-labels.md) — wrap →
  content-address → sandbox → conformance for graduated services.
- [`docs/transport_trust_boundary.md`](../docs/transport_trust_boundary.md) —
  loopback-by-default; non-loopback is an explicit, audited opt-in.

> The concrete instantiation (inventory, origins, secrets, runbooks) lives in a
> **private** companion repository and is intentionally not linked here.
