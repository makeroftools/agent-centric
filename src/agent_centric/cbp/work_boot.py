"""Boot/run dynamically bound work tasks through verified bundles (SPEC-0021 §5).

This wires the :class:`~agent_centric.cbp.work_runtime.WorkBinder` (the
fail-closed dynamic task-load/bind protocol) to the boot machinery: a task load's
artifact is a **component bundle** (``component.json`` + code). The binder has
already admitted the load (declared + pinned), verified it is content-addressed,
and (when configured) checked its detached signature — only then does
:func:`bundle_task_loader` resolve the entry:

- an **allowlisted** in-process entry runs as-is; and
- a non-allowlisted Python entry runs **process-isolated** from its verified
  bundle, only when ``allow_subprocess`` is explicitly set.

The bundle's ``component.json`` name must equal the task, so a load can never
bind a task to a different component's code. :func:`run_bound_work_network` then
executes a ``network.v1`` over the bound tasks: every non-child component must
map to an admitted task or the run refuses **before** anything executes.
"""

from __future__ import annotations

import tarfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

from ..contracts.component import EntryDescriptor
from ..contracts.work import TaskLoad
from .cache import ContentAddressedCache
from .component_bundle import load_manifest
from .component_process import DEFAULT_TIMEOUT_SECONDS, run_component
from .component_runtime import AllowlistedEntryResolver, EntryNotAllowed
from .network import ComponentNetwork, run_network
from .signing import SignatureVerifier
from .work_runtime import TaskLoader, WorkBinder, WorkRuntimeError


class WorkBootError(WorkRuntimeError):
    """A dynamically bound work task could not be resolved or bound (fail-closed)."""


@dataclass(frozen=True)
class WorkTaskSpec:
    """A pinned task load to bind: the load plus its expected content hash."""

    load: TaskLoad
    expected_hash: str


def _process_entry(
    bundle: bytes, entry: EntryDescriptor, timeout: float
) -> Callable[..., Any]:
    """Wrap a verified bundle+entry as a driver-dispatched process callable."""

    def invoke(**kwargs: Any) -> Any:
        return run_component(bundle, entry, kwargs, timeout=timeout)

    return invoke


def _bundle_entry(
    task: str,
    bundle: bytes,
    *,
    entry_allowlist: frozenset[str],
    allow_subprocess: bool,
    process_timeout: float,
) -> Callable[..., Any]:
    """Resolve a verified bundle's entry to a driver-dispatched callable."""
    try:
        manifest = load_manifest(bundle)
    except (ValueError, OSError, tarfile.TarError) as exc:
        raise WorkBootError(f"task {task!r} bundle manifest is invalid: {exc}") from exc
    if manifest.name != task:
        raise WorkBootError(
            f"task {task!r} does not match the bundle component {manifest.name!r} "
            "(fail-closed)."
        )
    try:
        return AllowlistedEntryResolver(entry_allowlist).resolve(manifest.entry)
    except EntryNotAllowed as exc:
        if not allow_subprocess:
            raise WorkBootError(
                f"task {task!r} entry is not executable: {exc}"
            ) from exc
    return _process_entry(bundle, manifest.entry, process_timeout)


def bundle_task_loader(
    *,
    entry_allowlist: frozenset[str],
    allow_subprocess: bool = False,
    process_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> TaskLoader:
    """A :class:`TaskLoader` that resolves a driver-dispatched entry from a bundle.

    The bundle bytes are already content-verified by the binder; this loader adds
    the *component* checks: the manifest must parse, its name must match the task,
    and its entry must be allowlisted or (explicitly) process-isolated.
    """

    def load(task: str, artifact: bytes) -> Callable[..., Any]:
        return _bundle_entry(
            task,
            artifact,
            entry_allowlist=entry_allowlist,
            allow_subprocess=allow_subprocess,
            process_timeout=process_timeout,
        )

    return load


def bind_work_tasks(
    driver: Any,
    specs: Iterable[WorkTaskSpec],
    *,
    declared_tasks: tuple[str, ...],
    artifacts: ContentAddressedCache,
    entry_allowlist: frozenset[str],
    verifier: SignatureVerifier | None = None,
    allow_subprocess: bool = False,
    process_timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> WorkBinder:
    """Bind a set of pinned task loads into ``driver``, fail-closed.

    Every load is verified before any bind; a failure on any load aborts the
    whole batch with no partial registration for the failing load.
    """
    binder = WorkBinder(
        driver,
        declared_tasks=declared_tasks,
        artifacts=artifacts,
        loader=bundle_task_loader(
            entry_allowlist=entry_allowlist,
            allow_subprocess=allow_subprocess,
            process_timeout=process_timeout,
        ),
        verifier=verifier,
    )
    for spec in specs:
        binder.bind(spec.load, expected_hash=spec.expected_hash)
    return binder


def run_bound_work_network(
    binder: WorkBinder,
    network: ComponentNetwork,
    *,
    driver: Any,
    step_limit: int | None = None,
) -> dict[str, Any]:
    """Run ``network`` over dynamically bound work tasks (fail-closed).

    Every non-child network component must map to a task the binder admitted
    (declared + pinned + verified); an unbound component refuses **before** any
    component runs. Child-routed components are left to the driver.

    Returns the :func:`~agent_centric.cbp.network.run_network` result shape.
    """
    tasks: list[str] = []
    for component in network.components():
        if component.child:
            continue
        try:
            binder.task(component.task)
        except WorkRuntimeError:
            return {
                "ok": False,
                "results": [],
                "completed": 0,
                "error": (
                    f"network component {component.id!r} task {component.task!r} is "
                    "not bound (fail-closed)"
                ),
            }
        tasks.append(component.task)
    if tasks:
        driver.configure(tasks=tuple(sorted(set(tasks))))
    return run_network(driver, network, step_limit=step_limit)
