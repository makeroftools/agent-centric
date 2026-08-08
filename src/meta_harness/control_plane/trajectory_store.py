"""Durable, append-only Trajectory Store.

The trajectory store persists every step and the terminal outcome of a task so
that the audit record survives process restarts. It is append-only: once a
step or outcome is written it is never rewritten or deleted, and a trajectory
can be fully reconstructed from the store afterwards.

Two implementations are provided:

- ``InMemoryTrajectoryStore`` — a simple, in-memory store used as the default so
  that the Manager is trivially usable without a filesystem. It implements the
  exact same append-only semantics but is not durable across restarts.
- ``FileTrajectoryStore`` — a durable, file-based store. Each trajectory is an
  append-only JSON-lines file (one record per line) under a directory. Writes
  are flushed and ``fsync``-ed so that a crash mid-task cannot silently corrupt
  an already-appended record; a truncated or malformed record is *detected* on
  load and reported as ``CorruptTrajectoryError``.

Both stores are deterministic and side-effect free on lookup.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from ..contracts.result import FailureReason
from ..contracts.trajectory import StepRecord, StepStatus, Trajectory, TrajectoryVersion

# A terminal outcome can be verified, a failure, or interrupted (a crash before
# the terminal decision was durably recorded).
OutcomeKind = Literal["verified", "failure", "interrupted"]


class TrajectoryStoreError(Exception):
    """Base error for trajectory store failures."""


class CorruptTrajectoryError(TrajectoryStoreError):
    """Raised when a stored trajectory is inconsistent or unparseable."""


@dataclass(frozen=True)
class StoredOutcome:
    """The terminal, audited decision of a task, as stored for replay.

    Attributes:
        kind: ``verified``, ``failure``, or ``interrupted``.
        output: For ``verified``, the verified output.
        reason: For ``failure``, the machine-readable FailureReason string.
        message: For ``failure``, a human-readable message.
    """

    kind: OutcomeKind
    output: Any = None
    reason: str | None = None
    message: str = ""

    @staticmethod
    def verified(output: Any) -> StoredOutcome:
        return StoredOutcome(kind="verified", output=output)

    @staticmethod
    def failure(reason: FailureReason, message: str) -> StoredOutcome:
        return StoredOutcome(kind="failure", reason=reason.value, message=message)

    @staticmethod
    def interrupted() -> StoredOutcome:
        return StoredOutcome(kind="interrupted")


@dataclass(frozen=True)
class StoredTrajectory:
    """A fully reconstructed, inspectable audit record from the store.

    Attributes:
        trajectory_id: The unique identifier of the trajectory.
        task_id: The task this trajectory belongs to.
        agent_name: The agent component that executed the task.
        steps: The ordered, append-only list of steps.
        outcome: The terminal audited decision.
    """

    trajectory_id: str
    task_id: str
    agent_name: str
    steps: tuple[StepRecord, ...]
    outcome: StoredOutcome

    def to_trajectory(self) -> Trajectory:
        """Reconstruct the immutable ``Trajectory`` contract from this record."""
        return Trajectory(TrajectoryVersion.V1, self.task_id, self.agent_name, self.steps)


class TrajectoryStore(Protocol):
    """The append-only trajectory persistence interface used by the Manager."""

    def begin(
        self, trajectory_id: str, task_id: str, agent_name: str
    ) -> None: ...

    def append_step(self, trajectory_id: str, step: StepRecord) -> None: ...

    def record_outcome(self, trajectory_id: str, outcome: StoredOutcome) -> None: ...

    def load(self, trajectory_id: str) -> StoredTrajectory | None: ...

    def contains(self, trajectory_id: str) -> bool: ...


def _validate_trajectory_id(trajectory_id: str) -> None:
    if not trajectory_id:
        raise ValueError("trajectory_id must be non-empty.")


class InMemoryTrajectoryStore:
    """An in-memory, append-only trajectory store (not durable across restarts).

    Used as the default so the Manager works without a filesystem. It enforces
    the same append-only semantics and is useful for unit tests that do not
    exercise durability.
    """

    def __init__(self) -> None:
        self._trajectories: dict[str, dict[str, Any]] = {}

    def begin(self, trajectory_id: str, task_id: str, agent_name: str) -> None:
        _validate_trajectory_id(trajectory_id)
        if trajectory_id in self._trajectories:
            raise TrajectoryStoreError(f"Trajectory {trajectory_id!r} already exists.")

        def _new() -> dict[str, Any]:
            return {"task_id": task_id, "agent_name": agent_name,
                    "steps": [], "outcome": None}

        self._trajectories[trajectory_id] = _new()

    def append_step(self, trajectory_id: str, step: StepRecord) -> None:
        meta = self._require(trajectory_id)
        meta["steps"].append(step)

    def record_outcome(self, trajectory_id: str, outcome: StoredOutcome) -> None:
        meta = self._require(trajectory_id)
        if meta["outcome"] is not None:
            raise TrajectoryStoreError(
                f"Outcome for trajectory {trajectory_id!r} already recorded."
            )
        meta["outcome"] = outcome

    def load(self, trajectory_id: str) -> StoredTrajectory | None:
        meta = self._trajectories.get(trajectory_id)
        if meta is None:
            return None
        outcome = meta["outcome"] or StoredOutcome.interrupted()
        return StoredTrajectory(
            trajectory_id=trajectory_id,
            task_id=meta["task_id"],
            agent_name=meta["agent_name"],
            steps=tuple(meta["steps"]),
            outcome=outcome,
        )

    def contains(self, trajectory_id: str) -> bool:
        return trajectory_id in self._trajectories

    def _require(self, trajectory_id: str) -> dict[str, Any]:
        meta = self._trajectories.get(trajectory_id)
        if meta is None:
            raise TrajectoryStoreError(f"Trajectory {trajectory_id!r} has not been begun.")
        return meta


# ---------------------------------------------------------------------------
# JSON (de)serialisation for the durable file store.
# ---------------------------------------------------------------------------


def _step_to_record(step: StepRecord) -> dict[str, Any]:
    return {
        "kind": "step",
        "step_index": step.step_index,
        "status": step.status.value,
        "description": step.description,
        "input": step.input,
        "output": step.output,
        "error": step.error,
        "elapsed_seconds": step.elapsed_seconds,
    }


def _record_to_step(data: dict[str, Any]) -> StepRecord:
    try:
        return StepRecord(
            step_index=int(data["step_index"]),
            status=StepStatus(data["status"]),
            description=str(data["description"]),
            input=data.get("input"),
            output=data.get("output"),
            error=data.get("error"),
            elapsed_seconds=float(data.get("elapsed_seconds", 0.0)),
        )
    except (KeyError, ValueError, TypeError) as exc:
        raise CorruptTrajectoryError(f"Malformed step record: {exc}") from exc


class FileTrajectoryStore:
    """A durable, append-only, file-based trajectory store.

    Each trajectory is stored as ``<hex(trajectory_id)>.jsonl`` under the
    configured directory. A file contains:

    1. one ``meta`` line (written at ``begin``),
    2. zero or more ``step`` lines (one per appended step),
    3. one ``outcome`` line (the terminal decision).

    Every write is flushed and ``fsync``-ed. Because records are written one per
    line and never rewritten, a crash cannot corrupt an already-appended
    record; an incomplete trailing record is detected on load.
    """

    def __init__(self, directory: str | Path) -> None:
        self._directory = Path(directory)
        self._directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _filename(trajectory_id: str) -> str:
        # Hex-encoding the id yields a deterministic, injective, filesystem-safe
        # filename, so load(trajectory_id) always finds the same file.
        return trajectory_id.encode("utf-8").hex() + ".jsonl"

    def _path(self, trajectory_id: str) -> Path:
        return self._directory / self._filename(trajectory_id)

    @staticmethod
    def _write_line_atomic_line(path: Path, line: str) -> None:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def begin(self, trajectory_id: str, task_id: str, agent_name: str) -> None:
        _validate_trajectory_id(trajectory_id)
        path = self._path(trajectory_id)
        if path.exists():
            raise TrajectoryStoreError(f"Trajectory {trajectory_id!r} already exists.")
        meta = {
            "kind": "meta",
            "trajectory_id": trajectory_id,
            "task_id": task_id,
            "agent_name": agent_name,
        }
        line = json.dumps(meta, ensure_ascii=False, sort_keys=True) + "\n"
        with path.open("x", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

    def append_step(self, trajectory_id: str, step: StepRecord) -> None:
        path = self._path(trajectory_id)
        self._write_line_atomic_line(path, json.dumps(_step_to_record(step), sort_keys=True) + "\n")

    def record_outcome(self, trajectory_id: str, outcome: StoredOutcome) -> None:
        # A trajectory must have exactly one terminal decision. Refuse to append a
        # second outcome so the audit record cannot silently contradict itself.
        if self._has_outcome(trajectory_id):
            raise TrajectoryStoreError(
                f"Outcome for trajectory {trajectory_id!r} already recorded."
            )
        data: dict[str, Any] = {"kind": "outcome", "outcome_kind": outcome.kind}
        if outcome.kind == "verified":
            data["output"] = outcome.output
        elif outcome.kind == "failure":
            data["reason"] = outcome.reason
            data["message"] = outcome.message
        path = self._path(trajectory_id)
        self._write_line_atomic_line(path, json.dumps(data, sort_keys=True) + "\n")

    def _has_outcome(self, trajectory_id: str) -> bool:
        path = self._path(trajectory_id)
        if not path.exists():
            return False
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                if json.loads(line).get("kind") == "outcome":
                    return True
            except json.JSONDecodeError:
                # A corrupt trailing line is not an outcome; treat as not present.
                continue
        return False

    def load(self, trajectory_id: str) -> StoredTrajectory | None:
        path = self._path(trajectory_id)
        if not path.exists():
            return None
        lines = path.read_text(encoding="utf-8").splitlines()
        records: list[dict[str, Any]] = []
        for lineno, line in enumerate(lines, start=1):
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise CorruptTrajectoryError(
                    f"Corrupt record on line {lineno} of {path.name}: {exc}"
                ) from exc

        meta: dict[str, Any] | None = None
        steps: list[StepRecord] = []
        outcome: StoredOutcome | None = None
        for record in records:
            kind = record.get("kind")
            if kind == "meta":
                meta = record
            elif kind == "step":
                steps.append(_record_to_step(record))
            elif kind == "outcome":
                outcome = self._decode_outcome(record)
            else:
                raise CorruptTrajectoryError(f"Unknown record kind: {record!r}")

        if meta is None:
            raise CorruptTrajectoryError(f"Trajectory {path.name} is missing a meta record.")
        if outcome is None:
            outcome = StoredOutcome.interrupted()

        return StoredTrajectory(
            trajectory_id=trajectory_id,
            task_id=meta["task_id"],
            agent_name=meta["agent_name"],
            steps=tuple(steps),
            outcome=outcome,
        )

    def contains(self, trajectory_id: str) -> bool:
        return self._path(trajectory_id).exists()

    @staticmethod
    def _decode_outcome(data: dict[str, Any]) -> StoredOutcome:
        kind = data.get("outcome_kind")
        if kind == "verified":
            return StoredOutcome.verified(data.get("output"))
        if kind == "failure":
            return StoredOutcome(
                kind="failure",
                reason=data.get("reason"),
                message=data.get("message", ""),
            )
        if kind == "interrupted":
            return StoredOutcome.interrupted()
        raise CorruptTrajectoryError(f"Unknown outcome kind: {data!r}")