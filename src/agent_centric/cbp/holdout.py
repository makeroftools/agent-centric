"""Isolated holdout validator core (SPEC-0019; the L3 gate).

This is the **pure library** behind ``tools/holdout-validate.py``. It is the
separate, context-gated process that is the only thing allowed to declare a run
**green** for L3. Being a library (not a script) makes it importable and
**component-ready**: a future ``component.v1`` wrapper can expose the pure
entry, and the authoritative validator still lives **outside** the composition it
validates (bootstrap circularity).

Contract (SPEC-0019):

* **Isolation.** Refuse to run unless ``CBP_HOLDOUT_CONTEXT=validator`` is set.
  As defence-in-depth the probe run also refuses on a privileged principal
  (``uid 0`` or the ``docker`` group, which is root-equivalent). That guard is
  **advisory in-process**; the real guarantee is environment-enforced (an
  unprivileged, distinct principal and an owner-only ``0700`` root). A
  ``--rehearsal`` run bypasses the guard but **records** ``isolation:
  rehearsal``; only an ``isolation: enforced`` record is authoritative.
* **Fail closed.** Load the suite, verify it against ``holdout.lock`` (probe
  contract + suite hash + per-scenario digests + TCB pins), and refuse on any
  drift **before** running a single probe.
* **Decision.** Each scenario runs ``RUNS = 3`` times on **fresh** instances; a
  scenario passes on a supermajority (``SUPERMAJORITY = 2``); high-priority
  scenarios require an all-run pass; semantic nondeterminism fails closed.
  ``ERROR`` runs are **infrastructure flake** — the only thing a supermajority
  may absorb.
* **Determinism.** Output is canonical JSON with scenarios sorted by id and the
  record is **wall-clock-free**, so a retried run is idempotent for its head.

Probe kinds (``holdout.v1``, additive):

* ``task`` — run the named task ``target`` (a ``service.v1`` task executable)
  and require ``expect_exit`` (default 0). A missing/unstartable task is
  ``ERROR`` (flake); a wrong exit is ``FAIL`` (semantic).
* ``service`` — load the scenario's operator-private
  ``holdout.deployment/v1`` descriptor (content-pinned by
  ``deployment_sha256``), boot the signed composition through the core harness
  (:func:`~agent_centric.cbp.component_boot.boot_from_lock`), run its declared
  observable, and compare the canonical-JSON projection to ``expect`` (or assert
  a fail-closed **refusal** when ``expect_refused`` is true). The descriptor is
  never hard-coded here; the validator knows no private path or slug.

The record adds the TCB pins (``validator_sha256``, ``probe_contract_sha256``,
``runtime``, ``platform``) plus ``lock_sha256`` / ``suite_sha256``. Exit codes:
``0`` green, ``1`` red, ``2`` usage, ``3`` refused.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform as _platform
import subprocess
import sys
import tempfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol, TextIO, cast

from ..contracts.components_lock import ComponentsLock
from .boot_network import run_booted_network
from .cache import ContentAddressedCache
from .component_boot import BootedTree, boot_from_lock
from .network import network_from_dict
from .resolver import DirectorySource, ResolveError
from .signing import GpgVerifier, MinisignVerifier, SignatureError, SignatureVerifier
from .trust import TrustStore, TrustWindow

CONTEXT_ENV = "CBP_HOLDOUT_CONTEXT"
CONTEXT_VALUE = "validator"
REHEARSAL_ENV = "CBP_HOLDOUT_REHEARSAL"

HOLDOUT_LOCK_SCHEMA = "holdout.lock/v1"
HOLDOUT_RUN_SCHEMA = "holdout.run/v1"
HOLDOUT_RECORD_SCHEMA = "holdout.record/v1"
HOLDOUT_SELFTEST_SCHEMA = "holdout.selftest/v1"
HOLDOUT_SUITE_SCHEMA = "holdout.v1"
DEPLOYMENT_SCHEMA = "holdout.deployment/v1"
TRUST_SCHEMA = "trust/v1"
PROBE_CONTRACT = "holdout.v1"
CHECK_INFO_STRING = "check"
SUPPORTED_PROBE_KINDS = ("task", "service")
SUPPORTED_OBSERVABLE_KINDS = ("root", "network")

VALIDATOR_VERSION = "holdout-validator/2"
HOLDOUT_COMPONENT = "holdout-validator"
HOLDOUT_ENTRYPOINT = "agent_centric.cbp.holdout:component_entry"
DEFAULT_HOLDOUT_ROOT = Path.home() / ".cbp" / "holdout"

# The pinned probe-contract vocabulary (a content-addressable description of the
# declarative surface, not the implementation bytes).
PROBE_CONTRACT_SPEC: dict[str, object] = {
    "contract": PROBE_CONTRACT,
    "probe_kinds": list(SUPPORTED_PROBE_KINDS),
    "observable_kinds": list(SUPPORTED_OBSERVABLE_KINDS),
    "run_schema": HOLDOUT_RUN_SCHEMA,
}

RUNS = 3
SUPERMAJORITY = 2
DEFAULT_THRESHOLD = 1.0

EXIT_OK = 0
EXIT_RED = 1
EXIT_USAGE = 2
EXIT_REFUSED = 3

_HEX64_LEN = 64


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


@dataclass(frozen=True)
class TrustWindowSpec:
    """A parsed trust window (``trust/v1``)."""

    key_id: str
    algorithm: str
    public_key: str
    prefix: str
    not_before: int
    not_after: int


@dataclass(frozen=True)
class TrustSpec:
    """A parsed deployment trust root (a ``trust/v1`` store or an inline key)."""

    kind: str
    windows: tuple[TrustWindowSpec, ...]


@dataclass(frozen=True)
class Deployment:
    """A parsed ``holdout.deployment/v1`` descriptor (operator-private)."""

    schema: str
    repo_root: Path
    composition_dir: Path
    lock_path: Path
    lock_signature_path: Path
    lock_hash: str
    trust: TrustSpec
    entry_allowlist: frozenset[str]
    observable: Mapping[str, object]
    allow_subprocess: bool


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


def probe_contract_sha256() -> str:
    """The content address of the pinned ``holdout.v1`` probe vocabulary."""
    return sha256_hex(_canonical_json(PROBE_CONTRACT_SPEC))


def validator_sha256() -> str:
    """The content address of the validator's TCB bytes (this module)."""
    return sha256_hex(Path(__file__).read_bytes())


