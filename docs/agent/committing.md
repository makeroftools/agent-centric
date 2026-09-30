# Committing — Law 13 (commit and push continuously)

Commit often; push often; **no permission is needed**. Small, frequent commits
keep work recoverable and the record honest.

## The loop

1. Make one coherent change.
2. Let the pre-commit hook check it (or run the fast checks yourself):
   ```sh
   uv run ruff check .
   uv run mypy src
   uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py tests/test_repo_home_paths.py
   ```
3. `git add` the intended files and commit with a descriptive message:
   `type(scope): what and why`.
4. `git push` — do not wait to be asked.

## Guardrails are sacred

- The pre-commit hook ([`.pre-commit-config.yaml`](../../.pre-commit-config.yaml))
  runs ruff, mypy, and the convention + home-path guards.
- CI ([`.github/workflows/gates.yml`](../../.github/workflows/gates.yml)) runs the
  full suite.
- **Never** commit with `--no-verify`. If a hook fails, fix the cause and commit
  again.
- Never commit secrets. Never force-push `main` unless the old line is preserved
  elsewhere (as `agent-manager-version` preserves the former Manager line).

## Install the hook (fresh clone)

```sh
uv sync --extra dev
uv run pre-commit install
```

## Commit messages

State what changed and why; mention the checks run in the body. Keep the subject
to one line.
