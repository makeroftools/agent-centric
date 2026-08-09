# Meta-Harness

**A deterministic control plane for governed, verifiable agents.**

Meta-Harness is a minimal, local-first, in-process core that lets a control
plane — the **Agent Manager** — register agent components, execute a task under
a strict resource envelope, record a full **durable, append-only audit
trajectory**, and mediate every tool and model call. Agents can only *request*
capabilities; the Manager is the sole authority that grants, executes, records,
and verifies.

This is the **v0.20 preliminary release** — a minimal but complete harness,
built across Volleys 001–020. It reflects what exists in the codebase, not an
aspirational platform.

---

## Why it exists

Autonomous agents are only useful if you can trust what they did. Meta-Harness
treats *correctness under autonomy* as the core problem:

- **No unverified success.** A task terminates in a verified result or an
  explicit, audited failure — there is no ambiguous third state.
- **Deterministic control plane.** Given identical inputs and a deterministic
  agent, the Manager reproduces the same trajectory and outcome.
- **Full auditability.** Every step, tool/model call, policy decision, resource
  use, and cancellation is recorded durably and reconstructible after restart.
- **Fail-closed by default.** Anything unexpected is an explicit, recorded
  failure — never a silent success.

The non-negotiable rules are recorded in [`PRINCIPLES.md`](PRINCIPLES.md), which
governs every decision in this repository.

---

## What v0.20 includes

| Area | What's implemented |
| --- | --- |
| **Manager** | Deterministic `AgentManager`: register, select (by name or capability), run, summarise, replay. |
| **Contracts** | Versioned, strongly-typed contracts for tasks, results, trajectories, tools, pipelines, parallel, policy, model, summary, replay, and critical path. |
| **Registry** | In-process, deterministic agent registry with capability-based selection. |
| **Tools** | Local tools (`to_upper`, `add`) plus a Manager-mediated **MCP adapter**; grants and policy enforced by the Manager. |
| **Model path** | Mediated `llm_complete` tool; deterministic stub by default; an optional, hardened real-provider path. |
| **Composition** | Sequential pipelines, parallel fan-out/join, and nested sequential-as-parallel — all Manager-orchestrated. |
| **Governance** | Policy (allow/deny), hard resource envelopes, cooperative cancellation, per-step budgets. |
| **Isolation** | Optional subprocess execution; silent-hang bounded, forced-kill recorded, no zombies. |
| **Observability** | Deterministic trajectory summary, replay verification, and read-only critical-path (CPM) analysis. |
| **Operator CLI** | `meta-harness` with `run`, `summarise`, and `replay-verify`. |

Full history and guarantees live in [`STATUS.md`](STATUS.md) and
[`KERNEL.md`](KERNEL.md).

---

## What it is not

These are **explicit non-goals** for v0.20 (see also [`KERNEL.md`](KERNEL.md)):

- Messaging fabric (ZeroMQ / NATS / etc.) or any network transport.
- Multi-tenancy, distribution, or cloud concerns.
- A2A agent mesh or a discovery marketplace.
- A Rust core or native reimplementation.
- Deep workflow graphs, cyclic/dynamic workflows, or durable workflow engines.
- Bit-exact replay of live non-deterministic model calls (replay applies to
  deterministic configurations only).
- A UI or operator dashboard.

These are deferred intentionally: the harness prefers adapters and backends over
changing Manager semantics.

---

## Quick start

