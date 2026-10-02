"""Tests for the work boot bridge (``cbp/work_boot.py``; SPEC-0021 §5).

Deterministic and offline: work tasks are resolved from **verified bundles**
(content-addressed by the binder) and run through a ``network.v1``; an
allowlisted in-process entry runs in-tree, a non-allowlisted entry runs
process-isolated only when explicitly permitted, and any mismatch refuses before
a bind.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.cbp import (
    CbpDriver,
    ContentAddressedCache,
    WorkBinder,
    WorkBootError,
    WorkTaskSpec,
    bind_work_tasks,
    bundle_task_loader,
    run_bound_work_network,
)
from agent_centric.cbp.component_bundle import build_bundle
from agent_centric.cbp.network import Component, ComponentNetwork
from agent_centric.contracts.component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
)
from agent_centric.contracts.work import TaskLoad

_PROC_ENTRY = "mymod:run"
_SHELL_ENTRY = "agent_centric.cbp.shell_component:plan_delegation"


def _manifest(name: str, entry: str) -> ComponentManifest:
    return ComponentManifest(
        version=ComponentVersion.V1,
        name=name,
        kind=ComponentKind.ATOMIC,
        entry=EntryDescriptor(runtime="python", entrypoint=entry),
        implements=("component.v1",),
    )


def _proc_bundle(name: str = "proc") -> bytes:
    code = b"def run(payload):\n    return {'doubled': payload['n'] * 2}\n"
    return build_bundle(_manifest(name, _PROC_ENTRY), {"mymod.py": code})


def _shell_bundle(name: str = "shell") -> bytes:
    return build_bundle(_manifest(name, _SHELL_ENTRY))


def _cache_with(tmp_path: Path, bundle: bytes) -> tuple[ContentAddressedCache, str]:
    cache = ContentAddressedCache(tmp_path / "cache")
    digest = cache.put(bundle)
    return cache, digest


def _load(task: str, digest: str) -> TaskLoad:
    return TaskLoad(task=task, content_hash=digest, artifact=f"work:{task}")


class TestBundleTaskLoader:
    def test_inprocess_allowlisted_entry_runs(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _shell_bundle())
        driver = CbpDriver()
        try:
            binder = WorkBinder(
                driver,
                declared_tasks=("shell",),
                artifacts=cache,
                loader=bundle_task_loader(entry_allowlist=frozenset({_SHELL_ENTRY})),
            )
            bound = binder.bind(_load("shell", digest), expected_hash=digest)
            value = bound.entry(payload={"mission": "ship", "components": ["b", "a"]})
            assert value == {"mission": "ship", "order": ["a", "b"], "count": 2}
        finally:
            driver.close()

    def test_non_allowlisted_entry_refuses_without_subprocess(
        self, tmp_path: Path
    ) -> None:
        cache, digest = _cache_with(tmp_path, _shell_bundle())
        driver = CbpDriver()
        try:
            binder = WorkBinder(
                driver,
                declared_tasks=("shell",),
                artifacts=cache,
                loader=bundle_task_loader(entry_allowlist=frozenset()),
            )
            with pytest.raises(WorkBootError):
                binder.bind(_load("shell", digest), expected_hash=digest)
            assert binder.bound_tasks == ()
        finally:
            driver.close()

    def test_process_isolated_entry_runs(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _proc_bundle())
        driver = CbpDriver()
        try:
            binder = WorkBinder(
                driver,
                declared_tasks=("proc",),
                artifacts=cache,
                loader=bundle_task_loader(
                    entry_allowlist=frozenset(), allow_subprocess=True
                ),
            )
            bound = binder.bind(_load("proc", digest), expected_hash=digest)
            assert bound.entry(n=3) == {"doubled": 6}
        finally:
            driver.close()

    def test_manifest_name_mismatch_refuses(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _proc_bundle(name="other"))
        driver = CbpDriver()
        try:
            binder = WorkBinder(
                driver,
                declared_tasks=("proc",),
                artifacts=cache,
                loader=bundle_task_loader(
                    entry_allowlist=frozenset(), allow_subprocess=True
                ),
            )
            with pytest.raises(WorkBootError):
                binder.bind(_load("proc", digest), expected_hash=digest)
        finally:
            driver.close()

    def test_malformed_bundle_refuses(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, b"not a tar archive")
        driver = CbpDriver()
        try:
            binder = WorkBinder(
                driver,
                declared_tasks=("proc",),
                artifacts=cache,
                loader=bundle_task_loader(
                    entry_allowlist=frozenset(), allow_subprocess=True
                ),
            )
            with pytest.raises(WorkBootError):
                binder.bind(_load("proc", digest), expected_hash=digest)
        finally:
            driver.close()


class TestRunBoundWorkNetwork:
    def test_process_task_runs_through_a_network(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _proc_bundle())
        with CbpDriver() as driver:
            binder = bind_work_tasks(
                driver,
                [WorkTaskSpec(_load("proc", digest), digest)],
                declared_tasks=("proc",),
                artifacts=cache,
                entry_allowlist=frozenset(),
                allow_subprocess=True,
            )
            network = ComponentNetwork().add_component(
                Component(id="p", task="proc", args={"n": 3})
            )
            result = run_bound_work_network(binder, network, driver=driver)
            assert result["ok"] is True
            assert result["results"][0]["value"] == {"doubled": 6}

    def test_inprocess_task_runs_through_a_network(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _shell_bundle())
        with CbpDriver() as driver:
            binder = bind_work_tasks(
                driver,
                [WorkTaskSpec(_load("shell", digest), digest)],
                declared_tasks=("shell",),
                artifacts=cache,
                entry_allowlist=frozenset({_SHELL_ENTRY}),
            )
            network = ComponentNetwork().add_component(
                Component(
                    id="s",
                    task="shell",
                    args={"payload": {"mission": "ship", "components": ["b", "a"]}},
                )
            )
            result = run_bound_work_network(binder, network, driver=driver)
            assert result["ok"] is True
            assert result["results"][0]["value"]["order"] == ["a", "b"]

    def test_unbound_component_refuses_before_running(self, tmp_path: Path) -> None:
        cache, digest = _cache_with(tmp_path, _proc_bundle())
        with CbpDriver() as driver:
            binder = bind_work_tasks(
                driver,
                [WorkTaskSpec(_load("proc", digest), digest)],
                declared_tasks=("proc",),
                artifacts=cache,
                entry_allowlist=frozenset(),
                allow_subprocess=True,
            )
            network = ComponentNetwork().add_component(
                Component(id="g", task="ghost", args={})
            )
            result = run_bound_work_network(binder, network, driver=driver)
            assert result["ok"] is False
            assert "not bound" in result["error"]
            assert result["completed"] == 0
