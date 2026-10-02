# HANDOFF — Agent-centric (mission-critical system)

> **Read first:** [`AGENTS.md`](AGENTS.md) is the always-loaded table of
> contents; [`PRINCIPLES.md`](PRINCIPLES.md) is the constitution. This file is
> the **current** session-continuity one-pager: it points, it does not restate.

**Prepared for a new model session. Every claim below was verified with the
commands in §"Verify everything" — that block is authoritative; commit hashes in
prose drift as work continues.** The workspace root is `cbp/` (a plain directory, **not** a
git repo); the work lives in four sibling repos:

| repo | visibility | role |
| --- | --- | --- |
| [`core/`](AGENTS.md) | public | Python **reflection** + the frozen specs (`specs/SPEC-0011`–`0019`). |
| [`../conformance/`](../conformance/AGENTS.md) | public | The shared contract: WIT ABI + conformance vectors + certifier. |
| [`../pro/`](../pro/AGENTS.md) | private | The Rust host (optimization edition). |
| [`../infra/`](../infra/HANDOFF.md) | private | SPEC-0018 instantiation: service-host provisioning + CD. |

Delivered so far, of the full-version plan (`SPEC-0011`): **Layers 0, 1a, 1b,
1c, signing, and Layer 2** (the frozen `component-abi.v1` and v1 conformance
vectors; a **Rust host certified cross-runtime-identical** to the Python
reference host — shared suite **31/31**; **content-addressed, signed WASM
execution** — WASM suite **10/10**, including a full **`component-abi.v1` WASM
guest** that announces its channels/tasks and exchanges Information Packets over
the host-mediated transport, with locked-down refusal of unsigned/bad-signature
artifacts; **`network.v1`** — static + deterministic-planner, pinned before it
runs, typed at instantiation, replayable — network suite **14/14** on both
hosts), plus **Layer 3** (a deterministic, content-addressed read-only network
diagram) and **Layer 4** (**`appointed.v1`** — allowlisted appointed sources,
admitted only through a host-configured source allowlist and a detached Ed25519
signature, contained with zero capabilities (A0), recorded as a scoped Assurance
Label — appointed suite **8/8** on both hosts). The distribution spine is also
advanced: the **`design → pin → components.lock` producer** (`cbp/lockfile.py`,
SPEC-0008 Phase 3), **signing-key rotation** with overlapping trust windows
(`cbp/trust.py`, SPEC-0007 §6), and the **n8n authoring adapter**
(`cbp/n8n_adapter.py`, SPEC-0008 Phase 2, lossless `design.v1` ⇄ n8n round-trip)
are delivered, as is the **recorded, confidence-scored `model` component**
boundary (`cbp/model_record.py`, SPEC-0009 slice 7) and **design envelope
limits** (SPEC-0008 §4, validated and pinned), the **offline umbrella
`components.lock`** generator (test-signed offline; operator signing remains),
and the **SPEC-0011 "viable" end-to-end** — boot a signed lock → typed-port
dataflow → durable-ledger replay, with a recorded, confidence-scored `model`
node (`cbp/boot_network.py`).

**SPEC-0002 Phase-1 is delivered** (adapter-first, under L1): one `Component`
contract with the `Agent` adapted onto it; a parent-provisioned
`ComponentCatalog` that **removed the module-level registry global** and the
central `_child_class_for` map (parent-declared kinds; an undeclared kind fails
closed); and explicit `inproc`-only backend enforcement. Design + progress:
[`docs/agent/spec-0002-phase1-adapter-design.md`](docs/agent/spec-0002-phase1-adapter-design.md).

**Infrastructure (private `infra/`).** SPEC-0018 is authored end-to-end below
the operator gates: Phase 1 (pinned NixOS host definition + `service.v1` + named
tasks + pull/apply agent), Phase 2 (the bootstrap services **graduated to
`component.v1`**, pinned in a signed content-addressed composition, and bound by
`service.v1.refs.composition`; the agent verifies the lock + graph before apply),
and the **provisioning specification** (`infra/specs/provisioning.md`, stages
P0–P9) whose tasks are now authored (`verify-image`, `install-host` plan/gated,
`authorize-host`). **Nothing is provisioned**; the real host, the operator signing
key, and any publish remain gates.

The **L3 isolated holdout validator (SPEC-0019)** is now **L3-ready**: the logic
is a pure importable library (`cbp/holdout.py`) behind a thin, separate-process
CLI. Beyond the original `task` probe it has the additive **`service`** probe
(boot a signed composition through the core harness via an operator-private,
content-addressed `holdout.deployment/v1` descriptor; compare the observable's
canonical-JSON projection, or assert a fail-closed refusal), **TCB pins** in the
lock (`probe_contract_sha256`, `validator_sha256`) and record (`runtime`,
`platform`, `lock_sha256`, `suite_sha256`, `isolation`), exit codes `0/1/2/3`, a
synthetic known-good/known-bad `--self-test`, and an **advisory principal guard**
(uid 0 / docker group) with a `--rehearsal` escape that records
`isolation: rehearsal` (offline-test trust is rehearsal-only). A component-ready
entry/contract is exposed, never placed inside the composition under test.
Operator-authored scenarios, the authoritative `isolation: enforced` green run on
a distinct principal/host, and the L3 flip remain operator gates.

## Latest session (UI ABI v1 — delivered under L2)

The UI ABI is authored and content-addressed, and its core manifest is
implemented and tested:

- **`conformance/contracts/ui-v1.wit`** — the target-agnostic **UI world**
  (`cbp:ui@1.0.0`): a UI component exports `describe`/`render`/`handle` and
  imports the host boundary (`get-projection`/`submit-directive`/`log`); no
  execution capability, no ambient authority. Resolved with the pinned
  `wasm-tools` **1.260.0**.
- **`conformance/contracts/UI-ABI.md`** — the normative companion (targets,
  projection/directive boundary, mandatory provenance-tiered sandboxing,
  verify-before-execute, assembly/pinning); **`ui.lock.v1.json`** pins
  `source_sha256`, `ui_md_sha256`, and `resolved_sha256`.
- **`core/src/agent_centric/contracts/ui.py`** — the pure, canonical,
  content-hashable `ui.v1` manifest (`UIComponent`, `UITarget`
  web/cli/os/embedded, `DirectiveBinding`, `validate_ui` with strict ABI
  negotiation, and default-deny `assert_projection_granted` /
  `assert_directive_declared`), with **20 tests**. `component-abi.v1` stays
  frozen; no new node kind.

Core suite **1906 → 1926 passed** (+20); `mypy` 138 files; `ruff`/`cbp-check`
green. No operator gate was touched.

## Latest session (front-end spec + local substrate — under L2)

- **SPEC-0022 drafted**
  ([`specs/SPEC-0022-frontend-and-components.md`](specs/SPEC-0022-frontend-and-components.md)):
  component-provided UI — a target-agnostic `ui.v1` contract with pluggable UI
  targets (web/cli/os/embedded), pinned UI refs on behavior components, dynamic
  assembly pinned before render, **mandatory provenance-tiered sandboxing**
  (security over dynamicism), verify-before-execute, pure projections, and
  directives through the verified spine. It formalizes the existing `cbp/web.py`
  surface as the `web` v0 target; no implementation yet.
- **Local CD substrate is up on this host** (private companion): loopback Gitea +
  OpenBao, mirror seeded (`gitea=200`, `openbao=200`), for front-end development.
  No host provisioned; no operator gate crossed.

## Latest session (threshold signing verification — delivered under L1)

The roadmap plan's below-gate candidate #2 (Workstream D) is **implemented, tested,
and pushed**
([`src/agent_centric/cbp/threshold.py`](src/agent_centric/cbp/threshold.py),
SPEC-0007 §6, 25 tests):

- **`ThresholdPolicy`** is a pure, content-addressed ``(k, key_ids)`` declaration;
  **`ThresholdSignature`** is a canonical, base64, content-addressed k-of-n bundle
  (schema ``threshold.sig/v1``) sorted by key id, with no repeated key.
- **`ThresholdVerifier`** implements the existing `SignatureVerifier` protocol and
  **drops into the resolver and `boot_from_lock` unchanged**: only the signature
  bytes differ. It refuses a malformed/non-canonical bundle, an ineligible or
  unknown key, **any** part that fails to verify, and a bundle below threshold —
  fail-closed. `ThresholdError` subclasses `SignatureError`, so boot still refuses
  through its existing guard (a below-threshold lock is proven not to boot).
- **`sign_threshold`** composes independent `Signer`s; **no new cryptography** (the
  same system-tool signers are reused). `component-abi.v1` untouched.
- Operator key acts (re-signing real locks, installing the trust root) remain
  **gated**; this session delivers the verification logic offline only.

Core suite **1881 → 1906 passed** (+25); `mypy` 137 files; `ruff`/`cbp-check`
green; basedpyright 0/0/0. **No operator gate was touched.**

## Latest session (local-first provider adapter — delivered under L1)

The roadmap plan's first below-gate unit (Workstream A) is **implemented, tested,
and pushed**
([`src/agent_centric/cbp/provider_local.py`](src/agent_centric/cbp/provider_local.py),
SPEC-0020 §1/§2, 17 tests):

