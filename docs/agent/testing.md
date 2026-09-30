# Testing — test authority (Law 12)

**The agent may run any tests.** The suite is a tool for correctness and the
agent is expected to use it. What remains is honesty, and authority of record.

## Who may run what

| Actor | May run the suite? | Authority of record for release |
| --- | --- | --- |
| The authoring agent | yes | no — corroborating evidence |
| The operator | yes | yes |
| CI / isolated validator | yes | yes |

## Commands

```sh
uv run ruff check .
uv run mypy src
uv run pytest -p no:cacheprovider
uv run agent-centric fbp-check     # deterministic readiness gate
```

## Reporting

When offering work, report precisely:

1. The exact commands run and their output.
2. Any failure, surfaced immediately (Law 8) with the command and output.
3. What remains unverified, if anything.

Never overstate a pass; never hide a failure. The agent's run is evidence; the
operator's or CI's run is the record.
