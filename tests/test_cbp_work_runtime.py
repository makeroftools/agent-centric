"""Tests for the work runtime (``cbp/work_runtime.py``; SPEC-0021).

Deterministic and offline: compilations are content-addressed and idempotent, and
the dynamic task-load binder verifies everything **before** any bind.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from agent_centric.cbp import (
    CbpDriver,
    ContentAddressedCache,
    MaterializedWork,
    WorkBinder,
    WorkRuntimeError,
    compile_and_materialize,
    materialize_work,
)
from agent_centric.cbp.signing import SignatureError
from agent_centric.contracts.capability import Capability
from agent_centric.contracts.work import (
    BuildRecipe,
    SourceRef,
    TaskLoad,
    VerifierSpec,
    WorkEnvelope,
    WorkRecipe,
    WorkSourceKind,
    compile_work,
)

_D = "a" * 64
_L = "b" * 64


def _recipe(**overrides: object) -> WorkRecipe:
    base: dict[str, object] = {
        "id": "tool",
        "build": BuildRecipe(language="python", runtime="python", toolchain=("python@3.13",)),
        "verifier": VerifierSpec(ref="verify:tool", digest=_L),
        "envelope": WorkEnvelope(timeout_seconds=10.0, max_steps=100),
        "source": SourceRef(
            repo_url="ssh://git/example/tool",
            commit="c" * 40,
            digest=_D,
            kind=WorkSourceKind.GIT,
        ),
        "grants": (Capability(name="fs.read"),),
    }
    base.update(overrides)
    return WorkRecipe(**base)  # type: ignore[arg-type]


class _FakeSigner:
    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(self._key_id.encode() + b":" + payload).hexdigest().encode()


class _FakeVerifier:
    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    def verify(self, payload: bytes, signature: bytes) -> None:
        expected = hashlib.sha256(
            self._key_id.encode() + b":" + payload
        ).hexdigest().encode()
        if signature != expected:
            raise SignatureError("bad signature")


class _FakeDriver:
    def __init__(self) -> None:
        self.registered: dict[str, tuple[Any, str]] = {}

    def register(self, name: str, fn: Any, *, source_url: str = "") -> None:
        self.registered[name] = (fn, source_url)


def _loader(task: str, artifact: bytes) -> Any:
    def entry(**kwargs: Any) -> Any:
        return kwargs

    return entry


def _bindable(tmp_path: Path) -> tuple[ContentAddressedCache, str]:
    cache = ContentAddressedCache(tmp_path / "cache")
    materialized = compile_and_materialize(_recipe(), cache)
    return cache, materialized.artifact_hash


class TestMaterializeWork:
    def test_materialize_is_content_addressed(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path / "cache")
        compilation = compile_work(_recipe())
        materialized = materialize_work(compilation, cache)
        assert materialized.artifact_hash == compilation.artifact_hash()
        assert cache.get(materialized.artifact_hash) == compilation.canonical_bytes()

    def test_materialize_is_idempotent(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path / "cache")
        first = materialize_work(compile_work(_recipe()), cache)
        second = materialize_work(compile_work(_recipe()), cache)
        assert first.artifact_hash == second.artifact_hash
        assert first == second

    def test_compile_and_materialize_is_deterministic(self, tmp_path: Path) -> None:
        a = compile_and_materialize(_recipe(), ContentAddressedCache(tmp_path / "a"))
        b = compile_and_materialize(_recipe(), ContentAddressedCache(tmp_path / "b"))
        assert a.artifact_hash == b.artifact_hash

    def test_materialized_work_round_trips(self, tmp_path: Path) -> None:
        materialized = compile_and_materialize(
            _recipe(), ContentAddressedCache(tmp_path / "cache")
        )
        assert MaterializedWork.from_dict(materialized.to_dict()) == materialized


class TestWorkBinder:
    def test_bind_admits_a_declared_pinned_task(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()
        binder = WorkBinder(
            driver, declared_tasks=("tool",), artifacts=cache, loader=_loader
        )
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        bound = binder.bind(load, expected_hash=digest)
        assert bound.task == "tool"
        assert bound.identity == digest
        assert driver.registered["tool"][1] == f"work://{digest}"
        assert binder.bound_tasks == ("tool",)

    def test_undeclared_task_refuses_without_binding(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()
        binder = WorkBinder(
            driver, declared_tasks=("other",), artifacts=cache, loader=_loader
        )
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        with pytest.raises(WorkRuntimeError):
            binder.bind(load, expected_hash=digest)
        assert driver.registered == {}
        assert binder.bound_tasks == ()

    def test_unpinned_task_refuses_without_binding(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()
        binder = WorkBinder(
            driver, declared_tasks=("tool",), artifacts=cache, loader=_loader
        )
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        with pytest.raises(WorkRuntimeError):
            binder.bind(load, expected_hash=_L)
        assert driver.registered == {}

    def test_missing_artifact_refuses_without_binding(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path / "cache")
        driver = _FakeDriver()
        binder = WorkBinder(
            driver, declared_tasks=("tool",), artifacts=cache, loader=_loader
        )
        load = TaskLoad(task="tool", content_hash=_D, artifact="work:tool")
        with pytest.raises(WorkRuntimeError):
            binder.bind(load, expected_hash=_D)
        assert driver.registered == {}

    def test_verifier_requires_a_signature(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()
        binder = WorkBinder(
            driver,
            declared_tasks=("tool",),
            artifacts=cache,
            loader=_loader,
            verifier=_FakeVerifier("k1"),
        )
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        with pytest.raises(WorkRuntimeError):
            binder.bind(load, expected_hash=digest)
        assert driver.registered == {}

    def test_verifier_accepts_a_valid_signature(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        artifact = cache.get(digest)
        assert artifact is not None
        signature = _FakeSigner("k1").sign(artifact).decode()
        driver = _FakeDriver()
        binder = WorkBinder(
            driver,
            declared_tasks=("tool",),
            artifacts=cache,
            loader=_loader,
            verifier=_FakeVerifier("k1"),
        )
        load = TaskLoad(
            task="tool", content_hash=digest, artifact="work:tool", signature=signature
        )
        assert binder.bind(load, expected_hash=digest).task == "tool"

    def test_verifier_rejects_a_bad_signature(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()
        binder = WorkBinder(
            driver,
            declared_tasks=("tool",),
            artifacts=cache,
            loader=_loader,
            verifier=_FakeVerifier("k1"),
        )
        load = TaskLoad(
            task="tool", content_hash=digest, artifact="work:tool", signature="deadbeef"
        )
        with pytest.raises(SignatureError):
            binder.bind(load, expected_hash=digest)
        assert driver.registered == {}

    def test_loader_failure_leaves_no_bind(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        driver = _FakeDriver()

        def _boom(task: str, artifact: bytes) -> Any:
            raise RuntimeError("loader exploded")

        binder = WorkBinder(
            driver, declared_tasks=("tool",), artifacts=cache, loader=_boom
        )
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        with pytest.raises(RuntimeError):
            binder.bind(load, expected_hash=digest)
        assert driver.registered == {}
        with pytest.raises(WorkRuntimeError):
            binder.task("tool")

    def test_duplicate_declared_tasks_refused(self, tmp_path: Path) -> None:
        cache = ContentAddressedCache(tmp_path / "cache")
        with pytest.raises(WorkRuntimeError):
            WorkBinder(
                _FakeDriver(),
                declared_tasks=("tool", "tool"),
                artifacts=cache,
                loader=_loader,
            )

    def test_binds_into_a_real_driver_and_runs(self, tmp_path: Path) -> None:
        cache, digest = _bindable(tmp_path)
        load = TaskLoad(task="tool", content_hash=digest, artifact="work:tool")
        with CbpDriver() as driver:
            binder = WorkBinder(
                driver, declared_tasks=("tool",), artifacts=cache, loader=_loader
            )
            binder.bind(load, expected_hash=digest)
            driver.configure(tasks=("tool",))
            response = driver.run("tool", {"value": 7})
            assert response.verified is True
            assert response.value == {"value": 7}
