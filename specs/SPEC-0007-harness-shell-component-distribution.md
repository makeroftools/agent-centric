---
id: SPEC-0007
title: Harness, shell component, and component distribution
type: feature
target_repo: agent-centric
target_branch: main
status: accepted
owner: operator
---

# Harness, shell component, and component distribution

This spec defines how `agent-centric` **executes** a CBP tree of independently
versioned components, and how those components are **distributed**. It is
**target architecture with phases**: each capability carries an explicit status
(`delivered`, `MVP`, `roadmap`, `declared-gated`). No claim exceeds what the
operator's suite proves.

## Context

Today everything lives in one repo and one Python package; components are
described by an import path (`AgentComponentManifest.entry_point`), an
in-process, shared-namespace model. The operator intends **many** components,
each **self-contained and independently versioned**, distributed from a
**self-hosted git server**. The passive-registry design already anticipates this
(`fbp/registry.py`, `fbp/spec.md` §3c: a registry that stores only a location,
with other agents doing fetch/compile/run under grant).

Two existing rules constrain the change and are amended here (operator-approved):

- Law 5 said "no distribution, no networking" — true for **run time**, not for
  **acquisition**.
- `KERNEL.md` listed "distribution" as an out-of-scope concern for the v0
  Manager-line kernel; that bullet is superseded for component distribution.

## 1. Two layers

| Layer | Is a component? | Owns |
| --- | --- | --- |
| **Harness — `agent-centric`** | **No** | boot, runtime/executor, contract definitions, passive registry, resolver/pinner, `components.lock`, signing/verify tooling, constitution, specs, CI |
| **Tree — components** | **Yes** | root **shell component**, domain components, LLM components (`kind: model`), each self-contained with local SQLite state |

The harness **executes** the tree; it is not a node in it. The tree's root is the
**shell component** — an ordinary component. This removes any bootstrap cycle:
the harness defines the interface it executes, so it cannot depend on a node for
its own definition.

## 2. Vocabulary

- **Component** — the only unit; an FBP node and an ABM agent. Self-similar.
- **Atomic / composite / model** — component kinds. A composite **contains** its
  children (embeds them) or **points to** them (pinned external components).
- **Directive** — a component's agent-facing `SKILL.md`; a skill is a directive
  that maps to a component of the same name (SPEC-0006).
- **Lock** — `components.lock`: the pinned, signed set of external components.
- **Harness contract** — the versioned interface the harness executes.

## 3. Contracts (harness-owned, additive)

These are interface definitions, **not** components (an interface is not an
agent). They live with the harness and are additive-only.

### 3.1 `component.v1`

```
component.v1: {
  name, kind: atomic|composite|model,
  implements: [<contract-version>...],
  ports: {in: [], out: []},
  capabilities: [...],
  grants: {...}, envelope: {...},
  state: {kind: sqlite, path, single_writer: true} | null,
  entry: {runtime: python|process, entrypoint: <module:qualname|exec>},
  children: [ {embed: <relpath>} | {ref: <name>} ],
  directive: <path-to-SKILL.md> | null,
  provenance: {repo_url, commit_sha, tree_sha256, signature}
}
```

A component may **contain** children (`embed`) or **point to** them (`ref`).
Embedded children are covered by the parent's `tree_sha256`; referenced children
get their own lock entries.

### 3.2 `components.lock`

```
components.lock: {
  schema: "components.lock/v1",
  harness_contracts: {component: "1", network: "1", ...},
  root: <shell-component-name>,
  entries: [
    {name, repo_url, commit_sha, tree_sha256, implements:[...],
     capabilities:[...], signature}
  ]
}
```

`lock_hash = sha256(canonical_serialization)`. **Umbrella commit + `lock_hash` =
the whole-system content address.**

The existing `manifest.vN` remains the per-component conformance declaration
(adapter; no semantic change).

## 4. Resolve → verify → boot (deterministic, fail-closed)

1. Read the lock; verify the **lock signature** and `lock_hash`.
2. For every entry, in deterministic order (sorted by name, then dependency
   order): fetch from mirror/cache or a local path → check out the exact
   `commit_sha` → verify `tree_sha256` → verify the component signature → verify
   `implements` against `harness_contracts`.
3. Embedded children are verified inside the parent tree hash; referenced
   children resolve from the same lock.
4. Build the component graph (`network.v1`), instantiate the **root shell
   component**, run.
5. Any mismatch, absence, or unknown contract version is an explicit failure
   (Law 8). Every resolution is recorded (Law 6). If all artifacts are present
   in cache/mirror, **no network is touched**.

**Tag rule:** a release tag is resolved to a commit **at pin time** and never
trusted at resolve time; the lock stores commits only.

## 5. Network boundary (Law 5, amended)

- **Run time** stays local-first: in-process or local; no networking, no cloud,
  no distribution required to *run*.
- **Acquisition** may fetch **pinned, signed, verified** components; it is
  **offline-capable** (local mirror/cache) and **hermetic in CI**.
- Remote/ZMQ transport remains a **declared-gated** backend (SPEC-0002 §5).

## 6. Trust model

- **Root of trust:** an operator-held signing key operated by an **isolated,
  automated signing service** (machine-performed, scoped to signing releases).