def _is_hex64(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _HEX64_LEN
        and all(character in "0123456789abcdef" for character in value)
    )


def _euid() -> int:
    """The effective uid (0 on a platform without ``geteuid``)."""
    return os.geteuid() if hasattr(os, "geteuid") else 0


def _process_groups() -> tuple[int, ...]:
    """The process's supplementary group ids."""
    return tuple(os.getgroups())


def _docker_gid() -> int | None:
    """The ``docker`` group id, if the group exists on this platform."""
    try:
        import grp  # noqa: PLC0415 - Unix-only, imported on demand

        return grp.getgrnam("docker").gr_gid
    except (ImportError, KeyError):
        return None


def principal_refusal() -> str | None:
    """Return why the principal is privileged, or ``None`` if it is not.

    Defence-in-depth only: it refuses ``uid 0`` and the ``docker`` group (docker
    is root-equivalent). Host/principal isolation is **environment-enforced**;
    this guard cannot prove it from inside the process.
    """
    if _euid() == 0:
        return "the validator must not run as root (uid 0)"
    docker_gid = _docker_gid()
    if docker_gid is not None and docker_gid in _process_groups():
        return "the validator must not run in the docker group (root-equivalent)"
    return None


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
    return cast("dict[str, object]", data)


def private_root() -> Path:
    """The operator-private holdout root (never inside a public repo)."""
    override = os.environ.get("CBP_HOLDOUT_ROOT")
    return Path(override) if override else DEFAULT_HOLDOUT_ROOT


def default_deployments_root() -> Path:
    """The default descriptor root (``$CBP_HOLDOUT_ROOT/deployments``)."""
    return private_root() / "deployments"


def enforce_private_suite(suite_dir: Path) -> None:
    """Refuse unless the suite is owner-only (not group- or other-accessible)."""
    if not suite_dir.exists():
        raise Refusal(f"no suite directory at {suite_dir}")
    mode = suite_dir.stat().st_mode
    if mode & 0o077:
        raise Refusal(
            f"suite directory {suite_dir} is accessible to group/other; "
            "holdout scenarios must be owner-only (chmod 700)"
        )


def _record_fingerprint(record: Mapping[str, object]) -> str:
    payload = {key: value for key, value in record.items() if key != "fingerprint"}
    return sha256_hex(_canonical_json(payload))


