# Roadmap plan — the next cycle (post-SPEC-0020/0021)

> **Status: planning-session output. Nothing in §3+ is implemented by this
> session.** This page is the plan of record for the **next** roadmap cycle. It
> was produced in a roadmap-planning session at the workspace root `cbp/`, as the
> handoffs' *Next* requires ("roadmap-planning session first — do not implement
> ahead of it"). Read [`../../HANDOFF.md`](../../HANDOFF.md),
> [`../../AGENTS.md`](../../AGENTS.md), [`../../PRINCIPLES.md`](../../PRINCIPLES.md),
> and [`../../.agentfactory.toml`](../../.agentfactory.toml) first; if this page
> disagrees with `PRINCIPLES.md`, the laws win. Operator gates are unchanged and
> must not be crossed: real host provisioning/enrollment, any signing-key act, any
> public publish, and any edit to `.agentfactory.toml`.

## 0. What this session is

A planning pass, not an implementation pass. It (a) re-verifies the delivered
state, (b) fixes the **single remaining unit that is buildable below the operator
gates** — a concrete `provider.v1` adapter — and its placement, design, and
acceptance, and (c) sequences the operator-gated and research work so the next
implementing session starts from a ratified plan rather than an improvised one.
**No code path changed in this session.**

## 1. Verified starting facts (re-run before acting)

Re-verified with the commands in [`../../HANDOFF.md`](../../HANDOFF.md)
§"Verify everything"; the anchor there is authoritative.

- All four repositories are on `main` and in sync with `origin/main`.
- **core:** `ruff` clean; `mypy` 135 files; `cbp-check` **READY (8/8)**; suite
  **1864 passed**.
- **conformance:** shared ABI **31/31** in-process **and** over the external
  protocol; `network.v1` **14/14**; `appointed.v1` **8/8**.
- **pro (Rust):** shared **31/31** + WASM **10/10** + network **14/14** +
  appointed **8/8**; **6** unit tests. Cross-runtime equivalence holds.
- **infra (private companion):** **109 passed, 1 skipped**.
- **basedpyright (workspace):** **0 errors, 0 warnings, 0 notes**.

Delivered below the gates: Layers 0–4 + signing; the distribution spine
(`component.v1`, locks, boot, transparency, signing-key rotation); `review.v1` +
confidence; SPEC-0002 Phase-1 (adapter-first); SPEC-0018 Phases 1–2 + the
provisioning specification (P0–P9) + the local CD substrate; the offline umbrella
lock; SPEC-0019's L3-ready validator; the documentation/spec-hygiene pass; and the
**SPEC-0020/0021 realization** — `contracts/provider.py` + `cbp/provider_runtime.py`
and `contracts/work.py` + `cbp/work_runtime.py` + `cbp/work_boot.py`.

## 2. The decision this session

1. **The only unit buildable below the operator gates is a concrete
   `provider.v1` adapter.** Everything else on the remainder list is either
   operator-gated (provisioning, the L3 flip, key acts, publishing) or a research
   charter (no design freeze).
2. **Ratify a local-first, task-executing adapter** as the next build unit, behind
   the **existing injected seams** — `ProviderAdapter.call` in
   [`../../src/agent_centric/cbp/provider_runtime.py`](../../src/agent_centric/cbp/provider_runtime.py)
   and the `TaskRunner` discipline already used by the private pull/apply agent.
   It is the natural first adapter (PRINCIPLES Law 5, local-first) and it exercises
   the runtime's plan → verify → apply → health → rollback loop against a real
   executable substrate instead of only the in-memory test double.
3. **Placement split.** The *generic* adapter mechanism (map a typed provider call
   to a pinned, named local task; content-verify before running; parse canonical
   output) is **provider-agnostic** and belongs in **core**, additively, with
   offline tests. The *concrete* provider manifest and task wiring (which
   executables, which resources, which secret refs) are host-specific and belong
   in the **private companion**. Core never learns a private path or an origin.
4. **Everything else is sequenced, not started** (§4–§8), and the operator gates
   are front-loaded (§10).

## 3. Workstream A — concrete `provider.v1` adapter (local-first; below the gates)

