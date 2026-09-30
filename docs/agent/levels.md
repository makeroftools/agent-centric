# docs/agent — operating levels (the factory mode)

The active mode is `factory.level` in
[`.agentfactory.toml`](../../.agentfactory.toml). It is the **single source of
truth**; humans and agents both read it, and guards/CI assert it. Changing the
level is a deliberate, reviewed edit to that file — never a side effect of a
prompt.

A mode sets three axes:

- **autonomy** — what the agent may do on its own.
- **gates** — the hard checks that must pass.
- **review** — where a human is required.

| Level | Name | Autonomy | Review | Suite | Merge |
| --- | --- | --- | --- | --- | --- |
| L0 | harness-only | assist | human-all | agent may | no |
| L1 | assisted | author | human-all | agent may | no |
| L2 | light-factory | spec-driven | human-gate | agent + validator | no |
| L3 | dark-factory | holdout | none-on-green | isolated validator | gated |

**L1 is the active default.** The agent authors full changes; a human reviews
every one. **L3 is declared but gated** (`status = "declared-gated"`); selecting
it is a hard error until the isolated validator exists.

## Per-path floors

A path may never operate below its floor, regardless of the global level
(`src/**` has `min_level = "L1"`). Mission-critical code never drops below
reviewed authoring.

## Rules

1. Never act above the active level without the operator changing it first.
2. `on_missing_gate = "fail-closed"`: if a gate named by the level is missing,
   refuse rather than proceed.
3. The level changes which gates and review apply; it **never suspends a law** in
   [`PRINCIPLES.md`](../../PRINCIPLES.md).

See the on-demand skill
[`.agents/skills/operating-levels/SKILL.md`](../../.agents/skills/operating-levels/SKILL.md)
and the spec [`specs/agent-conventions.md`](../../specs/agent-conventions.md).
