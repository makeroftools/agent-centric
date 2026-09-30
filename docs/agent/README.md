# docs/agent — the convention layer

Progressive disclosure for coding agents: [`AGENTS.md`](../../AGENTS.md) is the
thin table of contents; these pages are the detail, loaded only when a task
needs them. Each page covers exactly one concern.

| Page | Read it when |
| --- | --- |
| [`laws.md`](laws.md) | You need the index of non-negotiable laws. |
| [`levels.md`](levels.md) | You need to know what the active mode authorizes. |
| [`editing.md`](editing.md) | You are about to change any file (Law 11). |
| [`testing.md`](testing.md) | You are about to validate work (Law 12). |
| [`committing.md`](committing.md) | You are about to commit or push (Law 13). |
| [`verification.md`](verification.md) | You are designing or judging a gate. |

## How this layer is enforced

Prose points; machines decide. The following are deterministic and may not be
argued with:

- [`opencode.json`](../../opencode.json) denies the `edit`/`write`/`patch` tools.
- [`tools/safe-replace.sh`](../../tools/safe-replace.sh) is the only sanctioned mutation primitive.
- [`tests/test_agent_conventions.py`](../../tests/test_agent_conventions.py) asserts this layer stays consistent.
- [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml) runs the gates in CI.
- [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml) runs the fast gates on every commit.

If prose here ever contradicts [`PRINCIPLES.md`](../../PRINCIPLES.md), the laws win.
