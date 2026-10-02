"""Work runtime (SPEC-0021) — compiled-work materialization + the task-load binder.

The pure contract (:mod:`agent_centric.contracts.work`) freezes the recipe and
its compile seam; this module realizes the runtime side, offline and fail-closed:

- :func:`materialize_work` stores a compiled artifact in the **content-addressed
  cache** (content-addressed, idempotent), whose digest *is* the compilation's
  artifact hash.
- :class:`WorkBinder` implements the explicit **dynamic task-load/bind protocol**
  (SPEC-0002 open item #2): a task is admitted only when it is **declared** and
  **pinned** to its signed content hash, its artifact is present in the
  content-addressed cache (verify-on-read), and (when a verifier is configured)
  its detached signature verifies — all **before** any bind. A refusal leaves no
  partial side effect: an unpinned or undeclared task is never registered.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from ..contracts.work import (
    TaskAnnouncement,
    TaskLoad,
    WorkCompilation,
    WorkRecipe,
    admit_task_load,
    compile_work,
)
from .cache import ContentAddressedCache
from .signing import SignatureVerifier


class WorkRuntimeError(RuntimeError):
    """A work runtime operation violated the contract (fail-closed)."""


@dataclass(frozen=True)
class MaterializedWork:
    """A compiled work artifact stored in the content-addressed cache."""

    artifact_hash: str
    compilation: WorkCompilation

    def __post_init__(self) -> None:
        if self.artifact_hash != self.compilation.artifact_hash():
            raise ValueError(
                "MaterializedWork artifact_hash must equal the compilation's "
                "artifact hash."
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "artifact_hash": self.artifact_hash,
            "compilation": self.compilation.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MaterializedWork:
        return cls(
            artifact_hash=str(data["artifact_hash"]),
            compilation=WorkCompilation.from_dict(data["compilation"]),
        )


def materialize_work(
    compilation: WorkCompilation, cache: ContentAddressedCache
) -> MaterializedWork:
    """Store ``compilation`` in ``cache`` deterministically and idempotently.

    The artifact hash the cache returns must equal the compilation's artifact
    hash (they are the same content address); a mismatch is refused.
    """
    digest = cache.put(compilation.canonical_bytes())
    if digest != compilation.artifact_hash():
        raise WorkRuntimeError(
            "content-addressed cache returned a digest that does not match the "
            "compiled artifact (refused)."
        )
    return MaterializedWork(artifact_hash=digest, compilation=compilation)


def compile_and_materialize(
    recipe: WorkRecipe, cache: ContentAddressedCache
) -> MaterializedWork:
    """Compile ``recipe`` purely, then materialize it (identical ⇒ byte-identical)."""
    return materialize_work(compile_work(recipe), cache)


class TaskLoader(Protocol):
    """Turns verified artifact bytes into a callable task entry."""

    def __call__(self, task: str, artifact: bytes) -> Callable[..., Any]: ...


@dataclass(frozen=True)
class BoundTask:
    """A dynamically loaded task that has been admitted and bound."""

    task: str
    content_hash: str
    artifact_digest: str
    announcement: TaskAnnouncement
    entry: Callable[..., Any]

    @property
    def identity(self) -> str:
        """The task identity: its signed content hash, never its ports."""
        return self.content_hash


class WorkBinder:
    """The fail-closed dynamic task-load/bind protocol (SPEC-0021 §5).

    Verification always precedes binding: the load is admitted (declared +
    pinned), its artifact is read from the content-addressed cache
    (verify-on-read), and its signature is checked when a verifier is configured.
    Only then is the entry resolved and registered with the driver.
    """

    def __init__(
        self,
        driver: Any,
        *,
        declared_tasks: tuple[str, ...],
        artifacts: ContentAddressedCache,
        loader: TaskLoader,
        verifier: SignatureVerifier | None = None,
    ) -> None:
        self._driver = driver
        self._declared = tuple(declared_tasks)
        if len(set(self._declared)) != len(self._declared):
            raise WorkRuntimeError("declared tasks must be unique.")
        self._artifacts = artifacts
        self._loader = loader
        self._verifier = verifier
        self._bound: dict[str, BoundTask] = {}

    @property
    def declared_tasks(self) -> tuple[str, ...]:
        return self._declared

    @property
    def bound_tasks(self) -> tuple[str, ...]:
        return tuple(sorted(self._bound))

    def bind(self, load: TaskLoad, *, expected_hash: str) -> BoundTask:
        """Admit and bind ``load``, verifying everything **before** any bind.

        Raises:
            WorkRuntimeError: If the task is undeclared or unpinned, its artifact
                is not content-addressed, or its signature does not verify. No
                partial side effect is left behind.
        """
        try:
            announcement = admit_task_load(
                load, declared_tasks=self._declared, expected_hash=expected_hash
            )
        except ValueError as exc:
            raise WorkRuntimeError(str(exc)) from exc

        artifact = self._artifacts.get(load.content_hash)
        if artifact is None:
            raise WorkRuntimeError(
                f"task {load.task!r} artifact is not content-addressed; refusing "
                "before bind."
            )
        if self._verifier is not None:
            if not load.signature:
                raise WorkRuntimeError(
                    "a verifier is configured but the task load carries no "
                    "signature; refusing before bind."
                )
            self._verifier.verify(artifact, load.signature.encode("utf-8"))

        entry = self._loader(load.task, artifact)
        self._driver.register(
            load.task, entry, source_url=f"work://{load.content_hash}"
        )
        bound = BoundTask(
            task=load.task,
            content_hash=load.content_hash,
            artifact_digest=load.content_hash,
            announcement=announcement,
            entry=entry,
        )
        self._bound[load.task] = bound
        return bound

    def task(self, name: str) -> BoundTask:
        """Return a bound task (fail-closed if it was never admitted)."""
        try:
            return self._bound[name]
        except KeyError as exc:
            raise WorkRuntimeError(f"no bound task named {name!r}.") from exc