def _lock_file(handle: TextIO) -> None:
    """Take an exclusive advisory lock (POSIX); a no-op where unavailable."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_EX)


def _unlock_file(handle: TextIO) -> None:
    """Release the advisory lock taken by :func:`_lock_file`."""
    try:
        import fcntl
    except ImportError:  # pragma: no cover - non-POSIX
        return
    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def append_record(ledger_path: Path, record: Mapping[str, object]) -> bool:
    """Append a run record idempotently; return True iff appended (not a dup).

    The read-check-append is serialized with an exclusive advisory lock, so the
    idempotency guarantee holds under retry **and** under concurrent validators:
    two processes can never both append the same content fingerprint. The write
    is flushed and ``fsync``-ed before the lock is released, so an acknowledged
    record is durable.
    """
    fingerprint = _record_fingerprint(record)
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    entry = {**record, "fingerprint": fingerprint}
    line = json.dumps(entry, sort_keys=True, separators=(",", ":")) + "\n"
    with ledger_path.open("a+", encoding="utf-8") as handle:
        _lock_file(handle)
        try:
            handle.seek(0)
            for existing_line in handle.read().splitlines():
                if not existing_line.strip():
                    continue
                try:
                    existing = json.loads(existing_line)
                except json.JSONDecodeError:
                    continue
                if (
                    isinstance(existing, dict)
                    and existing.get("fingerprint") == fingerprint
                ):
                    return False
            handle.seek(0, os.SEEK_END)
            handle.write(line)
            handle.flush()
            os.fsync(handle.fileno())
        finally:
            _unlock_file(handle)
    os.chmod(ledger_path, 0o600)
    return True


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
    if kind == "task":
        if not isinstance(probe.get("target"), str):
            raise Refusal("task probe needs a string 'target'")
        if not isinstance(probe.get("expect_exit", 0), int):
            raise Refusal("'expect_exit' must be an integer")
        return dict(probe)
    if not isinstance(probe.get("deployment"), str) or not probe["deployment"]:
        raise Refusal("service probe needs a non-empty string 'deployment'")
    if not _is_hex64(probe.get("deployment_sha256")):
        raise Refusal("service probe needs a 64-hex 'deployment_sha256'")
    expect_refused = probe.get("expect_refused", False)
    if not isinstance(expect_refused, bool):
        raise Refusal("'expect_refused' must be a boolean")
    if not expect_refused and "expect" not in probe:
        raise Refusal("service probe needs 'expect' or 'expect_refused': true")
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
    """Build a deterministic ``holdout.lock`` pinning the suite and the TCB."""
    return {
        "schema": HOLDOUT_LOCK_SCHEMA,
        "probe_contract": PROBE_CONTRACT,
        "probe_contract_sha256": probe_contract_sha256(),
        "validator_sha256": validator_sha256(),
        "suite_sha256": _suite_sha256(suite),
        "scenarios": {scenario.scenario_id: scenario.digest for scenario in suite.scenarios},
    }


def verify_suite(suite: Suite, lock: Mapping[str, object]) -> None:
    """Refuse (fail-closed) unless the suite matches the lock exactly."""
    if lock.get("schema") != HOLDOUT_LOCK_SCHEMA:
        raise Refusal("holdout lock has the wrong schema")
    if lock.get("probe_contract") != PROBE_CONTRACT:
        raise Refusal("holdout lock pins a different probe contract")
    if lock.get("probe_contract_sha256") != probe_contract_sha256():
        raise Refusal("holdout probe contract drifted (TCB pin mismatch)")
    if lock.get("validator_sha256") != validator_sha256():
        raise Refusal("holdout validator content drifted (TCB pin mismatch)")
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


# -- deployment descriptors + trust -----------------------------------------

def _resolve_under(base: Path, value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else (base / path).resolve()


def _confined_child(root: Path, name: str) -> Path:
    """Resolve ``name`` under ``root``, refusing any escape (fail-closed).

    A scenario's ``deployment`` is a private-root-relative name; an absolute
    path or a ``..`` traversal (including via a symlink) refuses before any
    descriptor is read.
    """
    resolved_root = root.resolve()
    candidate = (root / name).resolve()
    if not candidate.is_relative_to(resolved_root):
        raise Refusal(
            f"deployment descriptor {name!r} escapes the deployments root {root} "
            "(fail-closed)"
        )
    return candidate


def _verifier_from_spec(algorithm: str, public_key: str, prefix: str) -> SignatureVerifier:
    if algorithm == "offline-test":
        if not prefix:
            raise Refusal("offline-test trust needs a 'prefix'")
        return _PrefixVerifier(prefix)
    if algorithm == "minisign":
        if not public_key:
            raise Refusal("minisign trust needs a 'public_key'")
        return MinisignVerifier(public_key)
    if algorithm == "gpg":
        return GpgVerifier()
    raise Refusal(f"unsupported trust algorithm: {algorithm!r}")


class _PrefixVerifier:
    """A clearly-labelled **test** verifier (never the authoritative trust root)."""

    def __init__(self, prefix: str) -> None:
        self._prefix = prefix.encode("utf-8")

    def verify(self, payload: bytes, signature: bytes) -> None:
        expected = hashlib.sha256(self._prefix + payload).hexdigest().encode("utf-8")
        if signature != expected:
            raise SignatureError("offline test signature does not verify")


def trust_verifier(spec: TrustSpec) -> SignatureVerifier:
    """Build the verifier for a parsed trust spec (fail-closed)."""
    if spec.kind != "trust_store":
        return _verifier_from_spec(
            spec.windows[0].algorithm, spec.windows[0].public_key, spec.windows[0].prefix
        )
    windows: list[TrustWindow] = []
    for window in spec.windows:
        windows.append(
            TrustWindow(
                key_id=window.key_id,
                verifier=_verifier_from_spec(window.algorithm, window.public_key, window.prefix),
                not_before=window.not_before,
                not_after=window.not_after,
            )
        )
    return TrustStore(windows)


def _parse_trust(value: object, base_dir: Path) -> TrustSpec:
    if not isinstance(value, dict):
        raise Refusal("deployment trust must be an object")
    kind = value.get("kind")
    if kind == "trust_store":
        path = value.get("path")
        if not isinstance(path, str) or not path:
            raise Refusal("trust_store trust needs a 'path'")
        document = _read_json_object(_resolve_under(base_dir, path))
        if document.get("schema") != TRUST_SCHEMA:
            raise Refusal("trust store has the wrong schema")
        raw_windows = document.get("windows")
        if not isinstance(raw_windows, list) or not raw_windows:
            raise Refusal("trust store has no windows")
        windows: list[TrustWindowSpec] = []
        for raw in raw_windows:
            if not isinstance(raw, dict):
                raise Refusal("each trust window must be an object")
            key_id = raw.get("key_id")
            algorithm = raw.get("algorithm")
            not_before = raw.get("not_before")
            not_after = raw.get("not_after")
            if not isinstance(key_id, str) or not key_id:
                raise Refusal("trust window needs a non-empty 'key_id'")
            if not isinstance(algorithm, str):
                raise Refusal("trust window needs an 'algorithm'")
            if not isinstance(not_before, int) or not isinstance(not_after, int):
                raise Refusal("trust window needs integer 'not_before'/'not_after'")
            windows.append(
                TrustWindowSpec(
                    key_id=key_id,
                    algorithm=algorithm,
                    public_key=str(raw.get("public_key", "")),
                    prefix=str(raw.get("prefix", "")),
                    not_before=not_before,
                    not_after=not_after,
                )
            )
        return TrustSpec(kind=kind, windows=tuple(windows))
    if kind == "inline":
        algorithm = value.get("algorithm")
        if not isinstance(algorithm, str):
            raise Refusal("inline trust needs an 'algorithm'")
        return TrustSpec(
            kind=kind,
            windows=(
                TrustWindowSpec(
                    key_id=str(value.get("key_id", "inline")),
                    algorithm=algorithm,
                    public_key=str(value.get("public_key", "")),
                    prefix=str(value.get("prefix", "")),
                    not_before=0,
                    not_after=2**63 - 1,
                ),
            ),
        )
    raise Refusal(f"unsupported trust kind: {kind!r}")


def _validate_observable(value: object) -> Mapping[str, object]:
    if not isinstance(value, dict):
        raise Refusal("deployment observable must be an object")
    kind = value.get("kind")
    if kind not in SUPPORTED_OBSERVABLE_KINDS:
        raise Refusal(f"unsupported observable kind: {kind!r}")
    if kind == "root":
        args = value.get("args", {})
        if not isinstance(args, Mapping):
            raise Refusal("root observable 'args' must be an object")
    else:
        if not isinstance(value.get("network"), Mapping):
            raise Refusal("network observable needs a 'network' object")
        spawn = value.get("spawn", [])
        if not isinstance(spawn, list):
            raise Refusal("network observable 'spawn' must be a list")
    return dict(value)


def parse_deployment(document: object, *, base_dir: Path) -> Deployment:
    """Parse and validate a ``holdout.deployment/v1`` descriptor (fail-closed)."""
    if not isinstance(document, dict):
        raise Refusal("deployment descriptor must be a JSON object")
    if document.get("schema") != DEPLOYMENT_SCHEMA:
        raise Refusal("deployment descriptor has the wrong schema")
    repo_root = document.get("repo_root")
    composition_dir = document.get("composition_dir")
    lock = document.get("lock", "components.lock")
    lock_signature = document.get("lock_signature", "components.lock.sig")
    lock_hash = document.get("lock_hash")
    if not isinstance(repo_root, str) or not repo_root:
        raise Refusal("deployment needs a 'repo_root'")
    if not isinstance(composition_dir, str) or not composition_dir:
        raise Refusal("deployment needs a 'composition_dir'")
    if not isinstance(lock, str) or not isinstance(lock_signature, str):
        raise Refusal("deployment 'lock'/'lock_signature' must be strings")
    if not _is_hex64(lock_hash):
        raise Refusal("deployment 'lock_hash' must be 64-hex")
    root = _resolve_under(base_dir, repo_root)
    raw_allowlist = document.get("entry_allowlist")
    if not isinstance(raw_allowlist, list) or not raw_allowlist:
        raise Refusal("deployment needs a non-empty 'entry_allowlist'")
    if not all(isinstance(entry, str) and entry for entry in raw_allowlist):
        raise Refusal("deployment 'entry_allowlist' entries must be non-empty strings")
    allow_subprocess = document.get("allow_subprocess", False)
    if not isinstance(allow_subprocess, bool):
        raise Refusal("deployment 'allow_subprocess' must be a boolean")
    return Deployment(
        schema=DEPLOYMENT_SCHEMA,
        repo_root=root,
        composition_dir=_resolve_under(root, composition_dir),
        lock_path=_resolve_under(root, lock),
        lock_signature_path=_resolve_under(root, lock_signature),
        lock_hash=cast("str", lock_hash),
        trust=_parse_trust(document.get("trust"), base_dir),
        entry_allowlist=frozenset(raw_allowlist),
        observable=_validate_observable(document.get("observable")),
        allow_subprocess=allow_subprocess,
    )


def load_deployment(path: Path, expected_sha256: str) -> Deployment:
    """Load a descriptor, verifying its content address before anything runs."""
    if not path.is_file():
        raise Refusal(f"no deployment descriptor at {path}")
    raw = path.read_bytes()
    actual = sha256_hex(raw)
    if actual != expected_sha256:
        raise Refusal(
            f"deployment descriptor drift: expected {expected_sha256}, got {actual}"
        )
    try:
        document = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise Refusal(f"deployment descriptor is not valid JSON: {exc}") from exc
    return parse_deployment(document, base_dir=path.parent)


def verify_deployment_available(deployment: Deployment) -> None:
    """Refuse (before running) unless the descriptor's references exist."""
    if not deployment.lock_path.is_file():
        raise Refusal(f"deployment lock not found: {deployment.lock_path}")
    if not deployment.lock_signature_path.is_file():
        raise Refusal(
            f"deployment lock signature not found: {deployment.lock_signature_path}"
        )
    if not deployment.composition_dir.is_dir():
        raise Refusal(f"deployment composition not found: {deployment.composition_dir}")
    trust_verifier(deployment.trust)


