"""Local-first provider adapter (SPEC-0020 §1/§2) — the first concrete adapter.

This is the **local-first** realization of the injected
:class:`~agent_centric.cbp.provider_runtime.ProviderAdapter` seam. It maps each
typed :class:`~agent_centric.cbp.provider_runtime.ProviderCall` to a **named,
content-pinned local task** declared by the ``provider.v1`` manifest and executes
it through an injected :class:`LocalTaskRunner`. It performs no network I/O and
contains no cryptography: the runner is the single confined execution seam, and it
is injectable so the whole adapter is deterministic and offline-testable.

Fail-closed by construction:

- A **mutating** script task is content-verified (re-hashed on disk against its
  ``TaskBinding.digest``) **before** it runs; a drift, an unreadable task, or a
  mutating task with no digest is refused with no side effect.
- A mutating task that is a **callable** cannot be content-verified and is
  refused outright.
- The ``observe`` task's output must be **canonical JSON**; a malformed,
  non-canonical, duplicate-keyed, or schema-invalid payload is refused cleanly.
- A provider id mismatch, a missing task role, or a non-zero task return code all
  yield ``ProviderResult(ok=False)`` (recorded as evidence by the runtime), never
  an exception that would skip the audit trail.

Only the four runtime call roles are executed here (``observe`` / ``apply`` /
``health`` / ``teardown``); ``plan`` stays **pure** in
:mod:`agent_centric.contracts.provider` and is never run as a task.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..contracts.provider import ObservedResource, Provider
from ..contracts.service import TaskBinding, TaskKind
from .provider_runtime import (
    ProviderCall,
    ProviderCallKind,
    ProviderResult,
    ProviderRuntimeError,
)


def _canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


@dataclass(frozen=True)
class LocalTaskResult:
    """The deterministic outcome of running one named local task."""

    target: str
    returncode: int
    output: str


class LocalTaskRunner(Protocol):
    """Invokes a named local task (an executable script), the confined I/O seam."""

    def __call__(
        self, target: str, args: Sequence[str], *, tasks_dir: Path
    ) -> LocalTaskResult: ...


def _digest_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _target_is_single_name(target: str) -> bool:
    """True iff ``target`` is a single path component (no traversal)."""
    return bool(target) and "/" not in target and "\\" not in target


class LocalTaskProviderAdapter:
    """A ``ProviderAdapter`` that runs a provider's named local tasks.

    Args:
        provider: The ``provider.v1`` manifest whose task table is executed.
        runner: The injected task runner (the only execution seam).
        tasks_dir: The directory the runner resolves ``target`` under; also the
            root the adapter re-hashes for content verification.
        require_digests: When set (the default), a **mutating** script task with
            no content-digest is refused before it runs (verify-then-apply).
    """

    def __init__(
        self,
        provider: Provider,
        runner: LocalTaskRunner,
        *,
        tasks_dir: str | Path,
        require_digests: bool = True,
    ) -> None:
        self._provider = provider
        self._runner = runner
        self._tasks_dir = Path(tasks_dir)
        self._require_digests = require_digests

    @property
    def provider(self) -> Provider:
        return self._provider

    @property
    def tasks_dir(self) -> Path:
        return self._tasks_dir

    # -- the ProviderAdapter seam -------------------------------------------

    def call(self, call: ProviderCall) -> ProviderResult:
        """Execute one typed provider call as a pinned local task (fail-closed)."""
        if call.provider != self._provider.id:
            return ProviderResult(
                ok=False,
                detail=(
                    f"provider id mismatch: call targets {call.provider!r}, "
                    f"adapter hosts {self._provider.id!r}"
                ),
            )
        try:
            binding = self._provider.task(call.op)
        except ValueError as exc:
            return ProviderResult(ok=False, detail=str(exc))

        if not _target_is_single_name(binding.target):
            return ProviderResult(
                ok=False,
                detail=(
                    f"task target {binding.target!r} must be a single name "
                    "(no path separators or traversal)"
                ),
            )

        try:
            self._verify_binding(binding)
        except ProviderRuntimeError as exc:
            return ProviderResult(ok=False, detail=str(exc))

        args = self._call_args(call, binding)
        try:
            result = self._runner(binding.target, args, tasks_dir=self._tasks_dir)
        except OSError as exc:
            return ProviderResult(ok=False, detail=f"task runner failed: {exc}")

        if result.returncode != 0:
            return ProviderResult(ok=False, detail=result.output)

        if call.op == ProviderCallKind.OBSERVE:
            try:
                observed = self._parse_observed(result.output)
            except ProviderRuntimeError as exc:
                return ProviderResult(ok=False, detail=str(exc))
            return ProviderResult(ok=True, observed=observed, detail=result.output)
        return ProviderResult(ok=True, detail=result.output)

    # -- verification --------------------------------------------------------

    def _verify_binding(self, binding: TaskBinding) -> None:
        """Content-verify a task before it runs (verify-then-apply)."""
        if binding.kind != TaskKind.SCRIPT:
            # A callable has no on-disk bytes to hash; a mutating callable can
            # never be verified, so it is refused rather than trusted.
            if binding.mutating:
                raise ProviderRuntimeError(
                    f"mutating task {binding.name!r} is a callable; refusing an "
                    "unverifiable mutating task"
                )
            return
        if binding.digest:
            path = self._tasks_dir / binding.target
            try:
                actual = _digest_file(path)
            except OSError as exc:
                raise ProviderRuntimeError(
                    f"task {binding.name!r} ({binding.target}) not readable: {exc}"
                ) from exc
            if actual != binding.digest:
                raise ProviderRuntimeError(
                    f"task digest mismatch for {binding.name!r} ({binding.target}): "
                    f"manifest {binding.digest}, on-disk {actual}"
                )
        elif binding.mutating and self._require_digests:
            raise ProviderRuntimeError(
                f"mutating task {binding.name!r} ({binding.target}) declares no "
                "content digest; refusing to run an unpinned task"
            )

    # -- invocation ----------------------------------------------------------

    def _call_args(self, call: ProviderCall, binding: TaskBinding) -> tuple[str, ...]:
        base = tuple(binding.args)
        if call.op == ProviderCallKind.APPLY:
            return (*base, call.kind, call.name, call.change, call.digest)
        return base

    # -- observation ---------------------------------------------------------

    def _parse_observed(self, output: str) -> tuple[ObservedResource, ...]:
        """Parse an ``observe`` task's canonical-JSON output (fail-closed)."""
        text = output.strip()
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ProviderRuntimeError(f"observe output is not JSON: {exc}") from exc
        if _canonical_bytes(data).decode("utf-8") != text:
            raise ProviderRuntimeError("observe output is not canonical JSON")
        resources: Any = (
            data.get("resources", []) if isinstance(data, dict) else data
        )
        if not isinstance(resources, list):
            raise ProviderRuntimeError(
                "observe output must be a resource list (or {'resources': [...]})"
            )
        observed: list[ObservedResource] = []
        for item in resources:
            if not isinstance(item, dict):
                raise ProviderRuntimeError("malformed observed resource: not an object")
            try:
                observed.append(ObservedResource.from_dict(item))
            except (KeyError, TypeError, ValueError) as exc:
                raise ProviderRuntimeError(
                    f"malformed observed resource: {exc}"
                ) from exc
        return tuple(observed)
