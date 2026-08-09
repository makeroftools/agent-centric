# Meta-Harness

**A deterministic control plane for governed, verifiable agents.**

Meta-Harness is a minimal, local-first, in-process core that lets a control
plane — the **Agent Manager** — register agent components, execute a task under
a strict resource envelope, record a full **durable, append-only audit
trajectory**, and mediate every tool and model call. Agents can only *request*
capabilities; the Manager is the sole authority that grants, executes, records,
and verifies.

This is the **v0.25 preliminary release** — a minimal but complete harness,
built across Volleys 001–025. It reflects what exists in the codebase, not an
aspirational platform.

```
Zed (Agent Panel) ──ACP──▶ meta-harness-acp (adapter) ──▶ AgentManager ──▶ agents / tools / MCP
```

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

## What v0.25 includes

| Area | What's implemented |
| --- | --- |
| **Manager** | Deterministic `AgentManager`: register, select (by name or capability), run, summarise, replay. |
| **Contracts** | Versioned, strongly-typed contracts for tasks, results, trajectories, tools, pipelines, parallel, policy, model, summary, replay, critical path, bills, workspace, email, and bills-registry. |
| **Registry** | In-process, deterministic agent registry with capability-based selection. |
| **Tools** | Local tools (`to_upper`, `add`, `bill_total`) plus a Manager-mediated **MCP adapter**, **allowlisted workspace tools**, **read-only email tools**, and **bills-registry tools**; grants and policy enforced by the Manager. |
| **Model path** | Mediated `llm_complete` tool; deterministic stub by default; an optional, hardened real-provider path. |
| **Composition** | Sequential pipelines, parallel fan-out/join, and nested sequential-as-parallel — all Manager-orchestrated. |
| **Governance** | Policy (allow/deny), hard resource envelopes, cooperative cancellation, per-step budgets. |
| **Isolation** | Optional subprocess execution; silent-hang bounded, forced-kill recorded, no zombies. |
| **Observability** | Deterministic trajectory summary, replay verification, and read-only critical-path (CPM) analysis. |
| **Specialty agents** | A **bills** agent, a **workspace** agent, a **read-only email** agent, and a **bills-registry + calendar** agent (deterministic agenda projection). |
| **Operator CLI** | `meta-harness` with `run`, `summarise`, and `replay-verify`. |
| **Editor bridge** | Thin ACP adapter (`meta-harness-acp`) so Meta-Harness runs as an External Agent in Zed. |

Full history and guarantees live in [`STATUS.md`](STATUS.md) and
[`KERNEL.md`](KERNEL.md).

---

## What it is not

These are **explicit non-goals** for v0.21 (see also [`KERNEL.md`](KERNEL.md)):

- Messaging fabric (ZeroMQ / NATS / etc.) or any network transport.
- Multi-tenancy, distribution, or cloud concerns.
- A2A agent mesh or a discovery marketplace.
- A Rust core or native reimplementation.
- Deep workflow graphs, cyclic/dynamic workflows, or durable workflow engines.
- Bit-exact replay of live non-deterministic model calls (replay applies to
  deterministic configurations only).