- **`LocalTaskProviderAdapter`** implements the injected `ProviderAdapter` seam by
  mapping each typed provider call (`observe`/`apply`/`health`/`teardown`) to a
  **named, content-pinned local task** run through an injected `LocalTaskRunner`.
  It is offline and network-free.
- **Verify-then-apply:** a mutating script task is re-hashed on disk against its
  `TaskBinding.digest` and refused on drift, unreadability, or a missing digest
  (and a mutating **callable** is refused as unverifiable) **before any side
  effect**.
- **Additive observe role:** `ProviderTask.OBSERVE` is added additively (optional,
  read-only, not required by `validate_provider`); the adapter parses the
  `observe` task's **canonical-JSON** output, refusing malformed/non-canonical
  payloads. `component-abi.v1` stays frozen; legacy `task.v1` untouched.
- The full reconcile loop is exercised over the real runtime (fresh convergence,
  already-converged no-op, idempotent evidence, failed-health rollback then
  escalation). The **concrete** provider manifest/task wiring stays in the private
  companion; a network/cloud adapter remains operator-gated.

Core suite **1864 → 1881 passed** (+17); `mypy` 136 files; `ruff`/`cbp-check`
green; basedpyright 0/0/0. **No operator gate was touched.**

## Latest session (roadmap planning — delivered under L1)

A roadmap-planning session was held at the workspace root, as the prior handoffs'
*Next* required (plan before implementing). It re-verified the full state (core
**1864 passed**; shared **31/31** in-process + external + Rust, WASM **10/10**,
network **14/14**, appointed **8/8**; infra **109 passed, 1 skipped**;
basedpyright **0/0/0**) and fixed the plan of record for the next cycle in
[`docs/agent/roadmap-plan.md`](docs/agent/roadmap-plan.md). **No code path
changed; no operator gate was touched.** Decision: the sole remaining unit
buildable below the gates is a concrete **local-first `provider.v1` adapter**,
behind the existing injected `ProviderAdapter`/`TaskRunner` seams — the generic
mechanism in core (additive, offline-tested), the concrete manifest/task wiring in
the private companion. Everything else (the L3 flip, SPEC-0018 Phases 3–6,
threshold-signing key acts, self-hosting apply, any publish, and the research
charters) is sequenced and operator-gated.

## Latest session (work boot bridge — delivered under L1)

The dynamic task-load binder is now **wired through the boot/bundle machinery**
([`src/agent_centric/cbp/work_boot.py`](src/agent_centric/cbp/work_boot.py),
SPEC-0021 §5, 8 tests):

- `bundle_task_loader` resolves a driver-dispatched entry from a **verified
  component bundle**: the manifest must parse, its name must equal the task, and
  its entry must be allowlisted (in-process) or, only when explicitly permitted,
  run process-isolated from the bundle.
- `bind_work_tasks` binds a batch of pinned `work.v1` loads (admit → pinned →
  content-addressed → optional signature → bundle checks) into a driver.
- `run_bound_work_network` executes a `network.v1` over the bound tasks, refusing
  **before** anything runs if any non-child component maps to an unbound task.

Core suite **1856 → 1864 passed** (+8); `mypy` 135 files; `ruff`/`cbp-check`
green; basedpyright 0/0/0. **No operator gate was touched.** The only remaining
SPEC-0020/0021 buildable unit below the gates is a concrete provider adapter;
that, and the rest of the roadmap, is operator-gated.

## Latest session (SPEC-0020/0021 runtimes — delivered under L1)

The runtime realization of the two contracts is **implemented, tested, and
pushed** (one L1-reviewed commit), offline and fail-closed:

- **Provider runtime** ([`src/agent_centric/cbp/provider_runtime.py`](src/agent_centric/cbp/provider_runtime.py),
  SPEC-0020 §2/§3). The pure contract decides the plan; an injected
  `ProviderAdapter` confines all provider I/O behind one typed `call` seam.
  Every call is recorded in an append-only, advisory-locked, `fsync`-ed,
  **idempotent** `ProviderLedger` (a retry never duplicates evidence). `apply`
  is verify-then-apply (`assert_plan_appliable`); `reconcile` drives
  plan → verify → apply → health, makes **no** mutating call on already-converged
  healthy state, **auto-rolls-back** to the last healthy desired state on a
  failed health gate (re-planned from the current observed state so drift is
  removed), and **escalates and stops** after bounded retries. Notifications are
  emit-only: a `NotificationSink` receives transition events validated against
  the bound `NotificationBinding`. A deterministic `InMemoryProviderAdapter` is
  the offline reference double; a real network adapter remains operator-gated.
- **Work runtime** ([`src/agent_centric/cbp/work_runtime.py`](src/agent_centric/cbp/work_runtime.py),
  SPEC-0021). `materialize_work` stores a compiled artifact in the
  content-addressed cache (its digest *is* the artifact hash; idempotent);
  `WorkBinder` implements the fail-closed dynamic task-load/bind protocol —
  admit (declared + pinned) → verify-on-read the artifact → optional signature
  verification → **only then** resolve the entry and register. A refusal leaves
  no partial side effect.

Core suite **1823 → 1856 passed** (+33); `mypy` 134 files; `ruff`/`cbp-check`
green; basedpyright 0/0/0. **No operator gate was touched.** Next buildable unit
below the gates: wire the binder through boot/network and provide a concrete,
operator-gated provider adapter; then the operator-gated roadmap.

## Latest session (SPEC-0020/0021 additive contracts — delivered under L1)

The two operator-ratified contracts are **implemented, tested, and pushed**,
additively, each as its own L1-reviewed commit; `component-abi.v1` stays frozen
and the legacy `task.v1` is untouched:

- **`provider.v1`** ([`src/agent_centric/contracts/provider.py`](src/agent_centric/contracts/provider.py),
  SPEC-0020, 61 tests). A thin provider manifest (local-first/cloud target,
  capabilities, named `plan`/`apply`/`health`/`teardown` tasks, secret **refs
  only**, grants) over the `service.v1` task discipline. Pure `plan_provider`
  (content-addressed, byte-deterministic, no I/O); verify-then-apply gates
  (`assert_plan_appliable`: drift / unpinned / unapproved destructive-or-cloud
  refused; `assert_task_appliable`: idempotent or gated, mutating pinned); a
  deterministic `reconcile_action` (`converged`/`apply`/`rollback`/`escalate`);
  an **emit-only** `NotificationBinding` (only `notify.emit`, never mutating);
  and `validate_provider` (cloud requires the explicit `provider.cloud` grant).
- **`work.v1`** ([`src/agent_centric/contracts/work.py`](src/agent_centric/contracts/work.py),
  SPEC-0021, 49 tests). A declarative, content-addressed recipe (obtain a pinned
  source **or** generate from a pinned `model` generator; build; verifier;
  envelope; grants) that `compile_work` maps **purely** onto a `component.v1`
  (identical recipe ⇒ byte-identical artifact). `assert_work_executable`
  refuses generated work until it passes its verifier/conformance; the dynamic
  task-load protocol (`TaskAnnouncement`/`TaskLoad`/`admit_task_load`) is
  explicit and fail-closed before any bind, with the task identity its **signed
  content hash**, never its ports.

Core suite **1713 → 1823 passed**; `mypy` 132 files; `ruff`/`cbp-check` green;
basedpyright 0/0/0. **No operator gate was touched.** The runtime realization
(provider adapter + reconcile/health/rollback loop, the notification component,
and the `work.v1` compiler + dynamic task-load binder) is the next buildable
unit below the gates.

## Latest session (spec authoring — Service Hosting + Components; charters)

Four new artifacts were authored (all `draft`; no implementation, no operator gate
touched):

- **SPEC-0020** — service hosting: a thin `provider.v1` (pure `plan`; gated,
  idempotent `apply`/`health`/`teardown`), verify-then-apply, a reconcile loop
  with deterministic auto-rollback and fail-closed escalation, an emit-only
  notification component, declarative local-first/cloud selection, and authN/Z at
  the provider boundary. Extends SPEC-0018.
