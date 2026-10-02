# SPEC-0002 Phase-1 remainder — adapter-first design

> **Status: S1–S5b implemented and verified (SPEC-0002 Phase-1 items 1, 2, 6 complete).**
> **how** the remaining Phase-1 items of
> [`../../specs/SPEC-0002-cbp-component-architecture.md`](../../specs/SPEC-0002-cbp-component-architecture.md)
> are built: the `Agent`→`Component` adapter (item 1), removal of the module-level
> callable registry and the central `_child_class_for` map (item 2), and
> `inproc`-only enforcement (item 6). The delivered `review.v1` + `confidence`
> slice (items 7 and part of 4/5) is **not** re-opened. This is **target, not
> implementation**; nothing below is a claim of done. It is produced under
> **L1** and requires operator review before the risky slices land. If this page
> disagrees with [`../../PRINCIPLES.md`](../../PRINCIPLES.md) or SPEC-0002, those
> win.

## 0. Scope, gates, and non-goals

- **In scope:** adapter-first delivery of SPEC-0002 §7 Phase-1 items 1, 2, 6, with
  the §12 acceptance criteria as the exit condition.
- **Operator gates untouched:** the L3 flip, provisioning/enrollment, the operator
  signing key, and any public publish. No `.agentfactory.toml` edit.
- **Non-goals:** bounded connections/back-pressure, async scheduler, non-`inproc`
  backends, model determinization, automated review tier, `skill.v1` (SPEC-0002
  §7/§8). No behavior change is permitted in the adapter slice.

## 1. Current state (verified this session)

Two contracts exist today and must not be conflated:

- **Declarative `component.v1`** — [`../../src/agent_centric/contracts/component.py`](../../src/agent_centric/contracts/component.py)
  (`ComponentManifest`, `EntryDescriptor`, `ChildRef`, `Provenance`, ports). This
  is already consumed at boot by
  [`../../src/agent_centric/cbp/component_boot.py`](../../src/agent_centric/cbp/component_boot.py)
  and executed allowlist-guarded by
  [`../../src/agent_centric/cbp/component_runtime.py`](../../src/agent_centric/cbp/component_runtime.py).
- **In-process lifecycle** — the `Node` Protocol (`init`/`run(work)`/`kill`) in
  [`../../src/agent_centric/cbp/node.py`](../../src/agent_centric/cbp/node.py),
  implemented only by the legacy `AgentNode`/`Shell` pair. The live runtime
  `Agent` ([`../../src/agent_centric/cbp/agent.py`](../../src/agent_centric/cbp/agent.py))
  does **not** implement it: it exposes `init`/`kill`, an async `run()` event
  loop, and `poll()`.

The remaining coupling is measurable and small:

- **Global registry:** `_REGISTRY` (`agent.py:81`), written by `register_callable`
  (`agent.py:93`) and read by `_resolve_entry` (`agent.py:84`). Read sites:
  `agent.py:602` and `agent.py:605` (a `configure` directive resolves the task and
  verifier names a child is granted), and `driver.py:386,388,552,568,1239`. Imports:
  `driver.py:41`, `cbp/__init__.py`, `agent_centric/__init__.py:35,254`.
- **Instance registry:** `self._registry = Registry()` (`agent.py:139`); the driver
  copies each registered entry into the root's instance registry (`driver.py:386`).
- **Central child-class map:** `_child_class_for` (`agent.py:979`), called at
  `agent.py:955`; hardcodes `store`/`bills`/`model` and defaults to `Agent`.
- **Tests that seed the global directly:** `tests/test_cbp_replay.py` (incl.
  `_agent._REGISTRY.clear()` at `:502`), `tests/test_cbp_agent.py`,
  `tests/test_cbp_driver.py`, `tests/test_cbp_envelopes.py`,
  `tests/test_cbp_model_agent.py`.
- **`inproc`-only:** there is no backend selector today; execution is in-process by
  default and subprocess execution is already opt-in (`allow_subprocess=False`)
  behind `component_boot`. Item 6 is therefore mostly an **enforcement + proof**
  obligation, not a new subsystem.

## 2. The single `Component` contract (interpretation)

SPEC-0002 §1 retires the `Node`/`Shell` dual model; SPEC-0012 fixes the runtime
lifecycle as `init(boot_config)` / `run(event_loop)` / `kill()`. The `Agent`
already matches that lifecycle (`init`/`kill` + async `run`). We therefore take
the **one Component contract** to be:

1. **Declarative:** `component.v1` (`ComponentManifest`) — identity, kind,
   `implements`, entry, ports, capabilities, state, children, provenance. This is
   the distributable, content-addressable face.
2. **Runtime:** the SPEC-0012 lifecycle over the event loop, plus the harness
   entry convention (a callable `payload -> result` used by `boot_from_lock`).

The legacy `Node` Protocol is the **retire-last** surface (migration step 4,
below); it is not extended. No new contract version is introduced: the adapter is
additive over the frozen `component.v1`.

## 3. `Agent`→`Component` adapter (item 1, no rewrite)

