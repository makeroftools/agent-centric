---
name: test-authority
description: Use when validating work or deciding who may run tests. Law 12 - the agent may run any tests; report results faithfully; the operator's run and CI are the authority of record.
license: MPL-2.0
compatibility: opencode
metadata:
  component: test-authority
  determinism: deterministic
---

# Test authority (Law 12)

**The agent may run any tests.** Running the suite is expected as part of
correctness work. What remains is honesty and authority of record.

## Who may run what

| Actor | May run tests? | Authority of record for release |
| --- | --- | --- |
| The authoring agent | yes | no — corroborating evidence |
| The operator | yes | yes |
| CI / isolated validator | yes | yes |

## Commands

```sh
uv run ruff check .
uv run mypy src
uv run pytest -p no:cacheprovider
uv run agent-centric cbp-check     # deterministic readiness gate
```

## Reporting

1. State the exact commands run and their output.
2. Surface any failure immediately (Law 8) with the command and output.
3. State what remains unverified.
4. Never overstate a pass; never hide a failure.

## Deterministic termination

`uv run pytest -p no:cacheprovider` and `uv run agent-centric cbp-check` are the
deterministic gates; their exit codes are the truth.

## Non-determinism (suspect)

Test *selection* and interpretation are the suspect step; the pass/fail exit
code is deterministic. The operator's or CI's run is the record.
