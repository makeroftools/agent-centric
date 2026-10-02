# L3 milestone plan — L3-ready validator + honest rehearsal

> **Status: implemented (agent-side, offline, under L1); operator gates remain.** This page is the mission-critical
> handoff for the implementing session. It was produced in a roadmap-planning
> session at the workspace root `cbp/` and is authoritative for **this milestone
> only**. Read [`../../HANDOFF.md`](../../HANDOFF.md), [`../../AGENTS.md`](../../AGENTS.md),
> and [`../../PRINCIPLES.md`](../../PRINCIPLES.md) first; if this page disagrees
> with `PRINCIPLES.md`, the laws win. Operator gates are unchanged and must not be
> crossed: real host provisioning/enrollment, any signing-key act, any public
> publish, and any edit to [`../../.agentfactory.toml`](../../.agentfactory.toml).

## 0. Implementation status (this session)

Workstreams **A–D are implemented offline under L1**:

- **A — validator upgrade.** The logic is extracted to the pure library
  [`../../src/agent_centric/cbp/holdout.py`](../../src/agent_centric/cbp/holdout.py);
  [`../../tools/holdout-validate.py`](../../tools/holdout-validate.py) is a thin
  CLI. The additive **`service`** probe boots a signed composition through
  `component_boot.boot_from_lock` via an operator-private content-addressed
  **`holdout.deployment/v1`** descriptor and compares the observable's
  canonical-JSON projection (or asserts a fail-closed refusal). `holdout.lock`
  pins `probe_contract_sha256` and `validator_sha256`; the record adds `runtime`,
  `platform`, `lock_sha256`, `suite_sha256`, and `isolation`. Exit semantics are
  `0` green / `1` red / `2` usage / `3` refused (SPEC-0019 reconciled). A synthetic
  `--self-test` proves the known-good/known-bad separation and ledger idempotency.
  The advisory principal guard refuses `uid 0` / the `docker` group, with a
  `--rehearsal` escape that **records** `isolation: rehearsal` (never
  authoritative). A component-ready entry/contract is exposed; it is deliberately
  **not** placed inside the composition under test.
- **B — isolation runbook.** Authored in the private companion
  (`infra/holdout/runbook.md`); the operator executes the group/ACL/root setup.
- **C — scenario templates.** Format/templates only (no scenario content) in the
  private companion (`infra/holdout/templates/`).
- **D — hermetic acceptance + continuity.** Core tests
  (`tests/test_holdout_service.py`) prove a deterministic green record on the
  test-signed composition, the negative tamper cases, the principal guard, and
  `--self-test`; the private companion adds a rehearsal test over its own
  composition. This page and `HANDOFF.md` record the §3 invariants.

The flip, scenario authoring, the authoritative validator run, key acts,
provisioning, and any publish remain **operator gates** (see §10).

## 1. Goal

Make a **genuine L3 (dark-factory) flip legitimately possible**, and make the L3
machinery **component-ready** so it can later graduate into the component
ontology. The authoritative flip itself is **out of scope** here: it is an
operator act, performed on a host/principal distinct from the author, after the
validator runs green.

The governing invariant is *verification, not generation, is the constraint on
autonomy*. Today the holdout validator ([`../../tools/holdout-validate.py`](../../tools/holdout-validate.py),
SPEC-0019) is **narrower than its spec**: it only runs a `task` executable and
checks its exit code. It does not boot the signed lock under test, verify
signatures, probe a `network.v1`/`service.v1` observable, pin its own TCB, or
enforce principal/host separation. A flip on that basis would be verification
theatre. This milestone closes the gap to a **minimum credible green**.

## 2. Verified starting facts (re-verify before acting)

- All four repos (`core`, `../conformance`, `../pro`, `../infra`) are on `main`,
  clean, pushed. Core suite **1655 passed**; infra **98 passed, 1 skipped**;
  shared **31/31** (in-process + external + Rust); WASM **10/10**; network
  **14/14**; appointed **8/8**; basedpyright **0/0/0**.
- Delivered: Layers 0–4 + signing, distribution spine, offline umbrella lock
  (test-signed) + operator-signed system lock (recorded, **not wired**), local CD
  substrate, SPEC-0018 Phase 2, provisioning spec P0–P9, and — this session —
  `review.v1` + the persistent Review queue + confidence on every driver response,
  and the offline self-hosting smoke.
- **The validator gap (facts):** only probe kind `task` (`SUPPORTED_PROBE_KINDS`);
  no signed-lock boot, no `network.v1`/`service.v1` observable; no runtime/TCB pin
  in the record; self-test exists only as a pytest class; exit code is `3` for
  refused while SPEC-0019 says `2`, and a **red verdict exits `0`**.
- **Isolation gap (facts):** the authoring agent runs as a `docker`+`sudo`-capable
  principal; `agent-runner` (uid 1001) exists but cannot traverse the workspace
  (`$HOME` is `750`, not in a shared group); `$CBP_HOLDOUT_ROOT` does not exist.
  Nothing in code checks principal/host separation.

## 3. Invariants (recorded this planning session)

1. **Everything in the tree is a component** — one node ontology, no privileged
   node type. The **harness/runtime is not a node**; the **bootstrap substrate
   graduates**, it is not a permanent exemption.
2. **Component-ready now, graduated later.** Tooling is wrapped/graduated in this
   order: **lock compiler → validator/holdout → signing service → provisioning
   tasks** (SPEC-0015: wrap → content-address → sandbox → conformance).
3. **"The compiler builds the compiler."** A post-L3 milestone: the harness boots
   a *tool composition* that produces/validates the next composition
   byte-identically. It **does not gate L3**. Build the L3 validator so it can
   graduate without a rewrite (a pure core library **plus** a `component.v1`-ready
   entry/contract); the authoritative validator lives **outside** the composition
   it validates (bootstrap circularity).