Add **one** additive adapter module
(`src/agent_centric/cbp/component_adapter.py`) exposing `AgentComponent`, a thin
wrapper around a live `Agent` that never re-implements agent behavior:

- **Lifecycle passthrough:** `init()`, `kill()`, and `run()` delegate to the
  wrapped `Agent`; `poll(timeout)` is exposed unchanged.
- **Manifest projection:** `manifest() -> ComponentManifest`, derived purely from
  what the agent announces/owns: `kind` (`atomic`; `composite` when it has
  declared children, escalating to `composite`; `model` for `ModelAgent`),
  `implements=("component-abi.v1",)`, `entry=EntryDescriptor(runtime=python,
  entrypoint=<allowlisted module:qualname>)`, `ports` from announced channels,
  `capabilities` from granted task names, `state=StateDescriptor(kind="sqlite",
  path=…)` only when a state store was granted, `children` from declared child
  refs. The projection is a pure function of agent state; two projections of the
  same state are byte-identical under canonical JSON.
- **Entry bridge:** a deterministic `run_once(directive_payload) -> Response`
  equivalent to the driver's round-trip for the same directive; this is the seam
  that lets a booted tree drive an `Agent` through the existing entry convention
  without the driver.

**Equivalence contract (item 1 exit):** for the same registered task, verifier,
envelope, and directive, `AgentComponent.run_once` produces a canonical-JSON
response byte-identical to `CbpDriver._roundtrip`, and the two agree on
`verified`/`error`. Proven in `tests/test_component_adapter.py`. No existing code
path is changed by this step.

## 4. Parent-provided registry component (item 2a: remove `_REGISTRY`)

The registry is already an object (`cbp/registry.py::Registry`) and already an
*instance* on each `Agent` (`agent.py:139`); the module global is only an ambient
hand-off. Replace the ambient hand-off with **explicit parent provision**:

1. Introduce a parent-owned **`ComponentCatalog`** wrapping `Registry` (passive
   catalog only — Law 10: records what/where, never decides). It is created by the
   driver/root and passed to a child at spawn/configure time; a child resolves its
   granted task/verifier names **only** from what its parent provisioned.
2. `register_callable`/`_resolve_entry` become a **test-only compatibility shim**
   (documented, deprecated) that seeds a module-scoped catalog used solely by
   tests and the `_seed_entry_from_source` replay path. The runtime path
   (`Agent._configure` and `CbpDriver`) reads from the parent-provisioned catalog.
3. **Fail-closed:** resolution of an unprovisioned name refuses with the existing
   error path; there is no ambient fallback in production.

**Equivalence contract (item 2a exit):** the full suite (1680) stays green; the
convention guard asserts no runtime read of the module global remains; a fresh
child cannot resolve a name its parent did not grant (a new negative test).

## 5. Parent-declared children (item 2b: remove `_child_class_for`)

Replace the static `kind`→class map with **parent declaration**:

- A parent declares its child kinds (e.g. a `dict[str, type[Agent]]`) in its own
  configuration and passes the declaration with the spawn directive/context.
- `_spawn` resolves the child class from the parent's declaration map. An
  undeclared kind **fails closed** — the current silent default to base `Agent` is
  replaced by an explicit refusal, because "unknown kind silently becomes a
  generic agent" is not a safe default (Law 7/8).
- Built-in domain agents (`store`, `bills`, `model`) are registered as
  declarations by the code that constructs the tree, not by `Agent` itself.

**Equivalence contract (item 2b exit):** tree topology and behavior are unchanged
for the declared kinds (tests that spawn `store`/`bills`/`model` stay green); a new
negative proves an undeclared kind refuses. **This is the highest-risk slice**
(§9) and lands only after §3/§4 are green and reviewed.

## 6. `inproc`-only enforcement (item 6)

- Make the backend explicit: a single `inproc` backend is the only admitted
  execution mode for the reference tree. Any non-`inproc` backend name fails
  closed with a clear refusal (never a silent downgrade or upgrade).
- `component_boot(allow_subprocess=True)` is **not** a backend of the reference
  tree; it stays an explicitly opt-in isolation path with its existing honest
  scope (`component_process.py`). Document the distinction so the two are never
  conflated.
- Proven by a test: an unknown/non-`inproc` backend refuses; the default path is
  in-process.

## 7. Staged migration (each step green, reviewed, committed)

Adapter-first, no big-bang (SPEC-0002 §9). Each step is its own commit and keeps
the suite green; nothing is removed until its replacement is proven equivalent.

1. **S1 — adapter (additive).** `component_adapter.py` + `tests/test_component_adapter.py`.
   No behavior change. *Exit: new tests + 1680 green.*
2. **S2 — catalog seam (additive).** `ComponentCatalog` + parent provisioning, with
   the global shim still present. *Exit: suite green; negative unprovisioned-name test.*
3. **S3 — cut the runtime over.** `Agent._configure`/`CbpDriver` read the
   parent-provisioned catalog; the global becomes test-only. *Exit: suite green;
   convention guard forbids runtime use of the global.*
