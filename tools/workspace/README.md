# tools/workspace/ — workspace-level IDE hygiene (canonical copy)

`cbp/` (the workspace root) is **not** a git repo, so the files Zed actually
reads there are local-only. This directory is the **versioned canonical copy**, so
the convention survives and can be reviewed.

- `pyrightconfig.workspace.json` — the workspace-root basedpyright config. Zed
  launches basedpyright with cwd = the workspace root, where it reads **only** the
  root config (nested per-repo configs are ignored). Install as
  `<workspace>/pyrightconfig.json`.
- `check-pyright.sh` — deterministic check; install as
  `<workspace>/scripts/check-pyright.sh` and run with `CBP_WORKSPACE=<workspace>`
  (when installed at that path it resolves the root automatically).

Install (operator):

```sh
WS=/path/to/cbp
cp tools/workspace/pyrightconfig.workspace.json "$WS/pyrightconfig.json"
install -m 0755 tools/workspace/check-pyright.sh "$WS/scripts/check-pyright.sh"
CBP_WORKSPACE="$WS" "$WS/scripts/check-pyright.sh"   # must print 0/0/0
```

Hard rule (workspace `AGENTS.md`): run `check-pyright.sh` as part of every
verification and keep the tray at **0 errors, 0 warnings, 0 notes**. Fix the
cause; never suppress a real diagnostic.