- **SPEC-0021** — components as dynamic work: a declarative, pinned **`work.v1`**
  recipe (obtain | create/generate; build; tests/verifier; provenance/grant;
  envelope) that compiles via a pure seam to `component.v1`; the explicit dynamic
  task-load/bind protocol (SPEC-0002 open item #2); generation gated by
  SPEC-0013/0015. `component-abi.v1` unchanged; legacy `task.v1` untouched.
- **`docs/agent/brain-charter.md`** — the ontological knowledge base: study
  charter, no design freeze; home SPEC-0010 + SPEC-0017 WS1.
- **`docs/agent/learning-charter.md`** — RL / active inference / bounded RSI:
  study charter, post-L3; home SPEC-0010 + SPEC-0017 WS5. Learning is a recorded,
  confidence-scored component that proposes while the verifier disposes; bounds
  cannot self-widen.

No code path changed; no operator gate was touched. The two specs were
operator-ratified to `accepted` (still unimplemented).

## Latest session (documentation/spec hygiene — delivered under L1)

The documentation/spec drift recorded in the previous handoff is closed:
`conformance/` and `pro/` now have real top-level READMEs
(`conformance/README.md`, `conformance/contracts/README.md`, `pro/README.md`) in
place of the byte-identical vectors-README copies; `pro/AGENTS.md` is rewritten
for the Rust host (it had been a verbatim copy of `conformance/AGENTS.md`); the
`conformance/`/`pro/` "Next" lines are refreshed and their duplicated
`PRINCIPLES.md` cross-links retargeted to `../core/`; SPEC-0008/0009 are
`implemented`, SPEC-0002's §12 boxes are reconciled (the author-blind holdout
half stays operator-gated, so SPEC-0002 stays `accepted`), and
SPEC-0003/0004/0006/0011 boxes are ticked; and
[`docs/agent/component-adapter.md`](docs/agent/component-adapter.md) records the
adapter/catalog and the removed module-level global. No code path changed; the
suite stayed green, basedpyright 0/0/0. No operator gate was touched. A
fail-closed **documentation-freshness guard**
([`tests/test_docs_freshness.py`](tests/test_docs_freshness.py)) was then added
(§"Documentation freshness").

## Latest session (SPEC-0002 Phase-1 remainder — delivered under L1)

The remaining Phase-1 items of SPEC-0002 are **implemented, equivalence-tested,
and pushed**, adapter-first; each step is an independently committed, green
unit (design record and progress log:
[`docs/agent/spec-0002-phase1-adapter-design.md`](docs/agent/spec-0002-phase1-adapter-design.md)
§12):

- **S1 — adapter (additive).** [`src/agent_centric/cbp/component_adapter.py`](src/agent_centric/cbp/component_adapter.py):
  `AgentComponent` (lifecycle passthrough, entry bridge `run_once`, pure
  `component.v1` manifest projection) with equivalence tests against the
  `CbpDriver` round-trip; no existing path changed.
- **S2 — catalog seam (additive).** [`src/agent_centric/cbp/component_catalog.py`](src/agent_centric/cbp/component_catalog.py)
  (`ComponentCatalog`, passive) + `RegistryEntry.from_callable` +
  `AgentConfig.catalog`; a child resolves only what its parent provisioned.
- **S3a/S3b — runtime cut-over + global removal.** `CbpDriver` owns the catalog
  and provisions it to the root; children inherit it; `replay_session` provisions
  the replay tree's catalog; `replay_ledger` seeds the fresh driver's catalog
  from the ledger manifest. The module-level `_REGISTRY`/`_resolve_entry`/
  `register_callable` shim is **deleted**, the package no longer exports it, and
  a guard test asserts no global remains.
- **S4 — parent-declared children.** The central `_child_class_for` map is
  **deleted**; `Agent.declare_child_kind` is provisioned by the tree builder
  (`CbpDriver` declares `store`/`bills`/`model`); an undeclared kind fails closed
  **before any socket bind** (no partial side effect).
- **S5 — inproc-only backend.** `ADMITTED_BACKENDS = {"inproc"}`;
  `CbpDriver(backend=…)` refuses anything else (`BackendNotAdmitted`).

**SPEC-0002 Phase-1 items 1, 2, and 6 are complete.** Core suite **1680 → 1708
passed**; mypy 130 files; basedpyright 0/0/0. No operator gate was touched (L1,
no `.agentfactory.toml` edit, no key, no provisioning, no publish).

## Latest session (L3 milestone — implemented offline, under L1)

The agreed *L3-ready validator + honest rehearsal* plan
([`docs/agent/l3-milestone-plan.md`](docs/agent/l3-milestone-plan.md)) is
implemented agent-side, workstream by workstream:

- **A — validator upgrade.** Logic extracted to
  [`src/agent_centric/cbp/holdout.py`](src/agent_centric/cbp/holdout.py);
  [`tools/holdout-validate.py`](tools/holdout-validate.py) is a thin CLI. Additive
  `service` probe + `holdout.deployment/v1` descriptor; TCB pins; `0/1/2/3` exit
  semantics (SPEC-0019 reconciled); `--self-test`; advisory principal guard with a
  rehearsal record; component-ready boundary.
- **B — isolation runbook.** Private companion
  [`holdout/runbook.md`](../infra/holdout/runbook.md) (operator-executed).
- **C — scenario templates.** Format/templates only in
  [`holdout/templates/`](../infra/holdout/templates/README.md).
- **D — hermetic acceptance + continuity.** Core tests
  ([`tests/test_holdout_validator.py`](tests/test_holdout_validator.py) and
  [`tests/test_holdout_service.py`](tests/test_holdout_service.py))
  prove the deterministic green record on the test-signed umbrella, the tamper
  negatives, the principal guard, and `--self-test`; the private companion adds a
  rehearsal test over its composition. Core suite **1680 passed**; infra **104
  passed, 1 skipped**. Hardened after first write: descriptor paths are confined
  to the deployments root (traversal/symlink escapes refuse), the ledger append is
  advisory-locked + `fsync`-ed (idempotent under concurrency), and malformed
  component-entry payloads refuse cleanly. The content-addressed cache now
  fsyncs the parent directory after its atomic replace (durability parity with
  component-state materialization).

**The flip, scenario authoring, the authoritative validator run, key acts,
provisioning, and any publish remain operator gates.** The governing invariants
are recorded below.

## Latest session (Horizon 1 — delivered offline, under L1)

- **`review.v1` + confidence (SPEC-0002 §4/§6).** A pure, content-addressed
  review contract plus a persistent, append-only, idempotent **Review queue**;
  every driver response carries a deterministic confidence (`1.0` for a
  verified deterministic output, the model score for a model output, else
  `0.0`); a sub-threshold run outcome enters the queue at one boundary
  (`contracts/review.py`, `cbp/review.py`, `tests/test_cbp_review.py`, 37
  tests). This closes the `review.v1`/confidence gap and the SPEC-0015
  promotion gate's queue.
- **Self-hosting (offline slice).** The private `infra/` companion boots the
  signed bootstrap composition **through the core harness** (`boot_from_lock`)
  and runs its deterministic local nodes — the shell entry in-process, the
  pull/apply node process-isolated from its verified bundle. Host-bound
  services are verified structurally and **refuse when invoked** (fail-closed,
  their pinned host tasks are absent offline). Nothing is provisioned; no key
  is touched.
- **Next milestone (subsequently implemented).** The *L3-ready validator +
  honest rehearsal* plan
  ([`docs/agent/l3-milestone-plan.md`](docs/agent/l3-milestone-plan.md)) was
  implemented in the next session — see *Latest session* above; operator gates
  unchanged.

## L3 invariants (recorded)

1. **Everything in the tree is a component** — one node ontology, no privileged
   node type. The **harness/runtime is not a node**; the **bootstrap substrate
   graduates**, it is not a permanent exemption.
2. **Component-ready now, graduated later.** Tooling is wrapped/graduated in this
   order: **lock compiler → validator/holdout → signing service → provisioning
   tasks** (SPEC-0015: wrap → content-address → sandbox → conformance).
3. **"The compiler builds the compiler."** A post-L3 milestone: the harness boots
   a *tool composition* that produces/validates the next composition
   byte-identically. It **does not gate L3**. The validator is a pure core library
   plus a `component.v1`-ready entry/contract; the **authoritative validator lives
   outside the composition it validates** (bootstrap circularity).

## Quality bar (every change)

Every change to this mission-critical system must be:

- **Correct** — deterministic, fail-closed, no unverified success; proven by a
  test (Law 12).
- **Robust** — no partial success; explicit refusal on drift; atomic writes and
  `fsync` where durability matters.
- **Secure** — deny by default; content-address and verify before use; never leak
  keys, scenarios, or private inventory into a public repo; confine untrusted
  paths.
- **Idempotent** — a retry never duplicates evidence (boot, ledger, transparency
  log); the same inputs produce byte-identical outputs.

The validator is TCB: pin, sign, content-address, and record it.

## Verify everything (do this before acting)

```sh
# core (Python reflection) ------------------------------------------------
cd core
uv sync --extra dev                                   # one-time
uv run ruff check .                                   # -> clean
uv run mypy src                                       # -> 138 files, clean
uv run agent-centric cbp-check                        # -> READY (8/8)
uv run pytest -o addopts="" -p no:cacheprovider       # -> 1926 passed

# conformance (shared contract) -------------------------------------------
cd ../conformance
python3 certifier/certify.py                          # Python reference: 31/31
python3 certifier/certify.py --host-cmd "python3 certifier/reference_host.py"  # ext protocol: 31/31
python3 certifier/certify.py \
  --suite vectors/network-suite.v1.json \
  --fixtures vectors/network-fixtures.v1.json \
  --lock vectors/network.lock.v1.json \
  --contract-lock contracts/ABI.lock.v1.json          # network.v1: 14/14
python3 certifier/certify.py \
  --suite vectors/appointed-suite.v1.json \
  --fixtures vectors/appointed-fixtures.v1.json \
  --lock vectors/appointed.lock.v1.json \
  --contract-lock contracts/ABI.lock.v1.json \
  --artifacts-dir artifacts                           # appointed.v1: 8/8
#   (the WASM suite is certified by the Rust host, below)

# pro (Rust host) ---------------------------------------------------------
cd ../pro
./scripts/certify.sh                                  # builds + certifies ALL FOUR suites (31/31 + 10/10 + 14/14 + 8/8) + unit tests
cargo test --release --features wasm                  # codec + artifact-signature + appointed tests: 6 passed

# infra (private companion) -----------------------------------------------
cd ../infra
uv run --project ../core pytest -o addopts="" -p no:cacheprovider tests   # -> 118 passed, 2 skipped

# workspace IDE hygiene (run from the workspace root) ----------------------
cd ..
./scripts/check-pyright.sh                            # -> 0 errors, 0 warnings, 0 notes
```

