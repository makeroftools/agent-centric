#!/usr/bin/env bash
#
# check-pyright.sh — keep the Zed/basedpyright tray clean.
#
# Zed launches basedpyright with cwd = the workspace root (cbp/), so it reads
# ONLY `cbp/pyrightconfig.json` (the per-repo configs are ignored at the root).
# This script runs the same analysis and fails unless it is zero *errors and*
# zero *warnings* for the three code repos.
#
# Usage:  scripts/check-pyright.sh          # check (exit 0 iff clean)
#         scripts/check-pyright.sh --fix     # reserved; never auto-edits code
#
# Exit: 0 clean · 1 diagnostics present · 2 no runnable basedpyright
set -uo pipefail

ROOT="${CBP_WORKSPACE:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
CONFIG="$ROOT/pyrightconfig.json"

if [ ! -f "$CONFIG" ]; then
  printf 'check-pyright: missing %s\n' "$CONFIG" >&2
  exit 2
fi

if [ "${1:-}" = "--fix" ]; then
  printf 'check-pyright: --fix is not supported; fix diagnostics by hand (Law 11).\n' >&2
  exit 2
fi

RUNNER=()
if command -v basedpyright >/dev/null 2>&1; then
  RUNNER=(basedpyright)
elif command -v uvx >/dev/null 2>&1; then
  RUNNER=(uvx basedpyright)
elif [ -f "$HOME/.local/share/zed/languages/basedpyright/node_modules/basedpyright/index.js" ]; then
  RUNNER=(node "$HOME/.local/share/zed/languages/basedpyright/node_modules/basedpyright/index.js")
else
  printf 'check-pyright: no basedpyright (install: uv tool install basedpyright)\n' >&2
  exit 2
fi

cd "$ROOT" || exit 2
OUT="$("${RUNNER[@]}" 2>&1)"
RC=$?

SUMMARY="$(printf '%s\n' "$OUT" | grep -E '[0-9]+ errors?, [0-9]+ warnings?, [0-9]+ notes?' | tail -1)"
printf 'basedpyright: %s\n' "${SUMMARY:-<no summary>}"

if printf '%s' "$SUMMARY" | grep -qxE '0 errors, 0 warnings, 0 notes'; then
  exit 0
fi

printf '\nDiagnostics (first 60 lines):\n' >&2
printf '%s\n' "$OUT" | grep -E 'error|warning' | head -60 >&2
exit 1
