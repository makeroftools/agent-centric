---
id: SPEC-0019
title: Isolated holdout validator (the L3 gate)
type: feature
target_repo: agent-centric
target_branch: main
status: draft
owner: operator
---

# Isolated holdout validator (the L3 gate)

> **Implementation status.** The isolated validator core is built as an
> importable library (`src/agent_centric/cbp/holdout.py`) behind the thin,
> separate-process CLI (`tools/holdout-validate.py`). It supports the additive
> `service` probe (boot a signed composition through the core harness and compare
> a canonical-JSON projection), pins its own TCB, distinguishes infrastructure
> flake from semantic nondeterminism, emits a wall-clock-free idempotent record,
> has a synthetic known-good/known-bad `--self-test`, and enforces an advisory
> principal guard. Operator-authored scenarios, the authoritative green run on a
> distinct principal/host, and the `[levels.L3]` flip remain operator gates; see
> the L3 milestone plan (`docs/agent/l3-milestone-plan.md`) and `HANDOFF.md`.

## Context

`.agentfactory.toml` targets **L3 (dark-factory)** but keeps it `declared-gated`:
L3's review is `none-on-green`, which is only safe if an **independent,
author-blind** check can decide "green". The governing invariant — "verification,
not generation, is the constraint on autonomy" — is already recorded in
[`docs/agent/verification.md`](../docs/agent/verification.md), and the gate is
already reserved: `specs/holdout/` holds author-blind acceptance scenarios and
[`docs/agent/levels.md`](../docs/agent/levels.md) lists the `holdout` gate as the
isolated validator (this spec builds it). [SPEC-0017](SPEC-0017-frontier-workstreams.md) §4
parks the validator as a frontier workstream and requires it be promoted to its
own spec before implementation. This is that spec.