- A UI or operator dashboard.
- Full coding-agent parity in the editor bridge (see the Zed notes below).

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
```

The `meta-harness` CLI is **local-only and fail-closed** — it never starts a
network service, never reads credentials, and exits non-zero on any failure.

---

## Use from Zed (ACP)

Meta-Harness can appear as an **External Agent** in Zed via the Agent Client
Protocol (ACP). The adapter (`meta_harness.acp`) is a client-facing transport
only: every prompt is routed through the Manager, and no ACP path can produce a
verified success that bypasses it.

### 1. Configure the agent server

Add an entry to Zed's `settings.json` (Zed → Settings → Agents). The command
must match the real console script name, `meta-harness-acp`:

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

If `uv run` is not desirable, point directly at the console script in the venv
(the name is `meta-harness-acp`, same as above).

### 2. Start a thread

Open the **Agents Panel** in Zed (Agent Panel), choose **Meta-Harness** as the
agent, and start a new thread. Each thread opens an ACP session; one process
owns one Manager that is shared across sessions in that process.

### 3. Try these prompts (v0.21 routing)

Prompts map to deterministic demo tasks. The expected outputs below were
produced by a live run:

| You type | Manager routing | Verified output |
| --- | --- | --- |
| `hello world` | reverse (default) | `dlrow olleh` |
| `upper hello` | mediated `to_upper` tool | `HELLO` |
| `counter hello l` | counter agent | `2` |
| `model hello` | stub model (`llm_complete`) | `stub response` |

Each reply streams back the **verified output and its trajectory id** (or a
clear fail-closed message).

### v1 limits (honest)

- **Not a full coding agent.** No diffs, slash commands, nested sub-agents, or
  rich following — this is a thin governed harness bridge.
- **Cancel-during-run is limited.** `session/cancel` is tracked per session, but
  the synchronous Manager run is not pre-emptible mid-turn; a cancel arriving
  before the next prompt causes it to be refused at start.
- **The Manager still governs.** No filesystem, shell, or arbitrary tool access
  is exposed from the ACP layer — only what the Manager grants for that task.
  Model/MCP output stays untrusted until verified.

---

## Operator path

`meta-harness` is a local, fail-closed operator CLI.

### Commands

| Command | Purpose |
| --- | --- |
| `run` | Run the deterministic demo task set and persist trajectories. |
| `summarise <id>` | Print a trajectory's deterministic summary. |
| `replay-verify <id>` | Re-run the demo task and verify equivalence. |

`--store <dir>` (a global option, before the command) selects the durable
trajectory store. When omitted it defaults to `examples/.trajectories`.

```sh
uv run meta-harness --store examples/.trajectories run
```

Output:

```
demo-counter: VERIFIED output=4 trajectory_id=demo-counter#0
demo-reverse: VERIFIED output='cirtnec-tnega' trajectory_id=demo-reverse#1
demo-tool: VERIFIED output='MEDIATED TOOLS' trajectory_id=demo-tool#2
demo-model: VERIFIED output='stub response' trajectory_id=demo-model#3
demo-pipeline: VERIFIED output='NOITISOPMOC LAITNEUQES' trajectory_id=demo-pipeline#4
demo-bills: VERIFIED output={'line_subtotal_cents': 2750, 'discount_cents': 275, 'taxable_amount_cents': 2475, 'tax_cents': 124, 'grand_total_cents': 2599} trajectory_id=demo-bills#5
demo-workspace: VERIFIED output={'relative_path': 'invoices/note.txt', 'kind': 'file', 'content': 'hello workspace'} trajectory_id=demo-workspace#6
demo-email: VERIFIED output={'folder': 'INBOX', 'limit': 10, 'count': 1, 'messages': [{'id': 'm1', 'folder': 'INBOX', 'subject': 'Hello', 'from_address': 'a@example.test', 'date': '2026-08-01'}]} trajectory_id=demo-email#7
demo-bills-calendar: VERIFIED output={'from_date': '2026-09-01', 'to_date': '2026-09-30', 'include_paid': False, 'count': 2, 'entries': [{'due_date': '2026-09-01', 'bill_id': 'b1', 'vendor': 'NetCo', 'amount_cents': 3000, 'status': 'due'}, {'due_date': '2026-09-10', 'bill_id': 'b3', 'vendor': 'PowerCo', 'amount_cents': 5000, 'status': 'due'}], 'total_outstanding_cents': 8000} trajectory_id=demo-bills-calendar#8
```

Trajectories are durable, append-only JSON-lines files (hex-named `.jsonl`)
under the store directory.

### One "inspect a run" flow

```sh
# 1. run the demo set and note a trajectory id
uv run meta-harness run
# 2. summarise a run by id
uv run meta-harness summarise demo-reverse#1
# 3. re-run it and verify the fresh trajectory is equivalent
uv run meta-harness replay-verify demo-reverse#1
```

`summarise` and `replay-verify` are fail-closed: a missing id exits non-zero
with a clear message.

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

## Extending (short)

The kernel is designed to grow via **adapters and backends**, not by changing
Manager semantics. Start with the versioned contracts in
[`meta_harness/contracts/`](src/meta_harness/contracts) and the freeze note in
[`KERNEL.md`](KERNEL.md).

Register an agent (a manifest + a callable factory) and grant a tool on the
task:

```python
from meta_harness import AgentManager
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion

manager = AgentManager()
manager.register(AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="meta_harness.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
))