Certifier exit codes: `0` certified · `1` a case failed / nondeterministic ·
`2` suite, lock, or contract drifted (refused **before** running).

**Test environment (this machine).** Global `commit.gpgsign=true` plus an
unavailable signing agent makes any test that creates a temp git repo fail. Point
`GIT_CONFIG_GLOBAL` at a throwaway gitconfig with `[commit] gpgsign = false`
(e.g. `printf '[commit]\n\tgpgsign = false\n' > /tmp/cbp-gitconfig`) for every
pytest run; never edit the real global config. Pushes may need the
`gh`-token-over-HTTPS workaround (see §"Current git state").

## Documentation freshness (periodic refresh)

Volatile facts — the suite count, the conformance suite ratios, and spec
references — drift when one document is updated and another is missed. This
system makes that drift **fail closed** instead of trusting memory.

- **Single source of truth.** The `# -> N passed` anchor in §"Verify everything"
  above is authoritative; update it from a real run.
- **Machine-checked.**
  [`tests/test_docs_freshness.py`](tests/test_docs_freshness.py) asserts that the
  README test badge, the "Verified state" bullet, and the suite ratios in
  [`ROADMAP.md`](ROADMAP.md) all agree with the anchor, that the **real** collected
  count equals the anchor, and that every `SPEC-NNNN` referenced in the
  README/ROADMAP/HANDOFF exists. It runs as part of the suite.

**Refresh ritual — run whenever a change moves a count or a spec status:**

1. Run the suite and read the real count.
2. Update the anchor in §"Verify everything" (`# -> N passed`).
3. Update the **README badge** (`tests-N%20passing`) and the "Verified state"
   bullet; update any suite ratio that moved.
4. Update [`ROADMAP.md`](ROADMAP.md) if a horizon or spec status moved.
5. If the **conformance** or **Rust host** suites moved, update the four ratios
   here and in `ROADMAP.md` together. The private companion points here for the
   core count, keeps only its own count, and mirrors this guard
   (`infra/tests/test_docs_freshness.py`) to cross-check the four ratios across
   every sibling doc.
6. Run `uv run pytest tests/test_docs_freshness.py` — green means the documents
   agree with reality.

Re-run the ritual at least once per working session; CI enforces it via the suite.

## Verified state (tips)

- All four repos are on `main` and pushed to `origin/main`. **Never trust a
  commit hash written in prose — it drifts. Read the live tips instead:**
  `git -C core log -1 --oneline`, `git -C ../conformance log -1 --oneline`,
  `git -C ../pro log -1 --oneline`, `git -C ../infra log -1 --oneline`.
- Core gates: `ruff` clean; `mypy` clean (138 files); `cbp-check` **READY (8/8)**;
  convention + home-path guard passed; full suite **1926 passed** (~65 s).
- Conformance: shared ABI suite **31/31** on the Python reference host (both
  in-process and over the external protocol) **and** the Rust host; WASM
  execution suite **10/10** on the Rust host (a narrow task fixture, a full
  `component-abi.v1` guest, and detached Ed25519 artifact-signing accept/refuse).
- Network (**Layer 2**): `network.v1` freezes a component network that is
  **content-addressed (pinned) before it runs**, type-checks every edge at
  instantiation, and records a deterministic, replayable trajectory that
  includes the pin. A `planner`-provenanced (generated) network is re-verified
  before execution: the deterministic planner is re-run on the pinned inputs and
  must reproduce the pin byte-identically (`plan-mismatch` otherwise). Network
  suite **14/14** on **both** the Python reference host and the Rust host
  (cross-runtime equivalence).
- Diagram (**Layer 3, static**): `cbp/diagram.py` projects a validated network
  into a deterministic, content-addressed `cbp.diagram.v1` document (longest-path
  ranks; nodes/edges sorted) and renders it to XML-escaped, read-only SVG/HTML.
  Served read-only at `/network/diagram/<name>`. Pure and offline (**14 tests**).
- Appointed (**Layer 4**): `appointed.v1` freezes the allowlisted-appointment
  trust gate — a component from a named external origin is admitted only through
  a host-configured **source allowlist** and a detached **Ed25519** signature over
  its exact bytes, runs contained with zero capabilities (**A0**), and records a
  scoped Assurance Label (property, tier, evidence). The ordered, fail-closed gate
  is implemented by the Python reference host and the Rust host and passes **8/8**
  cross-runtime; `appointed.lock.v1.json` carries the allowlist trust root.
- ABI content address: `component-abi.v1` **revision 3**, `source_sha256`
  `d8e0325d53789d1af631bae7638a5b8947606a2da00698d40452471316a3386e`
  (see `../conformance/contracts/ABI.lock.v1.json`, which also hashes
  `ABI.md` via `abi_md_sha256`). Revision 3 adds the additive data-plane
  transport (`transport.send-on` / `receive-on`).
- WASM fixture artifacts: `../conformance/artifacts/identity.wasm` (task world),
  sha256 `7f798bfd0268fc18cfc19f631de883d010f2cb2514930602a06f4c19f161246e`; and
  `../conformance/artifacts/abi-identity.wasm` (`component-abi.v1` guest),
  sha256 `8ee5c8a4a68310c0be0a5b5fa9f9c4dabeb086f133df32be4aa5d9506033a157`.

## Pinned toolchain / TCB

- Rust **1.97.0** (`pro/rust-toolchain.toml`); `serde_json` **1.0.151**,
  `sha2` **0.10.9**, `wasmtime` **49.0.1** (optional, feature `wasm`),
  `ed25519-dalek` **2.2.0** (optional, feature `wasm`, artifact signing), pinned
  in `pro/Cargo.lock`.
- ABI resolver `wasm-tools` **1.260.0** (in `ABI.lock.v1.json`); guest builder
  `cargo-component` **0.21.1** (fixture uses `wit-bindgen-rt` 0.44.0).
- `pro` deps and the fixture are reproducible: `Cargo.lock` is **committed** in
  both.

## Read first (in order)

1. This file, then [`AGENTS.md`](AGENTS.md) → [`PRINCIPLES.md`](PRINCIPLES.md) →
   [`.agentfactory.toml`](.agentfactory.toml) (active mode **L2**; target L3, gated).
2. [`.agents/skills/`](.agents/skills) — on-demand Agent Skills.
3. [`docs/agent/`](docs/agent/README.md) — architecture, levels, testing,
   verification, committing, components.
4. [`specs/`](specs) — the frozen plan of record: **SPEC-0011** (MVP; accepted),
   **SPEC-0012** (Component ABI), **SPEC-0013** (cross-runtime + vectors),
   **SPEC-0014**–**SPEC-0022**; plus SPEC-0002/0007/0008/0009/0010.
   **SPEC-0020** (service hosting) and **SPEC-0021** (dynamic work) are
   operator-ratified (`accepted`); their **additive contracts**
   (`contracts/provider.py`, `contracts/work.py`), their runtimes
   (`cbp/provider_runtime.py`, `cbp/work_runtime.py`), and the work boot/bundle
   bridge (`cbp/work_boot.py`) are **implemented and green**, and the concrete
   **local-first `provider.v1` adapter** (`cbp/provider_local.py`) is
   **delivered**.
5. `../conformance/AGENTS.md` and `../pro/AGENTS.md` — the shared contract and
   the Rust host.

> **Background (optional, non-normative).** The external literature (FBP, CPM,
> agent SDLC, dark factory) is indexed in
> [`docs/references/README.md`](docs/references/README.md); its PDFs are
> local-only and non-normative. Reading it is **not required** — the laws and
> specs are authoritative.

## Current git state

- **Branch:** `main` in every repo; in sync with `origin/main`.
- **Topology:** `main` is the only branch. The retired Manager line's tip is the
  annotated tag `v0.29.0-milestone`. The convention guard forbids the legacy
  `agent_centric.fbp` package and `fbp-*` gates from returning.
- **Push policy:** commit and push continuously, no permission needed (Law 13);
  never bypass hooks (`--no-verify` forbidden).