Today the strongest merge gate is `pytest` (L2: "an isolated validator runs the
suite"). But the suite is authored beside the code, so it cannot be the *sole*
constraint at L3: an agent that can edit both the code and its tests can make
them agree. The holdout validator breaks that coupling. Scenarios are authored by
the operator and never seen by the authoring agent; they are executed against an
**ephemeral deployment** of the built tree by a program the authoring agent
cannot run.

**Why now.** L3, auto-labeling at scale (SPEC-0015), and any self-improvement of
verification (SPEC-0017 §5) are all blocked on this. It is the single unlock for
the target level.

## Decision

### 1. Three isolated roles

| role | sees `specs/holdout/` | runs the validator | may merge |
| --- | --- | --- | --- |
| **author** (agent) | never | never | no |
| **validator** (program) | yes (its input) | yes | n/a |
| **operator** | authors scenarios | may run | yes |

Isolation is **structural, not advisory**, and it rests on three facts:

1. **The scenarios are not in the public repo.** `core/specs/holdout/` holds only
   a placeholder `README.md` (guard-tested); real scenarios live in the
   operator-private holdout root (`$CBP_HOLDOUT_ROOT`, default
   `$HOME/.cbp/holdout/suite`), which is **owner-only** (`0700`). The validator
   **refuses** if the suite directory is group- or other-accessible.
2. **The authoring principal is unprivileged and distinct.** The authoring agent
   runs as a separate user (`agent-runner`) with **no `sudo` and no `docker`**
   (docker is root-equivalent), so it cannot read the operator's `0700` space.
3. **The validator is a separate process.** It refuses unless
   `CBP_HOLDOUT_CONTEXT=validator` is set.

An `opencode.json` read-deny of `specs/holdout/` is kept as defence in depth, but
it is **not** the guarantee: with `bash: allow` it is advisory — which is exactly
why (1) and (2) carry the weight. The **authoritative L3 gate must not share a
principal or host with the author**; the operator workstation is a rehearsal.

**Advisory principal guard.** As defence in depth the probe run also refuses on a
privileged principal (`geteuid() == 0`, or membership of the `docker` group,
which is root-equivalent). This guard is **in-process only** and cannot prove
host/principal isolation; that isolation is **environment-enforced** by facts
(1) and (2). A `--rehearsal` run bypasses the guard for offline development and
**records** `isolation: rehearsal`; only an `isolation: enforced` record is
authoritative.

### 2. Scenario contract — deterministic and author-blind

A scenario is Markdown + YAML front-matter in `specs/holdout/`, using the format
already reserved by its README:

```yaml
---
id: HO-0001
service: cbp
feature: <feature>
priority: high        # high | normal
---
```

The body has two parts:

- a **plain-language** statement of the required behaviour (for humans and the
  operator's record), and
- a fenced `check` block: a **deterministic, declarative** probe naming a
  `service.v1` task, a booted composition, or a ledger state. There is **no
  free-form shell**; the probe vocabulary is a pinned, additive contract,
  **`holdout.v1`**, with two kinds:
  - **`task`** — run the named task `target` (a `service.v1` task executable) and
    require `expect_exit` (default `0`); a missing/unstartable task is
    infrastructure flake, a wrong exit is semantic failure.
  - **`service`** — load the scenario's operator-private
    **`holdout.deployment/v1`** descriptor (content-pinned by
    `deployment_sha256`), boot the signed composition under test through the core
    harness (`boot_from_lock`), run its declared observable, and compare the
    **canonical-JSON projection** to `expect` (or assert a fail-closed refusal
    when `expect_refused` is true).

Scenarios are content-addressed. The validator verifies a **`holdout.lock`**
(suite hash + per-scenario digest + probe-contract hash + TCB pins) and refuses
on any drift **before** running anything — the same fail-closed lock/contract
discipline as the conformance certifier (`exit 3` = refused before running;
`exit 2` is reserved for usage errors).

### 3. Ephemeral deployment, then teardown

For each run the validator:

1. loads the scenario's **`holdout.deployment/v1`** descriptor (operator-private,
   content-addressed) and verifies its `lock_hash` and referenced trust root; the
   validator hard-codes **no** private path or slug;
2. boots the tree from the **signed, content-addressed lock under test**
   (`cbp/boot_network.py` / `boot_from_lock`), verifying the lock signature and
   every entry signature;
3. runs the scenario's probe against that tree with a **pinned deterministic
   seed**, wall-clock frozen, no network, and no ambient filesystem;
4. captures a deterministic observation (the observable's canonical-JSON
   projection), then tears the deployment down.

Nothing persists between runs; every run is a fresh, reproducible instance. A
deployment descriptor that is missing or whose content address does not match its
pin **refuses before running**. A tampered lock or entry signature inside the
composition is a **scenario failure** (a negative scenario asserts the refusal
with `expect_refused`), never a silent pass.

### 4. Supermajority and overall threshold

Per the reserved format, each scenario is run **R = 3** times on fresh instances
and a scenario **passes on a supermajority (2-of-3)**. A change is **green** only
if:

- every `priority: high` scenario passes on **all three** runs (no supermajority
  relaxation for high-priority), **and**
- the overall pass fraction meets a pinned threshold (default: normal scenarios
  2-of-3, aggregate ≥ the configured floor).

A **nondeterministic** observation (the same normalized input yields different
results across runs) is **not** a pass: it is recorded as a failed run and fails
closed — the system's premise is determinism. Supermajority absorbs
**infrastructure flake** (e.g. a sandbox failing to start); it never absorbs
semantic nondeterminism. The validator distinguishes the two, and semantic
nondeterminism always fails.

### 5. The gate and the level transition

- L3 `can_merge = true` requires the validator to **exist and be green**; the
  `holdout` gate is named in `.agentfactory.toml` L3 with
  `on_missing_gate = "fail-closed"`.
- `[levels.L3] status = "declared-gated"` is flipped to enabled **only by the
  operator**, as a deliberate edit, after the validator passes its **self-test**
  on a known-good / known-bad pair.
- The validator is **TCB**: pinned, signed, content-addressed, and
  version-recorded (the SPEC-0013 doctrine for a foreign runtime), so a verdict
  is reproducible and auditable. `holdout.lock` pins `probe_contract_sha256` and
  `validator_sha256`; the record carries `runtime`, `platform`,
  `lock_sha256`, and `suite_sha256`.

### 6. Recording

Every validator run emits a deterministic **holdout record**
(`holdout.record/v1`: validator version + TCB hashes, runtime/platform, the
`isolation` mode, lock hash, suite hash, per-scenario verdicts, aggregate) and
appends it to an **append-only ledger** in the same operator-private root
(`$HOME/.cbp/holdout/runs.jsonl`, mode `0600`). The record is pure — **no
wall-clock** — so a retried run is **idempotent for its head**: a record whose
content fingerprint is already present is never appended twice. The record is the
evidence for the merge decision; a merge at L3 is reconstructible from it. Only a
record with `isolation: enforced`, produced on a principal and host distinct from
the author, is authoritative.

## Scope

- **In:** the isolation model and its enforcement; the `holdout.v1` scenario
  contract (`task` + additive `service`), the `holdout.deployment/v1` descriptor,
  and `holdout.lock`; ephemeral deployment of the built tree; the multi-run /
  supermajority / threshold decision rule; the level transition; the
  deterministic record; the TCB pins; the known-good/known-bad self-test; and the
  component-ready boundary.
- **Out:** scenario content (authored by the operator, author-blind); any change
  to `src/` verification semantics or the conformance contract; `review.v1`
  (SPEC-0017 §1); the formal-verification / Assurance-Label tiers A1–A4
  (SPEC-0015); auto-labeling and RSI (SPEC-0017 §5); validator HA; the
  authoritative green run and the level flip (operator gates).

## Acceptance criteria

- [x] The authoring context cannot read `specs/holdout/` (deny rule present and
      guard-tested); the validator runs as a separate process.
- [x] `holdout.v1` freezes the deterministic probe vocabulary (`task` + additive
      `service`); each scenario carries a content-addressed digest and
      `holdout.lock` pins the suite and the TCB.
- [x] The validator boots the signed lock under test, verifies signatures, and
      refuses on lock/contract drift **before** running.
- [x] Each scenario runs R=3 on fresh ephemeral instances; pass = 2-of-3;
      high-priority scenarios require all-run pass; the aggregate threshold is
      pinned.
- [x] Semantic nondeterminism fails closed; infrastructure flake is distinguished
      and never silently passes.
- [x] A green run is required for an L3 merge; `.agentfactory.toml` flips to L3
      only on the operator's deliberate edit, after the self-test.
- [x] Every run yields a deterministic, wall-clock-free record appended to the
      ledger.
- [x] The validator is pinned/signed/version-recorded, and a `--self-test`
      proves it fails a known-bad change.
- [ ] The authoritative green run is produced on a principal/host distinct from
      the author, and the operator flips the level. **(operator gate)**

## Phases

| Phase | Deliverable | Status |
| --- | --- | --- |
| 0 | this spec; `holdout.v1` probe vocabulary + `holdout.lock` schema | done |
| 1 | isolation enforcement (read-deny + separate validator process + advisory principal guard) and the guard tests | done |
| 2 | ephemeral deployment + deterministic observation (`service` probe + `holdout.deployment/v1`); the `holdout` gate named in `.agentfactory.toml` | done (agent-side) |
| 3 | supermajority/threshold decisions, the record + ledger, and the known-good/known-bad self-test | done |
| 4 | operator flips L3 `status`; auto-labeling (SPEC-0015) and RSI (SPEC-0017 §5) unblocked | operator gate |

## Risks / invariants

- Respects Laws 1, 2, 5, 8, 11, 12; fail-closed; no unverified success.
- **Isolation must be structural.** If the author can read the scenarios or run
  the validator, the gate is theater; the read-deny rule and the separate
  validator process are the guarantee, and both are guard-tested.
- The validator is TCB: pin, sign, and record it, or the verdict is
  non-reproducible.
- Adding the gate changes **no** `src/` semantics; the level transition is an
  operator edit, never a prompt side effect.
- Determinism is the premise: supermajority absorbs flake, never semantic
  nondeterminism.
- **Component-ready, graduated later.** The validator is a pure core library plus
  a component-ready entry/contract; the authoritative validator lives **outside**
  the composition it validates (bootstrap circularity). Tooling graduates in
  order: lock compiler → validator/holdout → signing → provisioning.