Requires Python ≥ 3.13 and [uv](https://docs.astral.sh/uv/).

```sh
uv sync --extra dev       # install the package and dev dependencies
uv run pytest             # run the control-plane invariant tests
uv run ruff check .       # lint
uv run mypy               # type check the source package
```

Run the end-to-end demo or the operator CLI:

```sh
uv run python examples/demo.py
uv run meta-harness run          # run the deterministic demo task set
uv run meta-harness summarise demo-pipeline#4
uv run meta-harness replay-verify demo-pipeline#4
```

The `meta-harness` CLI is **local-only and fail-closed** — it never starts a
network service, never reads credentials, and exits non-zero on any failure.

---

## Core execution model

There are exactly two roles:

- **The Agent Manager** — the deterministic control plane. It owns authority:
  mediation, policy, resource envelopes, trajectory recording, verification, and
  cancellation. It never *is* an agent and never runs unchecked agent code in a
  privileged path.
- **The agent** — a thin, governed component. It takes a payload, a step budget,
  and a set of *granted* tools; it yields steps and tool requests; it finally
  returns an output that the Manager verifies.

A minimal single-agent run:

```python
from meta_harness import AgentManager
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion

manager = AgentManager()
manager.register(AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="meta_harness.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
))

task = TaskSpecification(
    version=TaskSpecVersion.V3,
    task_id="demo",
    agent_name="reverse",
    payload={"text": "agent-centric"},
    envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
)

outcome = manager.run(task)        # task selects an agent, payload, envelope, grants
if outcome.result is not None:     # only after the verification gate passed
    print(outcome.result.output)
else:
    print(outcome.failure)         # explicit, audited failure
```

**Agents never gain the ability to spawn or directly invoke one another.** All
composition is orchestrated by the Manager.

---

## Public surface

The public API is deliberate and minimal. Top-level exports include
`AgentManager`, the core contracts, the execution backends
(`InProcessBackend` / `SubprocessBackend`), the trajectory stores,
`summarise` / `replay` / `verify_replay`, and `analyse_critical_path`. The
package is marked typed via `py.typed`; sub-package `__init__` files define
explicit `__all__` and don't leak internal helpers.

- **Contracts** — `meta_harness.contracts` (task, result, trajectory, tool,
  pipeline, parallel, policy, model, summary, replay, critical-path, manifest,
  capability).
- **Manager & control plane** — `meta_harness.control_plane` (AgentManager,
  registry, tools, verifier, trajectory store, execution backends, summary,
  replay, CPM).
- **Agents** — `meta_harness.agents` (thin interface + built-in agents).
- **Providers** — `meta_harness.providers` (stub / failing stub / optional real).

---

## Correctness posture

- **Model and MCP outputs are untrusted until verified.** A tool returning data
  is never a verified result on its own; the mandatory verification gate still
  applies.
- **Verification is real, not a stub.** The default verifier re-derives the
  expected output from the task payload and compares it to the agent's output.
- **Resource bounds are hard**, not advisory: overall timeout, step limit, and
  optional per-step budget are enforced by the Manager.
- **Failure is first-class.** Every failure mode — verification, policy,
  envelope exhaustion, cancellation, tool denial, MCP/provider error, child
  crash, or store error — is an explicit, audited failure.
- **Real providers are opt-in.** CI and default operation use the deterministic
  stub only (no network, no credentials). Enabling a real provider requires
  explicit configuration and does not relax verification.
- **Replay is read-only.** Replay never mutates the stored audit record and
  applies only to deterministic configurations.

The correctness guarantees are demonstrated across the test suite
(`tests/`) and documented volley-by-volley in [`STATUS.md`](STATUS.md).

---

## Layout (simplified)

```
PRINCIPLES.md          Non-negotiable governing rules
KERNEL.md              v0 kernel freeze note (guarantees, out-of-scope, versioning)
STATUS.md              Volley history 001–020 + correctness evidence
src/meta_harness/
  __init__.py          Public surface
  contracts/           Versioned, strongly-typed contracts
  agents/              Thin agent interface + built-in agents
  control_plane/       AgentManager, registry, tools, verifier,
                       trajectory store, backends, summary, replay, CPM
  providers/           Stub + optional real model providers
tests/                 Invariant tests across every volley
examples/demo.py       End-to-end demonstration
```

See [`KERNEL.md`](KERNEL.md) for the authoritative freeze boundaries.

---

## Tool & model mediation

Agents access external capabilities **only through the Manager**. An agent
yields a `ToolRequest`; the Manager validates the grant, executes or rejects,
records the request and outcome as ordered steps, and sends a `ToolResult` back.
This applies identically to:

- **Local tools** — deterministic, side-effect-free functions.
- **MCP tools** — exposed through a thin Manager-mediated adapter
  (`McpToolAdapter`), never agent-direct; real servers are not required for CI
  (an in-process fake double is used in tests).
- **Model calls** — the `llm_complete` tool, mediated and recorded; output is
  untrusted until verified.

Policy can allow or deny any tool, agent, or capability. Tool calls consume the
task step budget. Enforcement stays entirely in the Manager.

---

## Use from Zed (ACP)

Meta-Harness can appear as an **External Agent** in Zed via the Agent Client
Protocol (ACP). The adapter (`meta_harness.acp`) is a client-facing transport
only: every prompt is routed through the Manager, and no ACP path can produce a
verified success that bypasses it.

Add an agent server to Zed's `settings.json` (Zed → Settings → Agents):

```json
{
  "agent_servers": {
    "Meta-Harness": {
      "type": "custom",
      "command": "uv",
      "args": ["run", "meta-harness-acp"]
    }
  }
}
```

Or launch it directly (e.g. for the Agent Panel thread):

```sh
uv run meta-harness-acp     # or: uv run python -m meta_harness.acp
```

What works in v1:

- **initialize** — honest, minimal capabilities (all optional features disabled).
- **session/new** — a new session id; one process owns one Manager.
- **session/prompt** — a prompt maps to a deterministic demo task (`reverse`,
  `upper`, `counter`, or the stub `model`) run by the Manager; the verified
  result (or a clear fail-closed failure) and trajectory id are streamed back.
- **session/cancel** — tracked per session. Mid-run cancellation of the
  synchronous Manager run is not pre-emptible in v1 (documented limitation).

No filesystem, shell, or arbitrary tool access is exposed from the ACP layer —
only what the Manager grants for that task.

---

## Versioning / preliminary-release status

The package is versioned as a kernel milestone aligned with volley depth:
**`0.21.0`** (21 volleys delivered). This is a **preliminary release** — the
v0.21 kernel is complete but APIs may evolve before a stable 1.0. See
[`KERNEL.md`](KERNEL.md) for the versioning scheme and freeze boundaries.

---

## Roadmap posture

**Use first; enhance on demand.** The harness is intentionally minimal. New
capabilities will be added only when driven by demonstrated need, and will
prefer adapters and backends over changing Manager semantics. There is no
standing roadmap of large features; the next step is whatever a real use-case
requires.

---

## License

MPL-2.0 — see [`LICENSE`](LICENSE).