**A0. The gap this closes.** `ProviderRuntime` drives `observe → plan → verify →
apply → health` through `ProviderAdapter.call(ProviderCall)`. Only
`InMemoryProviderAdapter` exists today. The provider manifest names the task roles
`plan` / `apply` / `health` / `teardown`
([`contracts/provider.py`](../../src/agent_centric/contracts/provider.py)); the
runtime additionally needs an **observed-state** read. The plan resolves this
**additively**.

**A1. Resolve the observe role, additively.** The provider contract has no
`observe` role. Two acceptable shapes; pick one in the implementing session and
record why:
- **(preferred)** declare an optional, read-only **`observe`** task role in the
  provider manifest (additive: `ProviderTask.OBSERVE = "observe"`, not required by
  `validate_provider`), whose canonical-JSON output is the observed-resource list;
  or
- derive observed state from the `plan` task's read-only projection if a provider
  already emits it.
Additive-only: `provider.v1` is extended, never broken; the existing required
roles and `validate_provider` defaults are unchanged.

**A2. `LocalTaskProviderAdapter` (core, new module `cbp/provider_local.py`).**
- Implements the `ProviderAdapter` protocol. Constructed with the ratified
  `Provider` manifest, an injected `TaskRunner`-style callable, the tasks
  directory, and (optionally) a `TaskVerifier`.
- **Maps** each `ProviderCall` op to the manifest's named `TaskBinding`:
  `observe`/`apply`/`health`/`teardown`; an `apply` call passes the resource
  `kind`/`name`/`change` and the step `digest` as explicit arguments.
- **Content-verifies before running:** re-hash every **mutating** task's bytes on
  disk against its `TaskBinding.digest` and refuse on drift (reuse the
  `assert_task_appliable` / task-digest discipline; a mutating task with no digest
  is refused outright). No partial side effect on refusal.
- **Parses deterministically:** observed resources are read from canonical JSON on
  the task's stdout; a malformed or non-canonical payload refuses cleanly.
- **No network, no crypto:** local-first; the runner is the confined I/O seam and
  is injectable for tests.

**A3. Tests (offline; no network, no key).** Extend the provider-runtime suite:
the adapter drives the full reconcile loop over a fake runner; determinism
(identical inputs ⇒ byte-identical evidence); refusal of an unpinned mutating
task; refusal of a drifted task digest; a malformed observe payload; retry
idempotency (the ledger never duplicates evidence); health-gate failure ⇒
deterministic rollback, then bounded escalation. Keep `component-abi.v1` frozen
and legacy `task.v1` untouched.

**A4. Non-goals for A.** No real provider API, no cloud adapter, no concrete
task content in core, no credential handling, no live network in the component CI.

## 4. Workstream B — the L3 flip (operator-gated; do not start)

The validator is L3-ready (SPEC-0019). Remaining, entirely operator-side:
author the author-blind scenarios in the operator-private holdout root; run
`--self-test`; run the validator **green** with `isolation: enforced` on a
distinct unprivileged principal and a separate operator-owned host; append the
record; then the deliberate `[levels.L3]` edit. Plan of record:
[`l3-milestone-plan.md`](l3-milestone-plan.md) and the private companion's
runbook. **No agent act above L1.**

## 5. Workstream C — SPEC-0018 Phases 3–6 (operator/publish-gated)

VPS + public read-only mirror; multi-tenant/branding; source-of-truth migration;
hardening — each a named, deterministic, idempotent operation per
[`../../specs/SPEC-0018-service-host-provisioning-and-continuous-deployment.md`](../../specs/SPEC-0018-service-host-provisioning-and-continuous-deployment.md)
and the private provisioning spec (P0–P9). Settle the provisioning §9 open
decisions first. **Nothing is provisioned; no publish without the operator.**

## 6. Workstream D — threshold signing (SPEC-0007 §6)

