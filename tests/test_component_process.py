"""Tests for process-isolated component execution (SPEC-0007 Phase 2).

These prove the fail-closed contract of ``run_component``: a verified bundle's
entry runs in a child process and returns deterministic JSON, while a crash, a
timeout, garbage output, an oversize result, and an unsafe bundle are all
explicit failures — never a partial success.
"""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from agent_centric.cbp.component_bundle import build_bundle
from agent_centric.cbp.component_process import (
    ComponentProcessError,
    extract_bundle,
    run_component,
)
from agent_centric.contracts.component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
)

_MANIFEST = ComponentManifest(
    version=ComponentVersion.V1,
    name="proc",
    kind=ComponentKind.ATOMIC,
    entry=EntryDescriptor(runtime="python", entrypoint="mymod:run"),
    implements=("component.v1",),
)


def _bundle(code: str, *, entry: str = "mymod:run") -> tuple[bytes, EntryDescriptor]:
    payload = {("mymod.py"): code.encode("utf-8")}
    bundle = build_bundle(_MANIFEST, payload)
    descriptor = EntryDescriptor(runtime="python", entrypoint=entry)
    return bundle, descriptor


def _raw_tar(member: str, data: bytes = b"x") -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as tar:
        info = tarfile.TarInfo(member)
        info.size = len(data)
        tar.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


class TestRunComponent:
    def test_runs_verified_entry_and_is_deterministic(self) -> None:
        bundle, entry = _bundle(
            "def run(payload):\n    return {'doubled': payload['n'] * 2}\n"
        )
        assert run_component(bundle, entry, {"n": 3}) == {"doubled": 6}
        assert run_component(bundle, entry, {"n": 3}) == {"doubled": 6}

    def test_component_error_fails_closed(self) -> None:
        bundle, entry = _bundle("def run(payload):\n    raise ValueError('boom')\n")
        with pytest.raises(ComponentProcessError) as excinfo:
            run_component(bundle, entry, {})
        assert "exited 1" in str(excinfo.value)

    def test_stdout_noise_fails_closed(self) -> None:
        bundle, entry = _bundle(
            "def run(payload):\n    print('noise')\n    return 1\n"
        )
        with pytest.raises(ComponentProcessError):
            run_component(bundle, entry, {})

    def test_timeout_fails_closed(self) -> None:
        bundle, entry = _bundle(
            "import time\n"
            "def run(payload):\n"
            "    time.sleep(5)\n"
            "    return 1\n"
        )
        with pytest.raises(ComponentProcessError) as excinfo:
            run_component(bundle, entry, {}, timeout=0.5)
        assert "timed out" in str(excinfo.value)

    def test_oversize_output_fails_closed(self) -> None:
        bundle, entry = _bundle(
            "def run(payload):\n    return 'x' * 1000\n"
        )
        with pytest.raises(ComponentProcessError):
            run_component(bundle, entry, {}, max_output_bytes=16)

    def test_missing_module_fails_closed(self) -> None:
        bundle, entry = _bundle("def run(payload):\n    return 1\n", entry="ghost:run")
        with pytest.raises(ComponentProcessError):
            run_component(bundle, entry, {})

    def test_non_python_runtime_is_refused(self) -> None:
        bundle, _ = _bundle("def run(payload):\n    return 1\n")
        process_entry = EntryDescriptor(runtime="process", entrypoint="/bin/true")
        with pytest.raises(ComponentProcessError):
            run_component(bundle, process_entry, {})

    def test_non_positive_timeout_is_refused(self) -> None:
        bundle, entry = _bundle("def run(payload):\n    return 1\n")
        with pytest.raises(ComponentProcessError):
            run_component(bundle, entry, {}, timeout=0)


class TestExtractBundle:
    def test_extracts_regular_files(self, tmp_path: Path) -> None:
        bundle = _raw_tar("component.json", b"{}")
        dest = extract_bundle(bundle, tmp_path / "out")
        assert (dest / "component.json").read_bytes() == b"{}"

    def test_traversal_member_is_refused(self, tmp_path: Path) -> None:
        bundle = _raw_tar("../escape.txt")
        with pytest.raises(ComponentProcessError):
            extract_bundle(bundle, tmp_path / "out")

    def test_absolute_member_is_refused(self, tmp_path: Path) -> None:
        bundle = _raw_tar("/etc/passwd")
        with pytest.raises(ComponentProcessError):
            extract_bundle(bundle, tmp_path / "out")
