# Testing — Law 12 (test authority)

**The authoring agent is not the test authority.** It must never run `pytest`
(or any test runner) and must never claim a pass. Authority is held by the
operator, and at L2+ by an **isolated validator** that is a different process
from the one that wrote the code.

## What the authoring agent may run

Static checks the operator permits — these catch mistakes before handoff:

```sh
uv run ruff check .
uv run mypy src
uv run agent-centric fbp-check     # deterministic readiness gate
```

`fbp-check` is a self-contained deterministic gate (it does not import the test
suite); it is safe and encouraged at L1+.

## Who runs the suite

| Level | Suite runner |
| --- | --- |
| L0–L1 | the **operator**, manually |
| L2 | an **isolated validator** (separate job/process) |
| L3 | the **isolated validator**, gating merge |

"Automation with checks" (Q4 override) means the suite may be run by CI and by
the L2/L3 validator — never by the authoring agent, and never reported as the
agent's own result.

## Handoff protocol

When offering work, report precisely:

1. **Static checks run**: exact commands and their output.
2. **Suite to run**: the exact command, e.g. `uv run pytest -p no:cacheprovider`.
3. **Expected result**: what green looks like.
4. **Not claimed**: state plainly that you did not run the suite.

Never write "all tests pass." Only the operator (or the validator's recorded
result) may say that. This is the same discipline as the correctness spine:
a self-claim is not evidence.