Harden releases from one signature to **k-of-n** detached signatures over a lock,
additive to the existing overlapping trust windows
([`../../src/agent_centric/cbp/trust.py`](../../src/agent_centric/cbp/trust.py)).
The verification logic can be built and tested **offline** (candidate
below-gate build unit **#2**, after A); the operator key acts stay gated.

## 7. Workstream E — self-hosting the composition

The offline self-hosting smoke already boots the pinned composition through the
harness and runs its deterministic local nodes, refusing host-bound services
fail-closed. The remaining step — applying it to a real host — is operator-gated
(see §5). No code is required below the gates beyond what A adds.

## 8. Workstream F — research charters and the SPEC-0017 frontier

Each gets a dedicated session; **no design freeze**: the brain charter
([`brain-charter.md`](brain-charter.md), SPEC-0010 + SPEC-0017 WS1) and the
learning charter ([`learning-charter.md`](learning-charter.md), SPEC-0010 +
SPEC-0017 WS5). The wider frontier is indexed in
[`../../specs/SPEC-0017-frontier-workstreams.md`](../../specs/SPEC-0017-frontier-workstreams.md).

## 9. Sequencing and dependencies

| Order | Unit | Class | Depends on |
| --- | --- | --- | --- |
| 1 | **A** — local-first provider adapter | below gates | this plan ratified |
| 2 | **D** — threshold-signing verification | below gates | A (optional) |
| 3 | **B** — L3 scenarios + enforced run + flip | operator | operator setup |
| 4 | **C** — SPEC-0018 Phases 3–6 | operator/publish | provision errata settled |
| 5 | **E** — self-hosting apply | operator | C |
| 6 | **F** — charters / frontier | research | dedicated sessions |

Rule: **verification is the constraint, not generation.** A unit is built only
when the rung below is green; no operator-gated unit is started early.

## 10. Operator gates (front-loaded, unchanged)

1. **Ratify this plan**, in particular the §2 placement split and the §A1 choice
   of observe role.
2. L3: principal/group/ACL setup, the `0700` holdout root, scenario authoring, the
   authoritative `isolation: enforced` green run, and the `[levels.L3]` edit.
3. Provision/enroll the real intranet host; install/re-sign with the operator
   trust root (including re-signing the composition and umbrella locks).
4. Any public/live-mirror publish.

## 11. Acceptance for the next implementing session

Plan ratified; `provider_local` adapter + tests green offline; `component-abi.v1`
frozen and `task.v1` untouched; `provider.v1` extended additively;
`ruff`/`mypy`/`cbp-check` green; full suite green; basedpyright `0/0/0`; all four
repos on `main`, clean, pushed; **no operator gate touched**.

## 12. Verification block

```sh
cd core
uv run ruff check .                                   # -> clean
uv run mypy src                                       # -> clean
uv run agent-centric cbp-check                        # -> READY (8/8)
uv run pytest -o addopts="" -p no:cacheprovider       # -> green (incl. new adapter tests)

cd ../conformance
python3 certifier/certify.py                          # -> 31/31
python3 certifier/certify.py --host-cmd "python3 certifier/reference_host.py"  # -> 31/31
python3 certifier/certify.py --suite vectors/network-suite.v1.json --fixtures vectors/network-fixtures.v1.json --lock vectors/network.lock.v1.json --contract-lock contracts/ABI.lock.v1.json  # -> 14/14
python3 certifier/certify.py --suite vectors/appointed-suite.v1.json --fixtures vectors/appointed-fixtures.v1.json --lock vectors/appointed.lock.v1.json --contract-lock contracts/ABI.lock.v1.json --artifacts-dir artifacts  # -> 8/8

cd ../pro
./scripts/certify.sh                                  # -> 31/31 + 10/10 + 14/14 + 8/8 + 6 unit

cd ../infra
uv run --project ../core pytest -o addopts="" -p no:cacheprovider tests   # -> green

cd ..
./scripts/check-pyright.sh                            # -> 0 errors, 0 warnings, 0 notes
```

## 13. Ground rules

Obey the hard laws: whole-file replacement only via `tools/safe-replace.sh`
(**never in-place edits**); resolve every home path from `$HOME`; commit and push
continuously; **never `--no-verify`**. Never act above the active level (L1) or
cross an operator gate. Keep the public/private boundary: no hostname, address,
key, tenant name, private origin, or scenario content in a public repo. Every
change must be **correct, robust, secure, and idempotent**.