## 4. Workstream A — Validator upgrade (`core`)

A1. **Extract logic** into an importable `src/agent_centric/cbp/holdout.py`; make
    `tools/holdout-validate.py` a thin CLI. Keep the convention guard's assertions
    (tool exists; `specs/holdout/` stays placeholder-only).
A2. **Additive `service` probe kind** in `holdout.v1`: boot the scenario's
    **deployment descriptor** through the core harness
    ([`../../src/agent_centric/cbp/component_boot.py`](../../src/agent_centric/cbp/component_boot.py)),
    run one observable, compare its **canonical-JSON projection**. `task` parsing
    is unchanged; the new kind is optional; `holdout.v1` is extended additively
    (reserve `holdout.v2` for breaking changes) and its hash is pinned.
A3. **TCB pins (no floating toolchain):** add `probe_contract_sha256` and
    `validator_sha256` to `holdout.lock`; add `runtime` / `platform` plus
    `lock_sha256` / `suite_sha256` to `holdout.record`. Keep the record
    **wall-clock-free** so its head stays idempotent.
A4. **Exit semantics:** `0` green, `1` red, `2` usage, `3` refused; reconcile
    SPEC-0019's text. A red verdict must be non-zero at the boundary.
A5. **`--self-test` mode:** build a synthetic owner-only (`0700`) suite with a
    known-good and a known-bad scenario, assert green/red and ledger idempotency;
    exit `0` only when the validator behaves correctly. Keep the pytest guard.
A6. **Principal guard (defence-in-depth):** refuse if `geteuid()==0` or the process
    is in the `docker` group. Document honestly that host/principal isolation is
    **environment-enforced**, not provable from inside the process.
A7. **`holdout.deployment/v1` descriptor** (operator-private, content-addressed;
    the scenario references it): fields `schema`, `repo_root`, `composition_dir`,
    `lock_hash`, `trust` (a `trust/v1` store path or inline public key),
    `entry_allowlist`, and the observable. The core validator never hard-codes a
    private path or slug; a missing/mismatched descriptor refuses **before**
    running.
A8. **Component-ready boundary:** a pure entry/contract for a future
    `component.v1` wrapper; no placement inside the composition under test.

## 5. Workstream B — Rehearsal isolation (agent authors runbook; operator executes)

B1. **Runbook** (private companion): a dedicated group + ACLs so `agent-runner`
    operates the workspace with **no `sudo`/`docker`**; create the operator-private
    holdout root (`$CBP_HOLDOUT_ROOT`, default `$HOME/.cbp/holdout`) owned by the
    operator at `0700`; descriptor + scenario templates; self-test; the run
    command; and the flip checklist.
B2. **Operator executes** the group/ACL/holdout-root setup and runs the authoring
    agent as `agent-runner`. A **separate operator-owned host** is the precondition
    for the authoritative (green) validator run.

## 6. Workstream C — Scenario suite (operator-authored, author-blind)

C1. v1 suite (~5 scenarios): **[high]** signed composition boots and the shell-plan
    projection matches; **[high]** replay determinism (same lock ⇒ byte-identical
    projection); **[high]** a tampered lock/entry signature is refused (negative);
    **[normal]** pull/apply invoke projection; **[normal]** a host-bound node
    invocation refuses fail-closed. The agent supplies **format/templates only** —
    never scenario content.

## 7. Workstream D — Hermetic acceptance + continuity

D1. `core`/`infra` tests proving the upgraded validator on the **test-signed**
    composition yields a deterministic green `holdout.record`, plus the negative
    cases, the principal guard, and `--self-test`.
D2. Author the flip-ceremony checklist; refresh [`../../HANDOFF.md`](../../HANDOFF.md)
    and the private companion handoff; record the §3 invariants.

## 8. Acceptance (agent-side "done")

Core suite green including the new holdout tests; `--self-test` green; principal
guard test; a deterministic green record on the test-signed composition; runbook +
templates committed (private); public boundary guard clean; all four repos on
`main`, clean, pushed. **The authoritative green and the `.agentfactory.toml` flip
remain operator-side.**

## 9. Explicit exclusions

The flip itself; SPEC-0018 Phases 3–6; the SPEC-0002 `Agent`→`Component`
adapter / module-level-registry removal (adapter-first, and only the **fallback**
if isolation cannot be established); threshold signing; SPEC-0017 frontier;
tooling **graduation** (only the component-*ready* boundaries are in scope).

## 10. Operator gates and critical path (front-loaded)

1. Create the distinct unprivileged authoring principal + workspace access
   (group/ACLs) and the `0700` operator-private holdout root.
2. Provide the separate operator-owned host for the authoritative validator run.
3. Install the operator public root and **re-sign** the composition/umbrella locks
   at the deployment boundary (never commit operator-signed artifacts or keys to a
   public repo).
4. Author the scenarios; run the self-test; run the validator green; append the
   record; then the deliberate `[levels.L3]` edit.

## 11. Residual risks (stated, not hidden)

Frozen clock/seed/no-network determinism hardening is deferred; host/principal
isolation is environment-enforced (only advisory in-process); the authoritative
green is operator-side; the validator exit-code/spec drift is corrected here.

## 12. Verification block for the implementing session

```sh
cd core
uv run ruff check .                                   # -> clean
uv run mypy src                                       # -> clean
uv run agent-centric cbp-check                        # -> READY (8/8)
uv run pytest -o addopts="" -p no:cacheprovider       # -> green (incl. new holdout tests)

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
key, tenant name, private origin, or scenario content in a public repo.
