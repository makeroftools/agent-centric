#!/usr/bin/env python3
"""holdout-validate.py — the isolated holdout validator (SPEC-0019).

This is a **separate process**, deliberately outside the authoring path. It is
the only program that reads ``specs/holdout/`` (the authoring context is denied
read of that path; see ``opencode.json``) and the only program that may declare
a run **green** for L3.

Contract (SPEC-0019):

* **Isolation.** Refuse to run unless ``CBP_HOLDOUT_CONTEXT=validator`` is set,
  so the authoring agent cannot invoke it by accident or intent. The full
  structural isolation (a separate OS user/container with its own path access)
  is Phase 1+ hardening.
* **Fail closed.** Verify a ``holdout.lock`` (suite hash + per-scenario digests
  + probe-contract hash) and refuse **before** running anything on drift.
* **Decision.** Each scenario runs ``RUNS = 3`` times on fresh ephemeral
  instances; a scenario passes on a supermajority (``SUPERMAJORITY = 2``);
  high-priority scenarios require an all-run pass; semantic nondeterminism fails
  closed.

This file is the **Phase 1 skeleton**: the isolation gate and the fail-closed
entrypoint. The ``holdout.v1`` probe vocabulary, the ephemeral deployment, the
decision record, and the ledger are Phases 2-3. Until a suite exists, the
validator refuses — there is nothing to validate.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

CONTEXT_ENV = "CBP_HOLDOUT_CONTEXT"
CONTEXT_VALUE = "validator"

HOLDOUT_LOCK_SCHEMA = "holdout.lock/v1"
HOLDOUT_RUN_SCHEMA = "holdout.run/v1"
HOLDOUT_SUITE_SCHEMA = "holdout.v1"

RUNS = 3
SUPERMAJORITY = 2

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


class Refusal(Exception):
    """The validator refused to produce a verdict (fail-closed)."""


def context_is_validator(env: Mapping[str, str]) -> bool:
    """Return whether the process is running in the validator context."""
    return env.get(CONTEXT_ENV) == CONTEXT_VALUE


def run(argv: Sequence[str] | None, *, env: Mapping[str, str]) -> dict[str, object]:
    """Validate the holdout suite and return a deterministic run document.

    Raises :class:`Refusal` (fail-closed) when the validator may not, or cannot,
    produce a verdict. In Phase 1 every path refuses: the suite and the probe
    vocabulary do not exist yet.
    """
    args = build_parser().parse_args(argv)

    if not context_is_validator(env):
        raise Refusal(
            f"outside the validator context; set {CONTEXT_ENV}={CONTEXT_VALUE} "
            "(the authoring agent must never run the validator)"
        )

    lock = Path(args.lock)
    if not lock.is_file():
        raise Refusal(f"no holdout lock at {lock} (no suite to validate)")

    raise Refusal(
        "holdout lock present, but the holdout.v1 probe vocabulary and ephemeral "
        "deployment are not implemented yet (SPEC-0019 Phase 2)"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="holdout-validate",
        description="Isolated holdout validator (SPEC-0019). Separate process.",
    )
    parser.add_argument(
        "--lock",
        default="specs/holdout/holdout.lock",
        help="path to the holdout lock that pins the suite",
    )
    return parser


def _refusal_document(reason: str) -> dict[str, object]:
    return {
        "schema": HOLDOUT_RUN_SCHEMA,
        "verdict": "refused",
        "reason": reason,
        "runs": RUNS,
        "supermajority": SUPERMAJORITY,
    }


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    resolved_env = os.environ if env is None else env
    try:
        document = run(argv, env=resolved_env)
    except Refusal as exc:
        sys.stdout.write(
            json.dumps(_refusal_document(str(exc)), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        return EXIT_REFUSED
    sys.stdout.write(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n"
    )
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