def preflight(
    suite: Suite, deployment_root: Path, *, allow_offline_test: bool
) -> None:
    """Verify every service scenario's descriptor before running any probe."""
    for scenario in suite.scenarios:
        if scenario.probe.get("kind") != "service":
            continue
        path = _confined_child(deployment_root, str(scenario.probe["deployment"]))
        deployment = load_deployment(path, str(scenario.probe["deployment_sha256"]))
        verify_deployment_available(deployment)
        if not allow_offline_test and any(
            window.algorithm == "offline-test" for window in deployment.trust.windows
        ):
            raise Refusal(
                "offline-test trust is rehearsal-only; use a real trust root for an "
                "authoritative run"
            )


# -- probe backends ----------------------------------------------------------

class TaskBackend:
    """Run a named task executable (a ``service.v1`` task) in a fresh workdir."""

    def __init__(self, tasks_dir: Path, workdir: Path) -> None:
        self._tasks_dir = tasks_dir
        self._workdir = workdir

    def run(self, probe: Mapping[str, object]) -> RunOutcome:
        executable = self._tasks_dir / str(probe["target"])
        if not executable.is_file():
            return RunOutcome.ERROR
        raw_expect = probe.get("expect_exit", 0)
        expect = raw_expect if isinstance(raw_expect, int) else 0
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


