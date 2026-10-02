---
name: operating-levels
description: Use when you need to know what the active mode authorizes (autonomy, gates, review), or before acting above the current level. Reads .agentfactory.toml.
license: MPL-2.0
compatibility: opencode
metadata:
  component: operating-levels
  determinism: deterministic
---

# Operating levels (the factory mode)

The active mode is `factory.level` in [`.agentfactory.toml`](../../../.agentfactory.toml).
A mode sets three axes: *autonomy* (what the agent may do), *gates* (hard
checks), and *review* (where a human is required).

| Level | Name | Autonomy | Review | Suite | Merge |
| --- | --- | --- | --- | --- | --- |
| L0 | harness-only | assist | human-all | agent may | no |
| L1 | assisted | author | human-all | agent may | no |
| L2 | light-factory | spec-driven | human-gate | agent + validator | no |
| L3 | dark-factory | holdout | none-on-green | isolated validator | gated |

- **L2 (light-factory) is the active mode**, raised by the operator from the L1 default; the suite is a release gate and a human holds the review gate.
- **L3 is declared but gated** (`status = "declared-gated"`); selecting it is a hard error until the isolated validator exists.
- A path may never operate below its floor (`src/**` has `min_level = "L1"`).

Never act above the active level. Changing the level is a reviewed edit to
`.agentfactory.toml`, not a prompt.

## Deterministic termination

The convention guard parses `.agentfactory.toml` and asserts the level is valid
and L3 is not enabled.

## Non-determinism (suspect)

Judging which level a task warrants is the suspect step; the declared level in
`.agentfactory.toml` is the deterministic contract.