4. **S4 — declared children.** Parent declaration map replaces `_child_class_for`;
   undeclared kind refuses. *Exit: suite green; negative test; map deleted.*
5. **S5 — backend enforcement + doc/status.** Explicit `inproc`-only refusal; update
   SPEC-0002 status/checkboxes and HANDOFF. *Exit: full verification block green.*

## 8. Acceptance mapping (SPEC-0002 §12)

- `component.v1` / `network.v1` / `review.v1` exist and are additive — **met**
  (`contracts/component.py`, `network.py`, `contracts/review.py`).
- `Agent` adapter satisfies the `Component` contract; no component-semantics test
  regresses — **S1 + S3**, equivalence tests.
- No module-level registry global and no central child-class map remain — **S3 + S4**.
- Identical `network.v1` schedules/re-executes identically — existing network
  suite (14/14) + holdout (operator-authored, SPEC-0019).
- Every response carries `confidence`; sub-threshold enters the Review queue —
  **met** (delivered earlier).
- Only `inproc` enabled; others fail closed — **S5**.

## 9. Risks and fail-closed rules

- **Highest risk:** the adapter or catalog cut-over subtly changing
  verification/replay semantics. Mitigation: equivalence tests (canonical-JSON
  equality) must pass **before** any old path is removed; each stage is
  independently revertible.
- **No ambient resolution.** After S3, production resolution never falls back to a
  module global; absence is an explicit refusal.
- **No silent default child class.** After S4, an undeclared kind refuses.
- **Idempotence.** The catalog is a passive catalog (no writes on resolve); the
  adapter projection is pure, so repeated calls are byte-identical.
- **Scope discipline:** any needed change to `component.v1` is additive-only
  (`component.v2` otherwise); the ABI is consumed and frozen.

## 10. Open decisions for operator review

1. **Location/name** of the runtime `Component` contract: reuse the frozen
   `component.v1` + SPEC-0012 lifecycle (recommended), or add a thin
   `contracts/component_runtime.py` Protocol. Recommendation: **reuse**, no new
   version.
2. **Fate of the global shim:** keep a documented test-only shim (recommended, to
   avoid rewriting ~30 test call sites in one risky commit) or migrate all tests
   to `CbpDriver.register` first. Recommendation: **shim now, migrate tests over S2/S3.**
3. **Undeclared-kind behavior:** refuse (recommended, Law 7/8) versus keep the
   silent base-`Agent` default. Recommendation: **refuse**, with an explicit
   declaration for the base case if needed.

## 11. Operator gates (unchanged)

The L3 flip, real host provisioning/enrollment, the operator signing key
(including re-signing composition/umbrella locks), and any public publish remain
operator acts. This design crosses none of them.


## 12. Implementation progress (recorded from git; do not trust prose hashes)

Delivered under L1, each a green, committed unit (re-verify with the
`HANDOFF.md` block; the suite grew 1680 → 1708):

- **S1 — adapter (additive).** `src/agent_centric/cbp/component_adapter.py`
  (`AgentComponent`: lifecycle passthrough, entry bridge `run_once`, pure
  `component.v1` manifest projection) + `tests/test_component_adapter.py`
  (equivalence to the `CbpDriver` round-trip). No existing path changed.
- **S2 — catalog seam (additive).** `src/agent_centric/cbp/component_catalog.py`
  (`ComponentCatalog`, passive) + `RegistryEntry.from_callable` +
  `AgentConfig.catalog`; an agent given a catalog resolves **only** from it and
  an absent name fails closed. `tests/test_component_catalog.py`.
- **S3a — runtime cut-over.** `CbpDriver` owns the catalog and provisions it to
  the root; spawned children inherit it; `_configure` and `_replay_run` read the
  catalog. The module global is now a **test/authoring shim**, snapshotted once
  at driver construction (documented bridge).
- **S4 — parent-declared children.** The central `_child_class_for` map is
  **deleted**; `Agent.declare_child_kind` is provisioned by the tree builder
  (`CbpDriver` declares `store`/`bills`/`model`), children inherit declarations,
  and an undeclared kind fails closed **before** any socket bind.
  `tests/test_declared_children.py`.
- **S5 — inproc-only backend.** `ADMITTED_BACKENDS = {"inproc"}`;
  `CbpDriver(backend=...)` refuses any other backend
  (`BackendNotAdmitted`). `tests/test_cbp_backend.py`.

**S3b — delivered.** The module-level `_REGISTRY`/`_resolve_entry`/
`register_callable` shim is **deleted**; `Agent._catalog_entry` resolves only
from the parent-provisioned catalog (fail-closed when absent); the package no
longer exports `register_callable`; `CbpDriver` owns a fresh catalog;
`replay_session` provisions the replay tree's catalog; and `replay_ledger`
seeds the fresh driver's catalog from the ledger manifest. All test/authoring
call sites migrated to `CbpDriver.register` or an explicit `ComponentCatalog`.
A guard test asserts no module-level registry global remains.