class ServiceBackend:
    """Boot a signed composition through the harness and run one observable."""

    def __init__(self, deployment_root: Path, workdir: Path) -> None:
        self._deployment_root = deployment_root
        self._workdir = workdir

    def run(self, probe: Mapping[str, object]) -> RunOutcome:
        deployment = load_deployment(
            _confined_child(self._deployment_root, str(probe["deployment"])),
            str(probe["deployment_sha256"]),
        )
        expect_refused = bool(probe.get("expect_refused", False))
        try:
            value = self._boot_and_run(deployment)
        except (ResolveError, SignatureError, ValueError) as exc:
            del exc
            return RunOutcome.PASS if expect_refused else RunOutcome.FAIL
        if expect_refused:
            return RunOutcome.FAIL
        if _canonical_json(value) == _canonical_json(probe.get("expect")):
            return RunOutcome.PASS
        return RunOutcome.FAIL

    def _boot_and_run(self, deployment: Deployment) -> object:
        scratch = self._workdir
        scratch.mkdir(parents=True, exist_ok=True)
        verifier = trust_verifier(deployment.trust)
        signature = deployment.lock_signature_path.read_text(encoding="utf-8").strip()
        lock = ComponentsLock.from_dict(_read_json_object(deployment.lock_path))
        tree = boot_from_lock(
            lock,
            source=DirectorySource(deployment.composition_dir),
            cache=ContentAddressedCache(scratch / "cache"),
            verifier=verifier,
            entry_allowlist=deployment.entry_allowlist,
            state_root=scratch / "state",
            expected_lock_hash=deployment.lock_hash,
            lock_signature=signature,
            lock_verifier=verifier,
            allow_subprocess=deployment.allow_subprocess,
        )
        return _run_observable(tree, deployment.observable)


