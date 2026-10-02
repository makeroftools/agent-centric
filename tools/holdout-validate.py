#!/usr/bin/env python3
"""holdout-validate.py — the isolated holdout validator (SPEC-0019).

This is a **separate process**, deliberately outside the authoring path. It is
the only program that reads ``specs/holdout/`` (the authoring context is denied
read of that path; see ``opencode.json``) and the only program that may declare
a run **green** for L3.

Contract (SPEC-0019):

* **Isolation.** Refuse to run unless ``CBP_HOLDOUT_CONTEXT=validator`` is set.
* **Fail closed.** Load the suite, verify it against ``holdout.lock`` (probe
  contract + suite hash + per-scenario digests), and refuse on any drift **before**
  running a single probe.
* **Decision.** Each scenario runs ``RUNS = 3`` times on **fresh** instances; a
  scenario passes on a supermajority (``SUPERMAJORITY = 2``); high-priority
  scenarios require an all-run pass; semantic nondeterminism fails closed.
  ``ERROR`` runs are **infrastructure flake** — the only thing a supermajority
  may absorb.
* **Determinism.** Output is canonical JSON with scenarios sorted by id.

The scenario file is Markdown + simple YAML front-matter + a fenced ``check``
block holding the declarative probe (``holdout.v1``). Probe kinds:

* ``task`` — run the named task ``target`` (a ``service.v1`` task executable)
  and require ``expect_exit`` (default 0). A missing/unstartable task is
  ``ERROR`` (flake); a wrong exit is ``FAIL`` (semantic).

Booting a ``network.v1`` deployment as the probe environment (and the record's
ledger) are the next increments. Stdlib-only; no I/O beyond explicit inputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

CONTEXT_ENV = "CBP_HOLDOUT_CONTEXT"
CONTEXT_VALUE = "validator"

HOLDOUT_LOCK_SCHEMA = "holdout.lock/v1"
HOLDOUT_RUN_SCHEMA = "holdout.run/v1"
HOLDOUT_SUITE_SCHEMA = "holdout.v1"
PROBE_CONTRACT = "holdout.v1"
CHECK_INFO_STRING = "check"
SUPPORTED_PROBE_KINDS = ("task",)

RUNS = 3
SUPERMAJORITY = 2
DEFAULT_THRESHOLD = 1.0

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_REFUSED = 3


class Refusal(Exception):
    """The validator refused to produce a verdict (fail-closed)."""


class RunOutcome(StrEnum):
    """The outcome of one scenario run against one fresh instance."""

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
class Scenario:
    """A parsed, validated scenario."""

    scenario_id: str
    priority: Priority
    feature: str
    probe: Mapping[str, object]
    digest: str


@dataclass(frozen=True)
class Suite:
    """A validated, content-digested scenario suite."""

    scenarios: tuple[Scenario, ...]


@dataclass(frozen=True)
class RunVerdict:
    """The overall, deterministic decision over a set of scenarios."""

    green: bool
    aggregate: float
    scenarios: tuple[tuple[str, ScenarioVerdict], ...]


class ProbeBackend(Protocol):
    """Runs one scenario's probe against one fresh instance."""

    def run(self, probe: Mapping[str, object]) -> RunOutcome:
        ...


