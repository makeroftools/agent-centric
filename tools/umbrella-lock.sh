#!/usr/bin/env bash
#
# umbrella-lock.sh — regenerate the Core umbrella lock (Law-11 safe).
#
# SPEC-0011: offline directory source + local signing. Rebuilds the canonical
# bundles, re-pins ``components.lock`` from ``designs/core.v1.json``, re-signs
# the lock hash, and verifies the result. Every write goes through
# ``tools/safe-replace.sh`` (whole-file, atomic).
#
# Default key is the deterministic offline TEST key. The operator regenerates
# with the real key, e.g.:
#   tools/umbrella-lock.sh --key gpg --key-id operator-release-1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPONENTS=(shell registry)
KEY_ARGS=("$@")

if command -v uv >/dev/null 2>&1; then
  RUN=(uv run --project "$ROOT" python3 "$ROOT/tools/umbrella-lock.py")
else
  export PYTHONPATH="$ROOT/src${PYTHONPATH:+:$PYTHONPATH}"
  RUN=(python3 "$ROOT/tools/umbrella-lock.py")
fi

for component in "${COMPONENTS[@]}"; do
  "${RUN[@]}" "${KEY_ARGS[@]}" bundle --component "$component" \
    | "$ROOT/tools/safe-replace.sh" "$ROOT/artifacts/components/$component.tar"
done

"${RUN[@]}" "${KEY_ARGS[@]}" pin --design "$ROOT/designs/core.v1.json" \
  | "$ROOT/tools/safe-replace.sh" "$ROOT/components.lock"

"${RUN[@]}" "${KEY_ARGS[@]}" sign-lock --lock "$ROOT/components.lock" \
  | "$ROOT/tools/safe-replace.sh" "$ROOT/components.lock.sig"

"${RUN[@]}" "${KEY_ARGS[@]}" --repo-root "$ROOT" verify \
  --lock "$ROOT/components.lock" --sig "$ROOT/components.lock.sig"
