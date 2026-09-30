# docs/agent — test authority (Law 12)

> **The agent MAY run any tests, at any level.** Running the suite is expected
> as part of correctness work. The operator retains authority of record for a
> mission-critical release.

## Who may run what

| Actor | May run tests? | Authority of record for release |
| --- | --- | --- |
| The authoring agent | yes | no — corroborating evidence |
| The operator | yes | yes |
| CI / isolated validator | yes | yes |

## Obligations

1. Report results **faithfully** — never overstate a pass, never hide a failure.
2. Distinguish the agent's run from the authoritative gate: the operator's run
   and CI remain the record of truth.
3. Treat a failure as first-class (Law 8): surface it immediately with the exact
   command and output.
4. Keep the suite green; fix any regression you caused before handoff.

## Commands

```sh
uv run ruff check .
uv run mypy src
uv run pytest -p no:cacheprovider
uv run agent-centric cbp-check     # deterministic readiness gate
```

See `PRINCIPLES.md` Law 12, the on-demand skill
[`.agents/skills/test-authority/SKILL.md`](../../.agents/skills/test-authority/SKILL.md),
and [`verification.md`](verification.md).