# Grant the "to_upper" tool for this task; the Manager mediates and records it.
task = TaskSpecification(
    version=TaskSpecVersion.V3,
    task_id="upper-demo",
    agent_name="case_tool",
    payload={"text": "hello"},
    envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    granted_tools=("to_upper",),
)

outcome = manager.run(task)
print(outcome.result.output if outcome.result else outcome.failure)  # HELLO
```

All of the code above is real and smoke-checked against the public API.

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
STATUS.md              Volley history 001–025 + correctness evidence
src/meta_harness/
  __init__.py          Public surface
  contracts/           Versioned, strongly-typed contracts
  agents/              Thin agent interface + built-in agents
  control_plane/       AgentManager, registry, tools, verifier,
                       trajectory store, backends, summary, replay, CPM
  providers/           Stub + optional real model providers
  acp.py               Thin ACP adapter (Zed bridge)
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

## Versioning / preliminary-release status

The package is versioned as a kernel milestone aligned with volley depth:
**`0.25.0`** (25 volleys delivered). This is a **preliminary release** — the
v0.25 kernel is complete but APIs may evolve before a stable 1.0. See
[`KERNEL.md`](KERNEL.md) for the versioning scheme and freeze boundaries.

---

## The bills-registry + calendar agent (Volley 025)

A deterministic **bills-registry + calendar** agent maintains a canonical local
bills registry in the allowlisted workspace and answers **what is due when**.
The registry lives at `bills/registry.json` (an allowlisted path,
`bills_registry.v1`): each record has `id`, `vendor`, `amount_cents` (integer
cents), an ISO `due_date`, and optional `status` (`due`/`paid`/`skipped`) and
`category`. Projection is purely deterministic — no model is involved. By
default only unpaid bills (`due`/`skipped`) appear; `include_paid=True` also
includes `paid`. Entries are ordered by due date then bill id, with a total
outstanding sum. Recurrence is omitted in v1 (documented as future);
mark-paid/upsert are deferred to a later volley.

Example registry:

```json
{
  "version": "bills_registry.v1",
  "bills": [
    {"id": "b1", "vendor": "NetCo", "amount_cents": 3000, "due_date": "2026-09-01", "status": "due"},
    {"id": "b2", "vendor": "WaterCo", "amount_cents": 2000, "due_date": "2026-09-05", "status": "paid"}
  ]
}
```

Example agenda request (what is due in September, excluding paid):

```python
from meta_harness import AgentManager
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion

manager = AgentManager()
manager.register(AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills_registry",
    entry_point="meta_harness.agents.bills_registry:create_bills_registry_agent",
    description="Reads the bills registry and projects a deterministic agenda.",
))

task = TaskSpecification(
    version=TaskSpecVersion.V5,
    task_id="bills-cal",
    agent_name="bills_registry",
    payload={
        "operation": "calendar",
        "registry": {"version": "bills_registry.v1", "bills": [
            {"id": "b1", "vendor": "NetCo", "amount_cents": 3000, "due_date": "2026-09-01", "status": "due"},
        ]},
        "from_date": "2026-09-01",
        "to_date": "2026-09-30",
        "include_paid": False,
    },
    envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    granted_tools=("bills_calendar",),
)

outcome = manager.run(task)
print(outcome.result.output if outcome.result else outcome.failure)  # total=3000
```

---

## The read-only email specialty agent (Volley 024)

A deterministic **read-only email** agent lists or fetches messages through the
Manager's mediated `email_list` / `email_fetch` tools. It is strictly read-only:
no send, delete, move, or mailbox-destructive operations. Verification is real
(structural consistency): the output must match the requested operation, a list
must be bounded by its limit, and a fetch must echo the requested message id.

**Sensitivity note — email is sensitive.** Credentials are read from environment
variables (host / user / password or app token) and **never** appear in a task
payload, an agent prompt, or a trajectory. Errors from the real gateway are
redacted of any known secret before they can be recorded. A trajectory records
the message metadata (id, folder, subject, from, date) exactly as structured
results; it does not dump full bodies into step descriptions.

**Configuration (real IMAP, opt-in and off by default):**

| Env var | Purpose |
| --- | --- |
| `META_HARNESS_EMAIL_HOST` | IMAP host (e.g. a Mail-in-a-Box mailbox). |
| `META_HARNESS_EMAIL_USER` | Mailbox user. |
| `META_HARNESS_EMAIL_PASSWORD` | Mailbox password or app token. |
| `META_HARNESS_EMAIL_ENABLED` | Set to truthy (e.g. `1`) to opt in. |
| `META_HARNESS_EMAIL_FOLDERS` | Optional comma-separated folder allowlist. |

Missing host/user/password raises a clear fail-closed error; no network call is
made unless explicitly enabled, so CI never touches a mailbox. All tests use the
deterministic `FakeEmailGateway`.

**Non-goals (explicit):** no SMTP send, no delete/move/flag-as-destructive bulk
ops, no full mail-client UX, no PDF/bill auto-ingest from attachments, no cloud
SaaS mail provider as primary.

---

## Specialty agents: bills (022) and workspace (023)

### Bills (Volley 022)

A narrow, deterministic **bills** agent accepts a structured bill in and produces
deterministic totals out. Money math is integer-only (minor units / cents) with
an explicit half-up rounding rule, so totals are exact and replayable — no cloud
model is involved. Verification is real: the Manager independently recomputes
the expected totals from the payload and rejects bad or missing data.

```python
from meta_harness import AgentManager
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion

