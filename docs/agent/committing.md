# docs/agent — commit and push (Law 13)

> **Commit often; push often; no permission is needed.** Version control is a
> correctness tool: small, frequent, descriptive commits and frequent pushes
> keep work recoverable and the record honest.

## The loop

1. Make one coherent change.
2. Let the pre-commit hook check it (or run the fast checks):
   ```sh
   uv run ruff check .
   uv run mypy src
   uv run pytest -p no:cacheprovider -q tests/test_agent_conventions.py tests/test_repo_home_paths.py
   ```
3. `git add` the intended files; `git commit -m "type(scope): what and why"`.
4. `git push` — do not wait to be asked. `main` is the shared line.

## Guardrails are sacred

- **Never** bypass a hook with `--no-verify`; if a hook fails, fix the cause and
  commit again. Hooks: [`.pre-commit-config.yaml`](../../.pre-commit-config.yaml).
- CI is the isolated gate:
  [`.github/workflows/gates.yml`](../../.github/workflows/gates.yml).
- Never commit secrets. Never force-push `main` unless the old line is preserved
  elsewhere (as `agent-manager-version` preserves the Manager line).

A commit message states what and why, and reports the checks run. See
`PRINCIPLES.md` Law 13 and the on-demand skill
[`.agents/skills/commit-and-push/SKILL.md`](../../.agents/skills/commit-and-push/SKILL.md).