def context_is_validator(env: Mapping[str, str]) -> bool:
    """Return whether the process is running in the validator context."""
    return env.get(CONTEXT_ENV) == CONTEXT_VALUE


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_json(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
    """Green requires every high scenario to pass and aggregate >= threshold."""
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


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise Refusal(f"not readable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise Refusal(f"invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise Refusal(f"expected a JSON object in {path}")
    return data


def parse_scenario(text: str) -> tuple[dict[str, str], Mapping[str, object]]:
    """Parse a scenario's YAML front-matter and its fenced ``check`` probe."""
    lines = text.splitlines()
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    if start >= len(lines) or lines[start].strip() != "---":
        raise Refusal("scenario is missing YAML front-matter")
    end = next(
        (index for index in range(start + 1, len(lines)) if lines[index].strip() == "---"),
        None,
    )
    if end is None:
        raise Refusal("scenario front-matter is not terminated")

    front: dict[str, str] = {}
    for line in lines[start + 1 : end]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            raise Refusal(f"malformed front-matter line: {line!r}")
        key, _, value = line.partition(":")
        front[key.strip()] = value.strip()

    index = end + 1
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("```") and stripped[3:].strip() == CHECK_INFO_STRING:
            body: list[str] = []
            index += 1
            while index < len(lines) and not lines[index].strip().startswith("```"):
                body.append(lines[index])
                index += 1
            if index >= len(lines):
                raise Refusal("scenario check block is not terminated")
            try:
                probe = json.loads("\n".join(body))
            except json.JSONDecodeError as exc:
                raise Refusal(f"check block is not valid JSON: {exc}") from exc
            return front, _validate_probe(probe)
        index += 1
    raise Refusal("scenario has no fenced ```check block")


def _validate_probe(probe: object) -> Mapping[str, object]:
    if not isinstance(probe, dict):
        raise Refusal("check block must be a JSON object")
    kind = probe.get("kind")
    if kind not in SUPPORTED_PROBE_KINDS:
        raise Refusal(f"unsupported probe kind: {kind!r}")
    if not isinstance(probe.get("target"), str):
        raise Refusal("task probe needs a string 'target'")
    if not isinstance(probe.get("expect_exit", 0), int):
        raise Refusal("'expect_exit' must be an integer")
    return dict(probe)


def load_suite(suite_dir: Path) -> Suite:
    """Load and validate every ``*.md`` scenario in ``suite_dir`` (sorted)."""
    if not suite_dir.is_dir():
        raise Refusal(f"no suite directory at {suite_dir}")
    scenarios: list[Scenario] = []
    seen: set[str] = set()
    for path in sorted(suite_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        front, probe = parse_scenario(path.read_text(encoding="utf-8"))
        scenario_id = front.get("id", "").strip()
        if not scenario_id:
            raise Refusal(f"{path.name}: missing scenario id")
        if scenario_id in seen:
            raise Refusal(f"duplicate scenario id: {scenario_id}")
        seen.add(scenario_id)
        try:
            priority = Priority(front.get("priority", "normal"))
        except ValueError as exc:
            raise Refusal(f"{path.name}: invalid priority") from exc
        scenarios.append(
            Scenario(
                scenario_id=scenario_id,
                priority=priority,
                feature=front.get("feature", ""),
                probe=probe,
                digest=sha256_hex(path.read_bytes()),
            )
        )
    if not scenarios:
        raise Refusal(f"no scenarios in {suite_dir}")
    return Suite(
        scenarios=tuple(sorted(scenarios, key=lambda scenario: scenario.scenario_id))
    )


def _suite_sha256(suite: Suite) -> str:
    manifest = sorted((scenario.scenario_id, scenario.digest) for scenario in suite.scenarios)
    return sha256_hex(_canonical_json(manifest))


def build_lock(suite: Suite) -> dict[str, object]:
    """Build a deterministic ``holdout.lock`` pinning the suite."""
    return {
        "schema": HOLDOUT_LOCK_SCHEMA,
        "probe_contract": PROBE_CONTRACT,
        "suite_sha256": _suite_sha256(suite),
        "scenarios": {scenario.scenario_id: scenario.digest for scenario in suite.scenarios},
    }


def verify_suite(suite: Suite, lock: Mapping[str, object]) -> None:
    """Refuse (fail-closed) unless the suite matches the lock exactly."""
    if lock.get("schema") != HOLDOUT_LOCK_SCHEMA:
        raise Refusal("holdout lock has the wrong schema")
    if lock.get("probe_contract") != PROBE_CONTRACT:
        raise Refusal("holdout lock pins a different probe contract")
    expected = lock.get("scenarios")
    if not isinstance(expected, dict):
        raise Refusal("holdout lock has no scenario digests")
    actual = {scenario.scenario_id: scenario.digest for scenario in suite.scenarios}
    if set(expected) != set(actual):
        raise Refusal("holdout lock scenario set does not match the suite")
    for scenario_id, digest in actual.items():
        if expected[scenario_id] != digest:
            raise Refusal(f"holdout drift: scenario {scenario_id} changed")
    if lock.get("suite_sha256") != _suite_sha256(suite):
        raise Refusal("holdout suite hash drifted")


class TaskBackend:
    """Run a named task executable (a ``service.v1`` task) in a fresh workdir."""

    def __init__(self, tasks_dir: Path, workdir: Path) -> None:
        self._tasks_dir = tasks_dir
        self._workdir = workdir

    def run(self, probe: Mapping[str, object]) -> RunOutcome:
        executable = self._tasks_dir / str(probe["target"])
        if not executable.is_file():
            return RunOutcome.ERROR
        expect = int(probe.get("expect_exit", 0))
        try:
            completed = subprocess.run(
                [str(executable)],
                cwd=str(self._workdir),
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return RunOutcome.ERROR
        return RunOutcome.PASS if completed.returncode == expect else RunOutcome.FAIL


def run_suite(
    suite: Suite,
    *,
    make_backend: Callable[[int], ProbeBackend],
    threshold: float = DEFAULT_THRESHOLD,
) -> RunVerdict:
    """Run every scenario ``RUNS`` times on fresh backends and decide."""
    scenario_runs: list[ScenarioRuns] = []
    for scenario in suite.scenarios:
        outcomes: list[RunOutcome] = []
        for attempt in range(RUNS):
            backend = make_backend(attempt)
            try:
                outcomes.append(backend.run(scenario.probe))
            except Exception:  # noqa: BLE001 - any fault is an infrastructure flake
                outcomes.append(RunOutcome.ERROR)
        scenario_runs.append(
            ScenarioRuns(
                scenario_id=scenario.scenario_id,
                priority=scenario.priority,
                runs=tuple(outcomes),
            )
        )
    return decide(scenario_runs, threshold=threshold)


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


def _parse_decide_scenarios(document: object) -> tuple[ScenarioRuns, ...]:
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
        except (KeyError, ValueError, TypeError) as exc:
            raise Refusal(f"malformed scenario: {exc}") from exc
        scenarios.append(
            ScenarioRuns(scenario_id=scenario_id, priority=priority, runs=runs)
        )
    return tuple(sorted(scenarios, key=lambda scenario: scenario.scenario_id))


def run(argv: Sequence[str] | None, *, env: Mapping[str, str]) -> dict[str, object]:
    """Validate and return a deterministic run document, or raise :class:`Refusal`."""
    args = build_parser().parse_args(argv)

    if not context_is_validator(env):
        raise Refusal(
            f"outside the validator context; set {CONTEXT_ENV}={CONTEXT_VALUE} "
            "(the authoring agent must never run the validator)"
        )

    if args.decide:
        scenarios = _parse_decide_scenarios(_read_json_object(Path(args.decide)))
        verdict = decide(scenarios, threshold=args.threshold)
        return _verdict_document(verdict, threshold=args.threshold)

    lock_path = Path(args.lock)
    if not lock_path.is_file():
        raise Refusal(f"no holdout lock at {lock_path} (no suite to validate)")
    suite = load_suite(Path(args.suite))
    verify_suite(suite, _read_json_object(lock_path))

    tasks_dir = Path(args.tasks_dir)
    base = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="holdout-"))

    def make_backend(attempt: int) -> ProbeBackend:
        workdir = base / f"run-{attempt}"
        workdir.mkdir(parents=True, exist_ok=True)
        return TaskBackend(tasks_dir, workdir)

    verdict = run_suite(suite, make_backend=make_backend, threshold=args.threshold)
    document = _verdict_document(verdict, threshold=args.threshold)
    document["lock_sha256"] = sha256_hex(lock_path.read_bytes())
    return document


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
        "--suite",
        default="specs/holdout",
        help="directory of scenario Markdown files",
    )
    parser.add_argument(
        "--tasks-dir",
        default="tasks",
        help="directory holding the named task executables probes run",
    )
    parser.add_argument(
        "--workdir",
        default="",
        help="base directory for fresh per-attempt instances (default: a temp dir)",
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
    sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    return EXIT_OK


if __name__ == "__main__":  # pragma: no cover - CLI entry
    raise SystemExit(main())