- **Two independent integrity layers:** signed manifest/lock **and** content
  hash (`tree_sha256`) — a git SHA-1 collision alone is insufficient. Repos
  should move to SHA-256; until then `tree_sha256` is mandatory.
- **Transparency:** every signed release is appended to an append-only, signed
  registry log (Law 10: evidence is immutable), so an unauthorized signature is
  detectable.
- **Rotation:** scheduled and on suspicion, with overlapping trust windows; key
  IDs recorded in the log. An optional second signer enables threshold signing.
- **Three clamp points** (`fbp/spec.md` §3c): registry write, consumption, and
  execution are each explicitly gated.

## 7. Invariants (clamps)

1. Signatures are mandatory; verification fails closed.
2. Pins are `commit_sha` + `tree_sha256`; no floating versions at run time.
3. The resolver is deterministic: **resolving twice yields the same
   `lock_hash`**.
4. Lock changes are constitutional-level: a spec + operator review; never
   auto-bumped.
5. The cache is content-addressed, atomic (temp + fsync + rename), and verified
   on read.
6. Unknown contract versions fail closed (strict negotiation).
7. Adapter-equivalence (shadow-run; replay + audit hashes match) is required
   before any old path is retired.

## 8. Risk register — every risk is a gate

| Risk | Clamp | Gate |
| --- | --- | --- |
| Tampered component | sign; pin commit+hash | resolver verify; CI verify |
| Retagged release | resolve tag→commit at pin time | lock stores commits only |
| MITM at fetch | SSH/TLS pin + hash verify | hash mismatch fails |
| SHA-1 collision | independent `tree_sha256` + signature | tree hash verified |
| Registry poisoning | namespacing; append-only signed log | registry schema/name guard |
| Key compromise | offline-ish automated signer; rotation | signature verify + log |
| Floating versions | ranges only at pin time | guard rejects ranges |
| Nondeterministic resolve | canonical resolver | resolve-twice ⇒ same hash |
| Lock/tree drift | lock hash in umbrella commit | CI consistency check |
| Server outage | mirror + cache + vendor fallback | CI offline mode |
| Partial/corrupt fetch | atomic materialize | verify before use |
| Cache poisoning | content-addressed cache | verify on read |
| Contract mismatch | conformance + least privilege | resolve-time conformance |
| Unverified effect | verifier on upward path | existing verifier (Law 8) |
| State corruption | WAL; single-writer; integrity check | fail-closed on open |
| Graph errors | `network.v1` validation | existing `NetworkError` |
| Adapter divergence | shadow-run | equivalence test before retire |
| Silent distribution | acquisition-only, bounded | guard encodes boundary |
| Operator error | lock changes need spec+review | review; no `--no-verify` |
| Disaster | server + key backups | DR drill (Phase 5) |

## 9. Phases

| Phase | Status | Deliverable |
| --- | --- | --- |
| 0 | **delivered** | this spec; Law 5 amendment; KERNEL note; `AGENTS.md` + `docs/agent/architecture.md`; guard |
| 0.5a | **delivered** | `component.v1` + `components.lock/v1`; content-addressed atomic cache; deterministic resolver; minisign/gpg signature verification (system tool); offline directory source |
| 0.5b | roadmap | automated signing service; append-only transparency log; live self-hosted mirror |
| 1a | **delivered** | example `counter` component; deterministic bundle; offline git source + pinner; resolve→verify→load→run with an allowlisted entry; replay-from-lock proof. The committed umbrella `components.lock` lands in Phase 2 with the shell component; live server + signing service are 0.5b |
| 1b | roadmap | extract `bills_registry`; SQLite state descriptor; contains + points-to |
| 2 | roadmap | registry component + shell component; boot from lock |
| 3 | roadmap | directives aggregated into `.agents/skills/`; LLM components pinned; `review.v1` |
| 4 | roadmap | incremental migration; hermetic/offline CI; retire monolith |
| 5 | roadmap | hardening verification: DR drill, rotation rehearsal, offline CI, mirror-outage |

## 10. Scope

- **In:** the two layers; `component.v1`; `components.lock`; resolve/verify;
  network/trust boundaries; the risk register as gates; the Law 5 / KERNEL
  amendment; `docs/agent/architecture.md`; guard extensions.
- **Out:** any `src/` implementation (Phases 0.5+); changing `PRINCIPLES.md`
  laws other than Law 5; KERNEL invariants; execution backends beyond inproc.

## 11. Acceptance criteria

- [x] `PRINCIPLES.md` Law 5 distinguishes run-time local-first from
      acquisition-time distribution and references this spec.
- [x] `KERNEL.md` marks the "distribution" non-goal superseded for component
      distribution.
- [x] `AGENTS.md` and `docs/agent/architecture.md` describe the harness-vs-tree
      split; examples live under `examples/`.
- [x] The convention guard validates the above.
- [ ] Later phases: their own acceptance criteria.

## 12. Risks / invariants

Respects Laws 1, 2, 4, 5 (amended), 6, 7, 8, 10, 11, 12, 13. It changes no
`src/` semantics in Phase 0 and no KERNEL invariant; the harness extends the
existing `fbp` driver/registry/network adapter-first. Residual risks that cannot
be fully gated — a zero-day in a trusted component, signing-service compromise,
SHA-1 collision, and the self-hosted server as a single point of failure — are
operator-accepted and mitigated, not eliminated.
