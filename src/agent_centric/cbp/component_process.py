"""Process-isolated execution of a verified component entry (SPEC-0007 Phase 2).

This is the **declared gate** that lets a fetched component's own code run: a
bundle that passed hash + signature verification is extracted to a throwaway
directory and its entry is executed in a **separate child process**, exchanging
a single JSON request/response over stdin/stdout. Nothing from the bundle is
imported into the harness process.

Scope and honesty — read this before relying on it:

- This is **process isolation, not an OS sandbox.** A verified component runs
  with the same OS authority as the harness (filesystem, network). The trust
  anchor is still the signature + content hash; the process boundary bounds
  *crashes, hangs, namespace pollution, and output size*, not malicious code.
  Container/seccomp/VM sandboxing remains a later hardening phase.
- Execution is **opt-in and fail-closed**: :func:`run_component` is only reached
  when a caller (``boot_from_lock(..., allow_subprocess=True)``) asks for it, and
  every failure — non-zero exit, timeout, oversize or non-JSON output — raises
  :class:`ComponentProcessError`, never a partial success.
- The child runs with a **minimal environment** and ``python -I`` (isolated
  mode: no user site, no ``PYTHON*`` env, no cwd/script-dir on ``sys.path``); the
  component directory is added explicitly by the private runner.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path, PurePosixPath
from typing import Any

from ..contracts.component import EntryDescriptor, RuntimeKind

# Bump with the private runner's constant if the wire shape changes.
PROCESS_PROTOCOL = "component-process.v1"

DEFAULT_TIMEOUT_SECONDS = 30.0
MAX_OUTPUT_BYTES = 1_000_000

_RUNNER = Path(__file__).with_name("_component_runner.py")


class ComponentProcessError(Exception):
    """A component subprocess failed (fail-closed)."""


def _check_member(name: str) -> None:
    if not name or name.startswith("/") or ".." in PurePosixPath(name).parts:
        raise ComponentProcessError(f"unsafe bundle member: {name!r}")


def extract_bundle(bundle: bytes, dest: str | Path) -> Path:
    """Safely extract a verified bundle into ``dest`` (no path escape).

    Every member is validated (no absolute path, no ``..``) and extraction uses
    the ``data`` filter, so no symlink, device, or setuid entry can escape.

    Raises:
        ComponentProcessError: If the bundle is malformed or unsafe.
    """
    target = Path(dest)
    target.mkdir(parents=True, exist_ok=True)
    try:
        with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as tar:
            for member in tar.getmembers():
                _check_member(member.name)
            tar.extractall(target, filter="data")
    except (tarfile.TarError, OSError) as exc:
        raise ComponentProcessError(f"cannot extract bundle: {exc}") from exc
    return target


def run_component(
    bundle: bytes,
    entry: EntryDescriptor,
    payload: Any,
    *,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    max_output_bytes: int = MAX_OUTPUT_BYTES,
    python: str | None = None,
) -> Any:
    """Run a verified component entry in a child process and return its result.

    Args:
        bundle: The verified component bundle (hash + signature already checked).
        entry: The component's execution entry (a Python entrypoint).
        payload: A JSON-serializable value passed to the entry.
        timeout: Hard wall-clock limit in seconds (the child is killed on expiry).
        max_output_bytes: Reject output larger than this (bounds memory).
        python: The interpreter to launch (defaults to the harness's own).

    Returns:
        The entry's JSON-decoded result.

    Raises:
        ComponentProcessError: On a non-Python runtime, a launch/extraction
            failure, a non-zero exit, a timeout, or oversize/non-JSON output.
    """
    if entry.runtime != RuntimeKind.PYTHON:
        raise ComponentProcessError(f"runtime {entry.runtime!r} is not supported")
    if timeout <= 0:
        raise ComponentProcessError("timeout must be positive")
    if max_output_bytes <= 0:
        raise ComponentProcessError("max_output_bytes must be positive")
    if not _RUNNER.is_file():
        raise ComponentProcessError("component runner module is missing")

    executable = python or sys.executable
    request = json.dumps(
        {"protocol": PROCESS_PROTOCOL, "payload": payload}, separators=(",", ":")
    ).encode("utf-8")
    env = {"PATH": os.environ.get("PATH", ""), "LANG": "C.UTF-8"}

    with tempfile.TemporaryDirectory(prefix="component-run-") as tmp:
        workdir = extract_bundle(bundle, Path(tmp) / "component")
        try:
            completed = subprocess.run(
                [executable, "-I", str(_RUNNER), str(workdir), entry.entrypoint],
                input=request,
                capture_output=True,
                cwd=workdir,
                env=env,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as exc:
            raise ComponentProcessError(
                f"component timed out after {timeout}s"
            ) from exc
        except OSError as exc:
            raise ComponentProcessError(
                f"cannot launch component process: {exc}"
            ) from exc

    if completed.returncode != 0:
        stderr = completed.stderr.decode("utf-8", "replace").strip()
        raise ComponentProcessError(
            f"component exited {completed.returncode}: {stderr}"
        )
    if len(completed.stdout) > max_output_bytes:
        raise ComponentProcessError("component output exceeds the size limit")
    try:
        response = json.loads(completed.stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ComponentProcessError(
            f"component returned non-JSON output: {exc}"
        ) from exc
    if not isinstance(response, dict) or "result" not in response:
        raise ComponentProcessError("component response lacks a 'result' field")
    return response["result"]
