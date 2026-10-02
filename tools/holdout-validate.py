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
  is later hardening.
* **Fail closed.** Verify a ``holdout.lock`` (suite hash + per-scenario digests
  + probe-contract hash) and refuse **before** running anything on drift.
* **Decision.** Each scenario runs ``RUNS = 3`` times on fresh ephemeral
  instances; a scenario passes on a supermajority (``SUPERMAJORITY = 2``);
  high-priority scenarios require an all-run pass; semantic nondeterminism fails
  closed. ``ERROR`` runs are **infrastructure flake**, excluded from the
  decision — they are the only thing a supermajority may absorb.
* **Determinism.** Output is canonical JSON with scenarios sorted by id.

Implemented here: the isolation gate, the deterministic decision engine, and the
``holdout.run/v1`` record. Still to come: the ``holdout.v1`` probe vocabulary
and the ephemeral deployment (so the validator refuses until a suite exists).
This module is stdlib-only and performs no I/O except reading an explicit input.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

CONTEXT_ENV = "CBP_HOLDOUT_CONTEXT"
CONTEXT_VALUE = "validator"

HOLDOUT_LOCK_SCHEMA = "holdout.lock/v1"
HOLDOUT_RUN_SCHEMA = "holdout.run/v1"
HOLDOUT_SUITE_SCHEMA = "holdout.v1"

RUNS = 3
SUPERMAJORITY = 2
DEFAULT_THRESHOLD = 1.0

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


class Refusal(Exception):
    """The validator refused to produce a verdict (fail-closed)."""


class RunOutcome(StrEnum):
    """The outcome of one scenario run against one ephemeral instance."""

    PASS = "pass"
    FAIL = "fail"
    ERROR = "error"  # infrastructure flake: the instance never ran the scenario


class Priority(StrEnum):
    HIGH = "high"
    NORMAL = "normal"


class ScenarioVerdict(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    NONDETERMINISTIC = "nondeterministic"
    INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class ScenarioRuns:
    """The ``RUNS`` outcomes recorded for one scenario."""

    scenario_id: str
    priority: Priority
    runs: tuple[RunOutcome, ...]


@dataclass(frozen=True)
class RunVerdict:
    """The overall, deterministic decision over a set of scenarios."""

    green: bool
    aggregate: float
    scenarios: tuple[tuple[str, ScenarioVerdict], ...]


def context_is_validator(env: Mapping[str, str]) -> bool:
    """Return whether the process is running in the validator context."""
    return env.get(CONTEXT_ENV) == CONTEXT_VALUE


def evaluate_scenario(scenario: ScenarioRuns) -> ScenarioVerdict:
    """Decide one scenario from its runs — deterministic and fail-closed.

    * mixed PASS/FAIL among decisive runs => ``NONDETERMINISTIC`` (never a pass);
    * no decisive runs (all ERROR) => ``INSUFFICIENT`` (never a pass);
    * ``high``: every one of the ``RUNS`` runs must PASS (flakes included);
    * ``normal``: at least ``SUPERMAJORITY`` decisive runs, all PASS.
    """
    decisive = tuple(run for run in scenario.runs if run is not RunOutcome.ERROR)
    if not decisive:
        return ScenarioVerdict.INSUFFICIENT
    if RunOutcome.PASS in decisive and RunOutcome.FAIL in decisive:
        return ScenarioVerdict.NONDETERMINISTIC
    if scenario.priority is Priority.HIGH:
        # High-priority: every one of the RUNS runs must PASS (no flake slack).
        if len(scenario.runs) == RUNS and all(
            run is RunOutcome.PASS for run in scenario.runs
        ):
            return ScenarioVerdict.PASS
        return ScenarioVerdict.FAIL
    if all(run is RunOutcome.PASS for run in decisive) and len(decisive) >= SUPERMAJORITY:
        return ScenarioVerdict.PASS
    return ScenarioVerdict.FAIL


def decide(
    scenarios: Sequence[ScenarioRuns], *, threshold: float = DEFAULT_THRESHOLD
) -> RunVerdict:
    """Decide the whole suite. Green requires every high pass and aggregate >= threshold."""
    if not scenarios:
        return RunVerdict(green=False, aggregate=0.0, scenarios=())
    results = tuple(
        (scenario.scenario_id, evaluate_scenario(scenario)) for scenario in scenarios
    )
    by_id = {scenario.scenario_id: scenario for scenario in scenarios}
    high_bad = any(
        by_id[scenario_id].priority is Priority.HIGH
        and verdict is not ScenarioVerdict.PASS
        for scenario_id, verdict in results
    )
    normals = [
        (scenario_id, verdict)
        for scenario_id, verdict in results
        if by_id[scenario_id].priority is Priority.NORMAL
    ]
    if normals:
        aggregate = sum(
            1 for _, verdict in normals if verdict is ScenarioVerdict.PASS
        ) / len(normals)
    else:
        aggregate = 1.0
    green = (not high_bad) and aggregate >= threshold
    return RunVerdict(green=green, aggregate=aggregate, scenarios=results)


def _parse_scenarios(document: object) -> tuple[ScenarioRuns, ...]:
    if not isinstance(document, dict):
        raise Refusal("decide input must be a JSON object")
    raw = document.get("scenarios")
    if not isinstance(raw, list):
        raise Refusal("decide input needs a 'scenarios' list")
    scenarios: list[ScenarioRuns] = []
    for item in raw:
        if not isinstance(item, dict):
            raise Refusal("each scenario must be an object")
        try:
            scenario_id = str(item["id"])
            priority = Priority(str(item["priority"]))
            runs = tuple(RunOutcome(str(value)) for value in item["runs"])
        except (KeyError, ValueError) as exc:
            raise Refusal(f"malformed scenario: {exc}") from exc
        scenarios.append(
            ScenarioRuns(scenario_id=scenario_id, priority=priority, runs=runs)
        )
    return tuple(sorted(scenarios, key=lambda scenario: scenario.scenario_id))


def _verdict_document(verdict: RunVerdict, *, threshold: float) -> dict[str, object]:
    return {
        "schema": HOLDOUT_RUN_SCHEMA,
        "verdict": "green" if verdict.green else "red",
        "aggregate": verdict.aggregate,
        "threshold": threshold,
        "runs": RUNS,
        "supermajority": SUPERMAJORITY,
        "scenarios": [
            {"id": scenario_id, "verdict": scenario_verdict.value}
            for scenario_id, scenario_verdict in verdict.scenarios
        ],
    }


def run(argv: Sequence[str] | None, *, env: Mapping[str, str]) -> dict[str, object]:
    """Validate and return a deterministic run document, or raise :class:`Refusal`."""
    args = build_parser().parse_args(argv)

    if not context_is_validator(env):
        raise Refusal(
            f"outside the validator context; set {CONTEXT_ENV}={CONTEXT_VALUE} "
            "(the authoring agent must never run the validator)"
        )

    if args.decide:
        try:
            document = json.loads(Path(args.decide).read_text(encoding="utf-8"))
        except OSError as exc:
            raise Refusal(f"decide input not readable: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise Refusal(f"decide input is not valid JSON: {exc}") from exc
        scenarios = _parse_scenarios(document)
        verdict = decide(scenarios, threshold=args.threshold)
        return _verdict_document(verdict, threshold=args.threshold)

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
    parser.add_argument(
        "--decide",
        default="",
        help="evaluate a JSON run-outcome document (pure; no deployment)",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help="minimum normal-scenario pass fraction required for green",
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