class HoldoutBackend:
    """Dispatch a probe to its backend (task executable or service deployment)."""

    def __init__(self, tasks_dir: Path, deployment_root: Path, workdir: Path) -> None:
        self._task = TaskBackend(tasks_dir, workdir)
        self._service = ServiceBackend(deployment_root, workdir)

    def run(self, probe: Mapping[str, object]) -> RunOutcome:
        kind = probe.get("kind")
        if kind == "task":
            return self._task.run(probe)
        if kind == "service":
            return self._service.run(probe)
        return RunOutcome.ERROR


def _run_observable(tree: BootedTree, observable: Mapping[str, object]) -> object:
    kind = observable.get("kind")
    if kind == "root":
        args = observable.get("args", {})
        if not isinstance(args, Mapping):
            raise ValueError("root observable 'args' must be an object")
        return tree.run(dict(args))
    if kind == "network":
        from .driver import CbpDriver  # noqa: PLC0415 - avoid the zmq import for pure ops

        network = network_from_dict(cast("dict[str, object]", observable["network"]))
        driver = CbpDriver()
        try:
            for entry in cast("list[object]", observable.get("spawn", [])):
                if isinstance(entry, Mapping) and isinstance(entry.get("name"), str):
                    driver.spawn(
                        str(entry["name"]), kind=str(entry.get("kind", "model"))
                    )
            return run_booted_network(tree, network, driver=driver)
        finally:
            driver.close()
    raise ValueError(f"unknown observable kind: {kind!r}")


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


# -- documents ---------------------------------------------------------------

