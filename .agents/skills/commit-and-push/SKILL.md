---
name: commit-and-push
description: Use before and after every change. Law 13 - commit each coherent unit and push often, no permission needed; pre-commit hooks and CI are the guardrails and must never be bypassed with --no-verify.
license: MPL-2.0
compatibility: opencode
metadata:
  component: commit-and-push
  determinism: deterministic
  terminates-in: git push
---

# Commit and push continuously (Law 13)

Commit often; push often; **no permission is needed**.

## The loop

1. Make one coherent change.
2. Let the pre-commit hook check it (or run the fast checks):
   ```sh
   uv run ruff check .
   uv run mypy src
   uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py tests/test_repo_home_paths.py
   ```
3. `git add` the intended files; `git commit -m "type(scope): what and why"`.
4. `git push` — do not wait to be asked.

## Guardrails are sacred

- Pre-commit: [`.pre-commit-config.yaml`](../../../.pre-commit-config.yaml).
- CI: [`.github/workflows/gates.yml`](../../../.github/workflows/gates.yml).
- **Never** `--no-verify`. If a hook fails, fix the cause and commit again.
- Never commit secrets. Never force-push `main` unless the old line is preserved
  (as `agent-manager-version` preserves the Manager line).

Fresh clone: `uv sync --extra dev && uv run pre-commit install`.

## Deterministic termination

The pre-commit hook and CI are the deterministic gates; a green hook is required
before the commit is accepted.

## Non-determinism (suspect)

Choosing what to commit and writing the message are the suspect steps; the
hooks and the resulting clean tree are the deterministic check.