- **Environment note (this machine).** Global `git` has `commit.gpgsign=true`
  and `~/.ssh/config` points `github.com` at a public key. If the SSH agent is
  unavailable, commits fail at signing and pushes fail. Commit with a per-command
  `-c commit.gpgsign=false` (never `--no-verify`). For tests that create temp git
  repos, point `GIT_CONFIG_GLOBAL` at a throwaway gitconfig with
  `[commit] gpgsign = false` - never edit the real global config. To push while
  Bitwarden's SSH agent is down, use the already-authenticated `gh` token over
  HTTPS through a throwaway askpass (`GIT_ASKPASS` -> `gh auth token`) with the
  repository's HTTPS URL; this changes no remote and logs no secret.
  **Do not edit global config/**keys.**

## Fresh-session start

1. Read `AGENTS.md` → `PRINCIPLES.md` → `.agentfactory.toml` (mode **L2**;
   target L3, gated) → this file.
2. Confirm every tree: `git branch --show-current` → `main`; `git status` clean
   (in `core`, `../conformance`, `../pro`, `../infra`).
3. Run §"Verify everything" and confirm the results.
4. Continue from **Next**. Never act above the active level without the operator
   changing `.agentfactory.toml` first.

### Kickoff prompt (paste into a new session)

> Continue the mission-critical `agent-centric` system (CBP/ABM; deterministic,
> local-first, fail-closed). Open at the workspace root `cbp/`. Read
> `core/AGENTS.md` -> `core/PRINCIPLES.md` -> `core/.agentfactory.toml` (active
> **L2**; target L3, operator-gated) -> `core/HANDOFF.md`, then
> `core/specs/SPEC-0011`–`0022`, `core/ROADMAP.md`, `conformance/AGENTS.md`,
> `pro/AGENTS.md`, `infra/HANDOFF.md`, and `infra/holdout/runbook.md`. Confirm
> **all four** repos (`core`, `conformance`, `pro`, `infra`) are on `main` and
> clean, then run `core/HANDOFF.md`'s verification block (expect: `cbp-check`
> READY, **1926 passed**; shared **31/31** in-process + external + Rust; WASM
> **10/10**; network **14/14**; appointed **8/8**; infra **118 passed + 2
> skipped**; basedpyright **0/0/0**). Apply the temp-git-repo `GIT_CONFIG_GLOBAL`
> workaround before any pytest run.
>
> **Do not re-implement delivered work.** Complete and green: Layers 0–4 +
> signing; SPEC-0002 Phase-1 items 1/2/6 (adapter-first); SPEC-0018 Phases 1–2 +
> the provisioning spec; the local CD substrate (`infra/`); `review.v1` +
> confidence; the offline umbrella lock (test-signed); the L3-ready validator +
> honest rehearsal (`core/docs/agent/l3-milestone-plan.md`); the documentation/spec
> hygiene pass (real `conformance/` + `pro/` READMEs, reconciled statuses); the
> doc-freshness guards (`core/tests/test_docs_freshness.py`, mirrored in
> `infra/`); and the **SPEC-0020/0021 realization** — the additive contracts
> (`contracts/provider.py`, `contracts/work.py`), the provider runtime
> (`cbp/provider_runtime.py`), the work runtime (`cbp/work_runtime.py`), and the
> work boot/bundle bridge (`cbp/work_boot.py`).
>
> **Next work, in order.**
> (1) **Below-gate build queue is clear.** Workstream A (local-first `provider.v1`
> adapter, `cbp/provider_local.py`) and Workstream D (threshold-signing
> verification, `cbp/threshold.py`) are **delivered**. The remaining
> SPEC-0020/0021 unit — the concrete provider manifest/task wiring — is
> host-specific and belongs to the private companion. Next, run a dedicated
> research session for the charters
> ([`docs/agent/brain-charter.md`](docs/agent/brain-charter.md),
> [`docs/agent/learning-charter.md`](docs/agent/learning-charter.md)); do not start
> any operator-gated unit.
> (2) **Operator-gated:** the deliberate L3 flip (author scenarios in
> `$CBP_HOLDOUT_ROOT`, run the validator green with `isolation: enforced` on a
> distinct principal/host, then edit `[levels.L3]`); SPEC-0018 Phases 3–6 (VPS +
> public read-only mirror, multi-tenant/branding, source-of-truth migration,
> hardening); re-sign the composition/umbrella locks with the operator key;
> optional threshold signing.
> (3) **Research (charters, no design freeze):**
> [`docs/agent/brain-charter.md`](docs/agent/brain-charter.md) and
> [`docs/agent/learning-charter.md`](docs/agent/learning-charter.md), each for a
> dedicated session; the SPEC-0017 frontier afterward.
>
> Hard laws: whole-file replacement only via `tools/safe-replace.sh` (**never
> in-place edits**), resolve every home path from `$HOME`, commit and push
> continuously, never `--no-verify`. Mission-critical boundaries: never act above
> L2 without the operator changing `.agentfactory.toml`; operator gates = the L3
> flip, real host provisioning/enrollment, the operator signing key (including
> re-signing the composition/umbrella locks), and any public publish; the
> **authoring principal must be unprivileged and distinct** (`agent-runner`, no
> docker/sudo), and holdout scenarios and keys never enter a public repo. Every
> change must be **correct, robust, secure, and idempotent**. Volatile counts are
> machine-checked — run the refresh ritual in `core/HANDOFF.md`
> §"Documentation freshness" whenever a count or a spec status moves.

## What Agent-centric is

- **Two layers (SPEC-0007).** `agent-centric` is the **harness** — boot + runtime
  + meta. It **executes** the CBP tree and is **not** a node. The tree's root is
  the **shell component**, an ordinary component.
- **Everything is a component** (CBP node = ABM agent). A component is an
  **abstract, blank object**: `init`/`run`/`kill` over a **ZeroMQ** event loop, a
  **typed WIT contract**, and **two-level identity** (abstract = contract hash;
  task = signed content hash).
- **Posture:** deterministic control plane, local-first, fail-closed, no
  unverified success, full auditability.

## The frozen plan (full version — SPEC-0011 accepted)

The planning session fixed the target as the **full version**, not a crippled
subset. The Core public repo is a **Python reflection** that helps build it.

- **SPEC-0012 Component ABI v1.** `init(boot_config)` / `run(event_loop)` /
  `kill()`. Transport **ZeroMQ**. Control plane is always canonical JSON; the
  data plane is a host-granted encoding (`json` baseline, pinned canonical
  `msgpack`; canonical JSON is integers-only, floats MUST use a binary
  encoding). All channels/ports and tasks are announced over the **initial
  hard-coded channels**. Identity is **never the ports**; at rest a component has
  **no edges**. Registry sources are open (git repo, directory, binary, …).
- **SPEC-0013 Cross-runtime realization + conformance vectors.** Two paths:
  **translation/compilation** (canonically WASM) and **native-runtime invocation**
  (language-server protocol; runtime dialed up / JIT-provisioned). **Equivalence
  is proven by the shared vectors, never by Turing completeness**; a runtime is
  TCB and is version-pinned, signed, and recorded.
- **SPEC-0014 Network execution and trust.** Network is orthogonal to
  components; static or generated; execution only runs a content-addressed
  network; a generated network is pinned before running.
- **SPEC-0015 Provenance, trust gates, Assurance Labels.** Classes: static
  (launch), appointed (web/RAG; optional), generated/compiled/translated (gated),
  discovered (gated). "Safe" is a scoped, evidence-backed label (tiers A0–A4),
  never absolute; promotion past A0/A1 is human-gated until the L3 validator.
- **SPEC-0016 Editions, distribution, cloud.** Core (public Python reflection,
  headless) / Pro (private, Rust) / Enterprise (private components) / cloud
  (deployment adapter). Edition = a manifest selecting components. One verified
  semantics; tiers differ only in capacity/performance/deployment/governance/
  support/UI.
- **SPEC-0017 Parked frontier.** Formal semantics/ontology/closed shapes;
  reduction + formal verification (Z3/SMT → Assurance Labels); Universal Function
  Index/dynamic discovery; isolated validator (L3); bounded RSI; whitepaper last.

**Execution layers (target; every layer fallbacked).**

| Layer | Target | Fallback | Status |
| --- | --- | --- | --- |
| 0 | Freeze **ABI + conformance vectors** | must not slip | **done** |
| 1 | **Rust host** runs signed **WASM** components | **Python host**, same ABI | **1a/1b/1c + signing done** |
| 2 | **Content-addressed network** + typed enforcement + replayable trajectory | static `network.v1` | **done (static + generated planner)** |
| 3 | **Read-only web diagram** from the network document | static JSON/image | **done (json + svg + read-only route)** |
| 4 | **Appointed** components (allowlisted) | static-only | **done (allowlist + A0 gate)** |

Launch provenances: static, A0 sandboxed. Gated: generated/translated/discovered.

**Process rule:** on complexity/blockage, **step back one or two specs to
resurrect intent** before forcing a workaround.

**Open design items (deferred with intent).** (1) State model: parent-held
environmental state for children vs component-sovereign local state. (2) The
blank-component → dynamic task-load protocol (bind timing), hardened at Layer 2.

## Where we are (delivered)

- **Component ABI + conformance (SPEC-0012/0013, Layer 0)** — `../conformance/`:
  `contracts/component-abi-v1.wit` (validated with pinned `wasm-tools` 1.260.0),
  the control/data-plane split, the normative `ABI.md` (hashed in the lock), and
  the content address `ABI.lock.v1.json`. `vectors/` holds the shared suite
  (31 cases across semantics, lifecycle, transport, determinism, capability,
  encoding) in canonical form, content-addressed by `vectors.lock.v1.json`.
- **Python reflection host + certifier (Layer 0)** — `../conformance/certifier/`:
  `certify.py` (verifies suite/lock/contract, drives a host, compares normalized
  observations, enforces cross-run determinism, emits a deterministic record) and
  `reference_host.py` (the test double + external-protocol endpoint).
- **Rust host (Layer 1a)** — `../pro/`: `cbp-host` implements the ABI and the
  `cbp.conformance-host.v1` protocol and passes the shared suite **31/31**,
  identical to the Python reference host. **Cross-runtime equivalence is proven.**
  Codecs: canonical JSON + pinned canonical MessagePack, strict
  re-encode-and-compare decoding.
- **WASM execution (Layers 1b/1c)** — `../pro/` (feature `wasm`, pinned
  `wasmtime`): content-address an artifact, refuse a tampered one, compile it as
  a **WASM component**, drive `init`/`run`/`kill`. **1b**: narrow task fixture
  (`../pro/fixtures/identity/` → `../conformance/artifacts/identity.wasm`).
  **1c**: a full **`component-abi.v1` guest** (`../pro/fixtures/abi-identity/` →
  `../conformance/artifacts/abi-identity.wasm`) that announces its channels/task
  over the control channel and exchanges Information Packets via the host
  `transport`; the host enforces announcements, `ready`, endpoint addressing,
  declared types, canonical encodings, and the envelope. **Signing**: every
  artifact must carry a detached **Ed25519** signature over its exact bytes that
  verifies against the host trust root (carried in
  `../conformance/vectors/wasm.lock.v1.json`, never fixture data); unsigned or
  bad-signature artifacts are refused before execution. WASM suite **10/10**.
- **Network + diagram (Layers 2/3)** — `../conformance/`: `contracts/network-v1.md`
  (revision 2) and `vectors/network-{fixtures,suite,lock}.v1.json` (**14 cases**;
  static + deterministic-planner, pinned before running, typed at instantiation,
  replayable), implemented by the Python reference host and the Rust host
  (`../pro/src/network.rs`). `core`: `cbp/diagram.py` projects a validated network
  into a deterministic, content-addressed read-only diagram (JSON + SVG).
- **Appointed (Layer 4)** — `../conformance/contracts/appointed-v1.md` (revision
  1) and `vectors/appointed-{fixtures,suite,lock}.v1.json` (**8 cases**):
  allowlisted appointed sources, an ordered fail-closed gate, A0 containment, and
  a recorded Assurance Label. Implemented by the Python reference host
  (`certifier/reference_host.py`, pure-stdlib RFC 8032 verifier) and the Rust host
  (`../pro/src/appointed.rs`); **8/8** on both.
- **Convention layer (SPEC-0001/0003/0004)** — `AGENTS.md` TOC, canonical skills
  (auto-discovered), `tools/new-skill.sh`, same-name component mapping, guarded by
  `tests/test_agent_conventions.py`.
- **Publication hygiene (SPEC-0016 §94 / SPEC-0018 §13)** —
  `tests/test_public_boundary.py` fails closed if a private sibling-repo slug,
  private host id, or RFC 1918 address appears in this public repo. The
  workspace-root basedpyright policy (the IDE tray must read
  `0 errors, 0 warnings, 0 notes`) is versioned in `tools/workspace/` with
  `check-pyright.sh`.
- **Distribution spine (SPEC-0007)** — `component.v1` + `components.lock/v1`;
  content-addressed cache; deterministic resolver; minisign/gpg verification;
  offline directory + git sources; deterministic bundle; boot from lock (with
  lock-level signature verification, fail-closed); process isolation; signing
  service + transparency log. Boot is **idempotent** (booting the same lock twice
  yields an identical tree) and the transparency-log append is **idempotent for
  its head** (a retried release never duplicates evidence).
- **Offline umbrella `components.lock` (SPEC-0011 / SPEC-0007 Phase 2)** —
  `designs/core.v1.json` (shell → registry) is pinned by an **offline directory
  source** through the additive `DirectoryPin` path in `cbp/lockfile.py`; the
  canonical bundles (`artifacts/components/*.tar`), the lock (`components.lock`),
  and its detached signature are committed, and `tools/umbrella-lock.sh`
  regenerates and verifies them deterministically. The committed lock is
  **test-signed offline**; the operator re-signs with the real key (key gate).
  Boot verifies the lock signature and every component signature from a
  `DirectorySource` (12 tests).
- **Shell design (SPEC-0008)** — `design.v1` + `validate_design`/`compile_design`.
- **Design → pin → lock (SPEC-0008 Phase 3)** — `cbp/lockfile.py` (`pin_design` +
  `ComponentPin`) resolves a design's requested refs to immutable commits and
  content-addressed bundles **at pin time** (offline), deterministically and
  fail-closed (invalid design, unnamed identity, unsigned component, unpinned
  contract, or unresolvable ref all refuse); `record_release` signs + appends the
  lock and `boot_from_lock` boots the tree. Proven by `tests/test_lockfile.py` +
  demo `examples/design_pin.py`.
- **Signing-key rotation (SPEC-0007 §6)** — `cbp/trust.py` implements overlapping
  trust windows (`TrustStore`/`TrustWindow`): a retired key keeps **verifying**
  while only the current key **signs**; key selection and verification are
  explicit-time and fail-closed; every release records its `key_id`, and the
  transparency log verifies against the rotated ring. Proven by
  `tests/test_trust.py` + demo `examples/key_rotation.py`.
- **n8n authoring adapter (SPEC-0008 Phase 2)** — `cbp/n8n_adapter.py` projects a
  `design.v1` ⇄ an n8n workflow (**nodes** ⇄ components, **connections** ⇄ edges;
  edge/IIP metadata on the target node). The round-trip is lossless and stable
  (same `design_hash`; re-export reproduces the same canonical bytes) and
  fail-closed on any unsupported/inconsistent input. Proven by
  `tests/test_n8n_adapter.py` + demo `examples/n8n_roundtrip.py`.
- **FBP/ABM conformance (SPEC-0009)** — named/typed ports, Information-Packet
  flow with fail-closed back-pressure/deadlock, per-port IIPs, composite
  external-ports boundary guard, repeated-activation streaming, per-component
  state sovereignty, a component at rest has no edges, and recorded,
  confidence-scored `model` components (`cbp/model_record.py`). **9/9** built.
- **Docs / presentation** — README showcase; historical docs removed; `STATUS.md`
  retained as volley history.

## Spec status audit

`status` is `draft | accepted | implemented`; `accepted` = design approved, not built.

- **SPEC-0011** `accepted` — MVP definition frozen; acceptance reconciled
  (status record corrected; the `implemented` specs' boxes ticked).
- **SPEC-0012 / SPEC-0013** `accepted` — **Layers 0/1a/1b/1c + signing
  delivered** (ABI rev 3, vectors, certifier, Rust host 31/31, content-addressed
  WASM 10/10 including a full `component-abi.v1` guest and Ed25519 artifact
  signing).
- **SPEC-0014** `implemented` — `network.v1` (static + deterministic-planner)
  delivered: pinned before running, typed at instantiation, replayable; suite
  **14/14** cross-runtime. (Autonomy proposes / the pin disposes is the pin rule.)
- **SPEC-0015** `draft` — **appointed launch slice delivered** (Layer 4:
  `appointed.v1` source allowlist + ordered A0 gate, **8/8** cross-runtime); tiers
  A1–A4, `review.v1` promotion, and the generated/discovered gates remain
  (SPEC-0017).
- **SPEC-0016** `draft` — frozen; nothing built. **SPEC-0017** `draft` —
  frontier index; its §4 **isolated holdout validator** is promoted to
  **SPEC-0019** (below).
- **SPEC-0002** `accepted` — **Phase-1 items 1, 2, 6 delivered** (adapter-first,
  under L1): `review.v1` + response `confidence` + the persistent append-only
  Review queue (`contracts/review.py`, `cbp/review.py`, 37 tests); the
  `Agent`→`Component` adapter (`cbp/component_adapter.py`); a parent-provisioned
  `ComponentCatalog` (`cbp/component_catalog.py`) that **removed the module-level
  registry global** and the central `_child_class_for` map (parent-declared
  kinds; undeclared kind fails closed); and explicit `inproc`-only backend
  enforcement. Design record: `docs/agent/spec-0002-phase1-adapter-design.md`.
  (Holdout scenarios are operator-authored, in SPEC-0019.)
- **SPEC-0003/0004/0006** `implemented` — criteria met by the convention guard;
  acceptance boxes ticked.
- **SPEC-0007** `accepted` — Phases 0/0.5a/1a/1b/2 delivered; 0.5b signing,
  **key rotation**, and **threshold-signing verification** (`cbp/threshold.py`:
  canonical k-of-n bundles, fail-closed, drops into boot) delivered; the
  **offline umbrella `components.lock`** is
  committed (test-signed offline; operator re-signs with the real key);
  **live mirror open**.
- **SPEC-0008** `implemented` — `design.v1` + validate/compile, the Phase 3
  `pin`/`record` wiring (`cbp/lockfile.py`), the Phase 2 n8n adapter
  (`cbp/n8n_adapter.py`), **and** design envelope limits (`cbp/design.py`
  validate + `pin`) delivered.
- **SPEC-0009** `implemented` — **9/9** built: recorded, confidence-scored
  `model` components delivered (`cbp/model_record.py`).
- **SPEC-0010** `draft` — roadmap only.
- **SPEC-0018** `draft` — service-host provisioning + continuous deployment
  (verify-then-apply; self-hosted Gitea first). Public origin-agnostic spec in
  `specs/SPEC-0018-service-host-provisioning-and-continuous-deployment.md`; the
  private instantiation lives in a separate **private companion repo**. Additive
  public **`service.v1`** contract delivered (`contracts/service.py`, 36 tests,
  incl. additive task-content pins and opt-in `require_task_digests`); Phase 1
  infra **authored + hardened** (deterministic tasks, a pull/apply agent that
  content-verifies every mutating task, a pinned host definition + `service.v1`
  instance, enrollment runbook, infra CI, DR drill) — **nothing provisioned**.
  Phase 2 graduation **authored**: the bootstrap services are wrapped as public
  additive `component.v1`, pinned in a signed composition lock, and bound by
  `service.v1` (`refs.composition`); the agent verifies the lock + graph before
  apply (fail-closed).
- **SPEC-0019** `draft` — the **L3 isolated holdout validator is L3-ready**.
  Structural isolation (operator-private owner-only scenarios; public
  `specs/holdout/` a placeholder; a distinct unprivileged principal; a
  context-gated separate process) plus: the pure library `cbp/holdout.py` behind a
  thin CLI; the additive `service` probe (boot a signed composition via an
  operator-private, content-addressed `holdout.deployment/v1` descriptor and
  compare a canonical-JSON projection / assert a refusal); TCB pins in the lock
  (`probe_contract_sha256`, `validator_sha256`) and record (`runtime`,
  `platform`, `lock_sha256`, `suite_sha256`, `isolation`); exit codes `0/1/2/3`; an
  advisory principal guard with a recorded rehearsal escape; a synthetic
  known-good/known-bad `--self-test`; and a wall-clock-free idempotent ledger
  (advisory-locked + `fsync`-ed). Delivered under L1; **operator-authored
  scenarios, the authoritative `isolation: enforced` run, and the L3 flip
  remain**; L3 stays `declared-gated`.
- **SPEC-0020** `accepted` — service hosting: the additive **`provider.v1`**
  contract is **delivered** (`contracts/provider.py`, 61 tests: pure plan,
  verify-then-apply gates, deterministic reconcile decision, emit-only
  notification, local-first/cloud, secret refs only). The deterministic runtime
  engine (injected adapter, idempotent evidence ledger, reconcile/rollback/
  escalation, emit-only notifications) is **delivered**
  (`cbp/provider_runtime.py`, 20 tests). The concrete **local-first adapter**
  is **delivered** (`cbp/provider_local.py`, 17 tests: named, content-pinned
  local tasks; verify-then-apply; additive read-only `observe`); a
  network/cloud adapter remains operator-gated.
- **SPEC-0021** `accepted` — components as dynamic work: the additive **`work.v1`**
  contract is **delivered** (`contracts/work.py`, 49 tests: pure
  `work.v1 → component.v1` compile seam; verifier-gated generated work; explicit
  fail-closed dynamic task-load protocol). The compile/`materialize` runtime and
  the fail-closed task-load binder are **delivered** (`cbp/work_runtime.py`, 13
  tests), and the binder is **wired through boot/bundle** (`cbp/work_boot.py`,
  8 tests: resolve a task entry from a verified bundle, allowlisted in-process or
  process-isolated, and run a `network.v1` over the bound tasks). Charters for the
  Brain and for learning/RSI live in `docs/agent/`.

- **SPEC-0022** `draft` — component-provided UI: a **target-agnostic `ui.v1`**
  contract (projections in, directives out, capability grants, slots/tokens,
  secret refs only) with pluggable **UI targets** (web/cli/os/embedded); a
  behavior component may carry a **pinned UI ref** without changing its verified
  semantics; dynamic assembly is **pinned before render**; **mandatory,
  provenance-tiered capability sandboxing** (security over dynamicism);
  verify-before-execute with strict UI-ABI negotiation; pure projections of pinned
  documents; directives through the verified spine. Formalizes `cbp/web.py` as the
  `web` v0 target. The typed **UI ABI** is authored and content-addressed
  (`conformance/contracts/ui-v1.wit` + `UI-ABI.md` + `ui.lock.v1.json`), and
  the core `ui.v1` manifest (`contracts/ui.py`) is implemented and green
  (20 tests). The target bindings and assembly remain unimplemented.

## Next

- **SPEC-0020 / SPEC-0021 ratified (this session); the next buildable unit.** The
  operator-ratified `accepted` specs are
  [`SPEC-0020`](specs/SPEC-0020-service-hosting-provider-abstraction-and-self-healing.md)
  (service hosting: a thin `provider.v1` — pure `plan`, gated idempotent
  `apply`/`health`/`teardown`; verify-then-apply; a reconcile loop with
  deterministic auto-rollback and fail-closed escalation; an emit-only
  notification component; declarative local-first/cloud; authN/Z) and
  [`SPEC-0021`](specs/SPEC-0021-components-as-dynamic-work.md) (components as
  dynamic work: a declarative pinned `work.v1` recipe compiling via a pure seam
  to `component.v1`, plus the dynamic task-load/bind protocol). The additive
  contracts (`contracts/provider.py`, `contracts/work.py`) are **implemented and
  green**, their **runtime realization** is delivered (`cbp/provider_runtime.py`,
  `cbp/work_runtime.py`; 33 tests), and the `WorkBinder` is **wired through
  boot/network** (`cbp/work_boot.py`, 8 tests); `component-abi.v1` stays frozen.
  The only remaining SPEC-0020/0021 unit below the gates is a concrete provider
  adapter; after that the work is operator-gated (L3 flip, SPEC-0018 Phases 3-6,
  re-signing, publish). Study charters for the Brain
  ([`docs/agent/brain-charter.md`](docs/agent/brain-charter.md)) and for
  learning/RSI ([`docs/agent/learning-charter.md`](docs/agent/learning-charter.md))
  are parked for dedicated sessions.

- **SPEC-0002 Phase-1 remainder — S1–S5 implemented and verified (this
  session).** The design record is
  [`docs/agent/spec-0002-phase1-adapter-design.md`](docs/agent/spec-0002-phase1-adapter-design.md)
  (§12 = implementation progress). Delivered, each green: **S1** the
  `Agent`→`Component` adapter (`cbp/component_adapter.py`); **S2** the
  parent-provisioned `ComponentCatalog` + `AgentConfig.catalog`; **S3a** the
  driver-owned catalog provisioned to the root and inherited by children;
  **S4** the central `_child_class_for` map **deleted** (parent-declared
  kinds; undeclared kind fails closed before any bind); **S5** explicit
  `inproc`-only backend (`BackendNotAdmitted` otherwise). **S3b** deletes the
  module-level `_REGISTRY`/`_resolve_entry`/`register_callable` shim, makes
  `Agent._catalog_entry` resolve only from the parent-provisioned catalog, and
  migrates every test/authoring call site (a guard test asserts no global
  remains). **SPEC-0002 Phase-1 items 1, 2, and 6 are complete.** Core suite
  **1680 → 1708 passed**; mypy 130 files; basedpyright 0/0/0.

- **L3 milestone delivered agent-side (this session).** The validator is
  L3-ready (A) and hermetically accepted (D); the isolation runbook (B) and
  scenario templates (C) live in the private companion. Remaining, in dependency
  order: (1) **L3** — the operator executes the isolation runbook, authors
  holdout scenarios in the operator-private root (`$CBP_HOLDOUT_ROOT`), runs the
  validator green (`isolation: enforced`) on a distinct principal/host, and flips
  `[levels.L3]` deliberately; (2) **SPEC-0018 Phase 3** — the low-budget VPS +
  public read-only mirror, then Phases 4–6 (multi-tenant/branding,
  source-of-truth migration, hardening) (operator/publish-gated); (3) **signing**
  — optional threshold signing; (4) **SPEC-0017 frontier** — formal
  semantics/reduction, the Universal Function Index, bounded RSI, whitepaper
  last. (SPEC-0002 Phase-1 is **delivered** — see *Latest session*.)
- **Documentation & specification hygiene — delivered (this session).** (a) The
  real `conformance/README.md` + `conformance/contracts/README.md` and
  `pro/README.md` are authored (the byte-identical vectors-README copies are
  gone); (b) the `conformance/`/`pro/` `AGENTS.md` "Next" lines and the
  `pro/AGENTS.md` copy are corrected, and the duplicated `PRINCIPLES.md`
  cross-links are retargeted to `../core/`; (c) SPEC-0008/0009 are `implemented`,
  SPEC-0002's §12 boxes are reconciled (the holdout half stays operator-gated),
  and SPEC-0003/0004/0006/0011 boxes are ticked; (d)
  [`docs/agent/component-adapter.md`](docs/agent/component-adapter.md) records the
  adapter/catalog and the removed global. A repo-wide relative-link check passes.
- **Umbrella `components.lock` — delivered offline; operator signing remains.**
  The `design → pin → components.lock` producer and the additive offline
  **directory-pin** path are delivered; `designs/core.v1.json`, the canonical
  bundles, the lock, and its detached signature are committed and regenerated
  deterministically by `tools/umbrella-lock.sh`. The committed lock is
  **test-signed offline**; the operator re-signs it with the real, out-of-process
  key and installs the public trust root (key gate). Everything else on the spine
  is done: content-addressed cache, deterministic resolver, minisign/gpg
  verification, git/directory sources, deterministic bundle, boot-from-lock (with
  lock-level signature + transparency log verification, fail-closed), **key
  rotation**, and the **`pin`/`record`** wiring.
- **SPEC-0018 (draft): service-host provisioning + CD** — public spec in
  `specs/` (now with a §4.1 provisioning protocol); the instantiation lives in
  the **private `infra/` companion**. Authored: Phase 1 (pinned host definition,
  `service.v1`, named tasks, pull/apply agent), **Phase 2** (bootstrap services
  graduated to `component.v1`; `service.v1` binds the signed composition; the
  agent verifies lock + graph before apply), and the **provisioning
  specification** (`infra/specs/provisioning.md`, P0–P9) with its tasks
  (`verify-image`, `install-host` plan/gated, `authorize-host`). **Nothing is
  provisioned.**
- **L3 holdout gate (SPEC-0019)** — the validator is built and its isolation is
  **structural**; the remaining steps are operator-side: author scenarios in the
  operator-private root (`$CBP_HOLDOUT_ROOT`, default `$HOME/.cbp/holdout/suite`),
  pin them (`tools/holdout-validate.py --build-lock --suite …`), run the validator
  to **green** on a principal/host distinct from the author, then flip
  `[levels.L3] status` deliberately.
- **Signing** — **key rotation delivered** (`cbp/trust.py`); optional threshold
  signing remains.
- **SPEC-0017 frontier** — formal semantics/ontology; reduction + formal
  verification; Universal Function Index; bounded RSI; whitepaper last. The
  **isolated validator (L3)** is now **SPEC-0019** (spec + Phase 1 isolation +
  decision engine + probe runner delivered).

## Open threads (blockers & honest gaps)

- **Live self-hosted mirror** (Gitea) — no real pinned remotes yet. Now tracked by
  **SPEC-0018** (`specs/`) + a **private companion repo**; Core's umbrella lock is
  offline-signed (SPEC-0011) and no longer depends on it.
- **Integration spine** — delivered. The **lock-level signature is consumed at
  boot** (`boot_from_lock` verifies a detached signature over `lock_hash` before
  resolving anything, and records `lock_verified`, fail-closed); boot may also
  require the lock to be published in a **serving transparency log** (chain +
  signatures + head verified; an unpublished lock refuses). The
  `design → pin → signed lock` producer is delivered (`cbp/lockfile.py`), and
  `cbp/boot_network.py` closes the end-to-end wiring: **boot a signed lock →
  run its typed-port `network.v1` over the verified entries → replay the durable
  directive ledger**, with a spawned `model` node recorded and
  confidence-scored (`tests/test_viable_demo.py`, `examples/viable_demo.py`).
  The **operator-signed system umbrella lock is recorded** in the private infra
  repo (`locks/core-umbrella/`: lock, bundles, and the **public** trust root),
  verified with the core `GpgVerifier` against a keyring holding only the public
  key; the committed Core lock stays test-signed for hermetic CI.
- **`review.v1` / `confidence`** — **delivered** (SPEC-0002 §4/§6):
  `contracts/review.py` + `cbp/review.py`, a persistent append-only Review queue,
  and a deterministic confidence on every driver response (37 tests). The
  formalizer / higher tiers remain under SPEC-0017.
- **Honest FBP note** — non-port networks still run on the legacy args model;
  port-declared networks use `run_flow`. Both are deterministic.
- **The ABI is now consumed.** `component-abi.v1` (revision 3) is implemented
  by a real `component-abi.v1` WASM guest (Layer 1c), so changes are
  **additive-only** from here (`component-abi.v2` for anything else). The one
  pre-consumption correction made at consumption was the data-plane transport
  (`transport.send-on` / `receive-on`), which the frozen `send`/`receive`
  (control channel only) could not express.
- **Pre-existing doc drift (not introduced by this work).** In the untouched
  public `conformance/` and private `pro/` repos, `README.md`,
  `conformance/contracts/README.md`, and `conformance/vectors/README.md` are
  byte-identical copies of the *vectors* README, so their relative links
  (`fixtures.v1.json`, `../certifier/`) resolve wrongly at the top level. The
  proper fix is to author real top-level READMEs (roadmap candidate). A
  repo-wide link check reports these plus the copied `PRINCIPLES.md` cross-links;
  every link in the documents changed this session resolves.
- **Unrun local gate.** `infra/tools/pin-flake.sh --check`, `nix flake lock`, and
  `nix flake check` were **not** executed here (no `nix` installed). The committed
  `hosts/<host>/flake.lock` is validated by infra CI and by a skipped test
  (`CBP_RUN_NIX=1` on a nix-capable host); verify there before P3.

## How to work here (hard laws)

- **Law 11 — no in-place edits, EVER.** Replace whole files via
  [`tools/safe-replace.sh`](tools/safe-replace.sh) (`opencode.json` denies
  `edit`/`write`/`patch`). Transform a large file into a temp and `safe-replace`
  it; never retype-and-drift, never `sed -i`, never `Path.write_text`.
  See the `safe-file-editing` skill.
- **Law 12 — test authority.** The agent may run any tests; report results
  faithfully; the operator/CI is the record of truth.
- **Law 13 — commit and push continuously.** Hooks and CI are the guardrails;
  never `--no-verify`.

**Process risk (observed — guard against it).** Law-11 slips have occurred here.
An early session made **three** (twice `sed -i`, once Python `write_text`); a
later hardening session made **two more `write_text` slips** — a patch script
wrote docs and tests directly instead of piping through `safe-replace`. Each was
caught and the whole file re-emitted via `tools/safe-replace.sh`, so committed
content is byte-complete — but the *method* was wrong. **The patch script is the
trap:** even a "read → transform → write" script must write to a temp and go
through `safe-replace`; never call `Path.write_text` on a repo file. If you catch
yourself reaching for an in-place tool, stop and use `safe-replace`.

## Key files

- **conformance (1c)** — `artifacts/abi-identity.wasm`;
  `vectors/wasm-{fixtures,suite,lock}.v1.json` (10 cases).
- **pro (1c)** — `fixtures/abi-identity/` (WIT-referencing `component-abi.v1`
  guest); `src/wasm.rs` (ABI bindgen + host mediator).
- **core** — `src/agent_centric/cbp/` (`component_bundle.py`,
  `component_source.py`, `component_runtime.py`, `component_state.py`,
  `component_graph.py`, `component_boot.py`, `component_process.py`,
  `signing_service.py`, `transparency.py`, `trust.py`, `design.py`,
  `lockfile.py`, `n8n_adapter.py`, `network.py`, `flow.py`,
  `cache.py`, `resolver.py`, `signing.py`, `agent.py`); `src/agent_centric/contracts/`
  (`component.py`, `components_lock.py`, `design.py`); `specs/SPEC-0011`–`0019`;
  `examples/components/`; `tests/test_agent_conventions.py`.
- **conformance** — `contracts/component-abi-v1.wit`, `contracts/ABI.md`,
  `contracts/ABI.lock.v1.json`, `contracts/network-v1.md`,
  `contracts/appointed-v1.md`;
  `vectors/{fixtures,suite,vectors.lock}.v1.json`;
  `vectors/network-{fixtures,suite,lock}.v1.json` (network.v1, 14 cases);
  `vectors/appointed-{fixtures,suite,lock}.v1.json` (appointed.v1, 8 cases);
  `vectors/wasm-{fixtures,suite,lock}.v1.json`; `artifacts/identity.wasm`;
  `certifier/{certify.py,reference_host.py,PROTOCOL.md}`.
- **pro** — `src/{main.rs,host.rs,codec.rs,wasm.rs,network.rs,appointed.rs}`; `fixtures/identity/`
  (guest + WIT); `scripts/certify.sh`; `Cargo.toml` + committed `Cargo.lock`;
  `rust-toolchain.toml`.

## Non-goals (do not build without explicit direction)

See [`AGENTS.md`](AGENTS.md) → *Non-goals*. In short: no new agents/ACP/refactor/
dependency bumps; no auto-accept or unsupervised money; no send/delete/move
email; no cloud/network in CI; no changing Manager orchestration/verification/
policy/envelope/accounting semantics (prefer adapters/backends); never bypass
hooks or CI; discovery/generation is never auto-trusted (SPEC-0015 gates).

## Historical documents

[`STATUS.md`](STATUS.md) records the volley-by-volley history; not updated
retroactively.