manager = AgentManager()
manager.register(AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="bills",
    entry_point="meta_harness.agents.bills:create_bills_agent",
    description="Computes deterministic totals for a structured bill.",
))

task = TaskSpecification(
    version=TaskSpecVersion.V5,
    task_id="bill-demo",
    agent_name="bills",
    payload={
        "lines": [
            {"description": "widget", "quantity": 2, "unit_price_cents": 1000},
            {"description": "gadget", "quantity": 3, "unit_price_cents": 250},
        ],
        "discount_bps": 1000,
        "tax_bps": 500,
    },
    envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    granted_tools=("bill_total",),
)

outcome = manager.run(task)
print(outcome.result.output if outcome.result else outcome.failure)  # grand_total_cents=2599
```

A malformed bill (empty/missing `lines`, non-integer or negative quantities or
prices, out-of-range rates) is rejected explicitly and fails closed — it never
produces a verified result.

### Workspace (Volley 023)

A deterministic **workspace** agent operates on a local, allowlisted workspace
through the Manager. The workspace is a root directory plus an explicit
`WorkspaceLayout` allowlist of relative paths; the mediated file tools
(`list_workspace`, `read_workspace_file`, `write_workspace_file`,
`create_workspace_dir`) reject any path not on the allowlist (fail-closed). No
deletion, rename, or arbitrary traversal is possible, so an agent never gains
broad filesystem powers. Verification is real: the Manager recomputes the
expected result (operation, path, and for writes content) from the payload.

```python
from meta_harness import AgentManager
from meta_harness.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from meta_harness.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from meta_harness.contracts.workspace import WorkspaceLayout
from meta_harness.control_plane.tools import ToolRegistry
from meta_harness.control_plane.workspace import Workspace, register_workspace_tools

workspace = Workspace("ws", WorkspaceLayout(files=("invoices/note.txt",), directories=("invoices",)))
tools = ToolRegistry()
register_workspace_tools(tools, workspace)
workspace.create_workspace_dir("invoices")

manager = AgentManager(tools=tools)
manager.register(AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="workspace",
    entry_point="meta_harness.agents.workspace:create_workspace_agent",
    description="Performs an allowlisted workspace operation via mediated file tools.",
))

task = TaskSpecification(
    version=TaskSpecVersion.V5,
    task_id="ws-demo",
    agent_name="workspace",
    payload={"operation": "write", "relative_path": "invoices/note.txt", "content": "hello workspace"},
    envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
    granted_tools=("write_workspace_file",),
)

outcome = manager.run(task)
print(outcome.result.output if outcome.result else outcome.failure)  # content='hello workspace'
```

A path outside the allowlist, or a failed/disallowed workspace operation, fails
closed and never produces a verified result.

---

## Roadmap posture

**Use first; enhance on demand.** The harness is intentionally minimal. New
capabilities will be added only when driven by demonstrated need, and will
prefer adapters and backends over changing Manager semantics. The planned
specialty agent sequence was bills → workspace → email; with the read-only email
agent now delivered, the next step is any follow-up the architect directs under
the same fail-closed rules (candidate: PDF/bill auto-ingest from email
attachments, explicitly deferred beyond this volley).

---

## License

MPL-2.0 — see [`LICENSE`](LICENSE).