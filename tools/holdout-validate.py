#!/usr/bin/env python3
"""holdout-validate.py — thin CLI over ``agent_centric.cbp.holdout`` (SPEC-0019).

The validator core (and its pinned TCB) lives in the importable library
``src/agent_centric/cbp/holdout.py``; this script is only the operator-facing
entrypoint. It remains a **separate process**: the core refuses to run unless
``CBP_HOLDOUT_CONTEXT=validator`` is set, so the authoring agent can never run
the validator (SPEC-0019 §1). Exit codes: 0 green, 1 red, 2 usage, 3 refused.

Run ``--self-test`` for a synthetic known-good/known-bad check, and
``--build-lock`` to pin an operator-private suite.
"""

from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from agent_centric.cbp.holdout import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