def _verdict_document(verdict: RunVerdict, *, threshold: float) -> dict[str, object]:
    return {
        "schema": HOLDOUT_RUN_SCHEMA,
        "validator_version": VALIDATOR_VERSION,
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


def _record_document(
    verdict: RunVerdict,
    *,
    threshold: float,
    lock_sha256: str,
    suite_sha256: str,
    isolation: str,
) -> dict[str, object]:
    """A wall-clock-free ``holdout.record/v1`` with the full TCB pins."""
    document = _verdict_document(verdict, threshold=threshold)
    document.update(
        {
            "schema": HOLDOUT_RECORD_SCHEMA,
            "validator_sha256": validator_sha256(),
            "probe_contract": PROBE_CONTRACT,
            "probe_contract_sha256": probe_contract_sha256(),
            "runtime": f"python-{_platform.python_version()}",
            "platform": _platform.system().lower(),
            "isolation": isolation,
            "lock_sha256": lock_sha256,
            "suite_sha256": suite_sha256,
        }
    )
    return document


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


# -- self-test ---------------------------------------------------------------

_SCENARIO_TEMPLATE = """---
id: HO-SELF
service: cbp
feature: self-test
priority: high
---

The named task must exit {exit_code}. This synthetic scenario is built and
consumed entirely inside the validator self-test.

```check
{{"kind": "task", "target": "check.sh", "expect_exit": 0}}
```
"""


@dataclass(frozen=True)
class _SelfTestCase:
    suite: Path
    lock: Path
    tasks: Path
    ledger: Path


def _selftest_case(root: Path, name: str, *, exit_code: int) -> _SelfTestCase:
    base = root / name
    suite = base / "suite"
    suite.mkdir(parents=True)
    os.chmod(suite, 0o700)
    tasks = base / "tasks"
    tasks.mkdir()
    script = tasks / "check.sh"
    script.write_text(f"#!/usr/bin/env bash\nexit {exit_code}\n", encoding="utf-8")
    os.chmod(script, 0o755)
    (suite / "HO-SELF.md").write_text(
        _SCENARIO_TEMPLATE.format(exit_code=exit_code), encoding="utf-8"
    )
    lock = base / "holdout.lock"
    lock.write_text(
        json.dumps(build_lock(load_suite(suite)), sort_keys=True), encoding="utf-8"
    )
    return _SelfTestCase(suite=suite, lock=lock, tasks=tasks, ledger=base / "runs.jsonl")


def _selftest_run(case: _SelfTestCase) -> tuple[dict[str, object], int]:
    argv = [
        "--suite",
        str(case.suite),
        "--lock",
        str(case.lock),
        "--tasks-dir",
        str(case.tasks),
        "--ledger",
        str(case.ledger),
        "--rehearsal",
    ]
    return run(argv, env={CONTEXT_ENV: CONTEXT_VALUE})


def run_self_test() -> tuple[dict[str, object], int]:
    """Prove the validator separates a known-good from a known-bad suite."""
    checks: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(prefix="holdout-selftest-") as tmp:
        root = Path(tmp)
        good = _selftest_case(root, "good", exit_code=0)
        bad = _selftest_case(root, "bad", exit_code=1)
        good_doc, good_code = _selftest_run(good)
        checks.append(
            {
                "name": "known-good-is-green",
                "ok": good_code == EXIT_OK and good_doc.get("verdict") == "green",
            }
        )
        good_again_doc, good_again_code = _selftest_run(good)
        lines = good.ledger.read_text(encoding="utf-8").splitlines()
        checks.append(
            {
                "name": "rerun-is-identical-and-ledger-is-single",
                "ok": (
                    good_again_code == good_code
                    and good_again_doc == good_doc
                    and len(lines) == 1
                ),
            }
        )
        bad_doc, bad_code = _selftest_run(bad)
        checks.append(
            {
                "name": "known-bad-is-red",
                "ok": bad_code == EXIT_RED and bad_doc.get("verdict") == "red",
            }
        )
    ok = all(bool(check["ok"]) for check in checks)
    return (
        {
            "schema": HOLDOUT_SELFTEST_SCHEMA,
            "ok": ok,
            "validator_sha256": validator_sha256(),
            "probe_contract_sha256": probe_contract_sha256(),
            "checks": checks,
        },
        EXIT_OK if ok else EXIT_RED,
    )


# -- component-ready boundary ------------------------------------------------

def component_contract() -> dict[str, object]:
    """The ``component.v1``-ready description of the future holdout wrapper.

    The authoritative validator lives **outside** the composition it validates
    (bootstrap circularity), so this wrapper exposes only the pure operations
    (build-lock / decide / self-test); it never boots a composition from within.
    """
    return {
        "version": "component.v1",
        "name": HOLDOUT_COMPONENT,
        "kind": "atomic",
        "implements": ["component.v1"],
        "entry": {"runtime": "python", "entrypoint": HOLDOUT_ENTRYPOINT},
        "tasks": ["build-lock", "decide", "self-test", "contract"],
        "probe_contract": PROBE_CONTRACT,
        "validator_version": VALIDATOR_VERSION,
        "note": (
            "component-ready boundary only; the L3 authoritative run happens in a "
            "separate process outside the composition under test"
        ),
    }


def component_entry(payload: Mapping[str, object]) -> dict[str, object]:
    """A pure, component.v1-ready entry dispatching the validator's pure ops.

    Fail-closed on a malformed payload: a missing/invalid argument refuses with
    :class:`Refusal` rather than raising a bare ``KeyError``/``ValueError``.
    """
    command = payload.get("command")
    if command == "contract":
        return component_contract()
    if command == "build-lock":
        suite = payload.get("suite")
        if not isinstance(suite, str) or not suite:
            raise Refusal("build-lock requires a non-empty string 'suite'")
        suite_dir = Path(suite)
        enforce_private_suite(suite_dir)
        return {"ok": True, "lock": build_lock(load_suite(suite_dir))}
    if command == "decide":
        raw_scenarios = payload.get("scenarios")
        if isinstance(raw_scenarios, list):
            raw_scenarios = {"scenarios": raw_scenarios}
        scenarios = _parse_decide_scenarios(raw_scenarios)
        try:
            threshold = float(payload.get("threshold", DEFAULT_THRESHOLD))  # type: ignore[arg-type]
        except (TypeError, ValueError) as exc:
            raise Refusal(f"invalid threshold: {exc}") from exc
        verdict = decide(scenarios, threshold=threshold)
        return {
            "ok": verdict.green,
            "verdict": _verdict_document(verdict, threshold=threshold),
        }
    raise Refusal(f"unknown holdout component command: {command!r}")


# -- CLI ---------------------------------------------------------------------

def _execute(args: argparse.Namespace, env: Mapping[str, str]) -> tuple[dict[str, object], int]:
    if args.self_test:
        return run_self_test()
    if args.decide:
        scenarios = _parse_decide_scenarios(_read_json_object(Path(args.decide)))
        verdict = decide(scenarios, threshold=args.threshold)
        return (
            _verdict_document(verdict, threshold=args.threshold),
            EXIT_OK if verdict.green else EXIT_RED,
        )
    if args.build_lock:
        suite_dir = Path(args.suite)
        enforce_private_suite(suite_dir)
        return build_lock(load_suite(suite_dir)), EXIT_OK

    reason = principal_refusal()
    rehearsal = args.rehearsal or env.get(REHEARSAL_ENV) == "1"
    if reason is not None and not rehearsal:
        raise Refusal(
            f"{reason}; use --rehearsal for a non-authoritative rehearsal run"
        )
    isolation = "enforced" if reason is None and not rehearsal else "rehearsal"

    lock_path = Path(args.lock)
    if not lock_path.is_file():
        raise Refusal(f"no holdout lock at {lock_path} (no suite to validate)")
    suite_dir = Path(args.suite)
    enforce_private_suite(suite_dir)
    suite = load_suite(suite_dir)
    verify_suite(suite, _read_json_object(lock_path))
    deployment_root = Path(args.deployments_dir)
    preflight(suite, deployment_root, allow_offline_test=isolation == "rehearsal")

    tasks_dir = Path(args.tasks_dir)
    base = Path(args.workdir) if args.workdir else Path(tempfile.mkdtemp(prefix="holdout-"))

    def make_backend(attempt: int) -> ProbeBackend:
        workdir = base / f"run-{attempt}"
        workdir.mkdir(parents=True, exist_ok=True)
        return HoldoutBackend(tasks_dir, deployment_root, workdir)

    verdict = run_suite(suite, make_backend=make_backend, threshold=args.threshold)
    document = _record_document(
        verdict,
        threshold=args.threshold,
        lock_sha256=sha256_hex(lock_path.read_bytes()),
        suite_sha256=_suite_sha256(suite),
        isolation=isolation,
    )
    append_record(Path(args.ledger), document)
    return document, (EXIT_OK if verdict.green else EXIT_RED)


def run(argv: Sequence[str] | None, *, env: Mapping[str, str]) -> tuple[dict[str, object], int]:
    """Validate and return ``(document, exit_code)``, or raise :class:`Refusal`."""
    args = build_parser().parse_args(argv)
    if not context_is_validator(env):
        raise Refusal(
            f"outside the validator context; set {CONTEXT_ENV}={CONTEXT_VALUE} "
            "(the authoring agent must never run the validator)"
        )
    return _execute(args, env)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="holdout-validate",
        description="Isolated holdout validator (SPEC-0019). Separate process.",
    )
    parser.add_argument(
        "--lock",
        default=str(private_root() / "holdout.lock"),
        help="path to the holdout lock that pins the suite",
    )
    parser.add_argument(
        "--suite",
        default=str(private_root() / "suite"),
        help="directory of scenario Markdown files (must be owner-only)",
    )
    parser.add_argument(
        "--ledger",
        default=str(private_root() / "runs.jsonl"),
        help="append-only holdout ledger (idempotent per record)",
    )
    parser.add_argument(
        "--tasks-dir",
        default="tasks",
        help="directory holding the named task executables probes run",
    )
    parser.add_argument(
        "--deployments-dir",
        default=str(default_deployments_root()),
        help=(
            "directory holding holdout.deployment/v1 descriptors "
            "(default: $CBP_HOLDOUT_ROOT/deployments)"
        ),
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
        "--build-lock",
        action="store_true",
        help="print the holdout.lock for --suite (operator pinning; no probes)",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="run the synthetic known-good/known-bad self-test and exit 0 iff correct",
    )
    parser.add_argument(
        "--rehearsal",
        action="store_true",
        help="allow a privileged principal; records isolation=rehearsal (not authoritative)",
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
        document, code = run(argv, env=resolved_env)
    except Refusal as exc:
        sys.stdout.write(
            json.dumps(_refusal_document(str(exc)), sort_keys=True, separators=(",", ":"))
            + "\n"
        )
        return EXIT_REFUSED
    sys.stdout.write(json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n")
    return code


__all__ = [
    "CONTEXT_ENV",
    "CONTEXT_VALUE",
    "REHEARSAL_ENV",
    "HOLDOUT_LOCK_SCHEMA",
    "HOLDOUT_RUN_SCHEMA",
    "HOLDOUT_RECORD_SCHEMA",
    "HOLDOUT_SELFTEST_SCHEMA",
    "DEPLOYMENT_SCHEMA",
    "PROBE_CONTRACT",
    "SUPPORTED_PROBE_KINDS",
    "VALIDATOR_VERSION",
    "HOLDOUT_ENTRYPOINT",
    "ProbeBackend",
    "Priority",
    "RunOutcome",
    "RunVerdict",
    "Scenario",
    "ScenarioRuns",
    "ScenarioVerdict",
    "Suite",
    "append_record",
    "build_lock",
    "build_parser",
    "component_contract",
    "component_entry",
    "context_is_validator",
    "decide",
    "enforce_private_suite",
    "evaluate_scenario",
    "load_deployment",
    "load_suite",
    "main",
    "parse_deployment",
    "parse_scenario",
    "preflight",
    "principal_refusal",
    "private_root",
    "probe_contract_sha256",
    "run",
    "run_self_test",
    "run_suite",
    "sha256_hex",
    "trust_verifier",
    "validator_sha256",
    "verify_deployment_available",
    "verify_suite",
]
