"""Tests for design envelope limits (SPEC-0008 §4).

A design may declare a per-component hard resource envelope. Validation is
deterministic and fail-closed: the grant must reference a real component, be
unique, declare at least one bound, and construct a valid ``ResourceEnvelope``.
The validated grant is pinned into the signed lock, and the n8n round-trip is
lossless. All of this is additive: a design (or lock entry) without envelopes
keeps its exact prior canonical bytes.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from agent_centric.cbp.design import (
    DesignError,
    compile_design,
    validate_design,
)
from agent_centric.cbp.lockfile import ComponentPin, PinError, pin_design
from agent_centric.cbp.n8n_adapter import from_n8n, to_n8n
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry
from agent_centric.contracts.design import Design, EnvelopeGrant, RequestedComponent

_COMPONENTS = Path(__file__).resolve().parents[1] / "examples" / "components"
_NETWORK = {
    "components": [
        {"id": "shell", "task": "plan", "args": {}},
        {"id": "registry", "task": "catalog", "args": {}},
    ],
    "edges": [],
}


def _design(*envelopes: EnvelopeGrant) -> Design:
    return Design(
        id="d1",
        network=_NETWORK,
        components=(
            RequestedComponent("shell", "HEAD"),
            RequestedComponent("registry", "HEAD"),
        ),
        envelopes=envelopes,
    )


class TestDesignEnvelopeValidation:
    def test_valid_envelope_is_accepted(self) -> None:
        design = _design(EnvelopeGrant("shell", {"step_limit": 10, "size_cap": 4096}))
        report = validate_design(design)
        assert report.ok, report.errors
        assert compile_design(design).design_hash == design.design_hash()

    def test_envelope_for_unknown_component_is_refused(self) -> None:
        report = validate_design(_design(EnvelopeGrant("ghost", {"step_limit": 1})))
        assert not report.ok
        assert any("unknown component" in e for e in report.errors)

    def test_unsupported_bound_key_is_refused(self) -> None:
        report = validate_design(_design(EnvelopeGrant("shell", {"cpu_cores": 1})))
        assert not report.ok
        assert any("unsupported bound key" in e for e in report.errors)

    def test_negative_bound_is_refused(self) -> None:
        report = validate_design(_design(EnvelopeGrant("shell", {"step_limit": -1})))
        assert not report.ok
        assert any("non-negative" in e for e in report.errors)

    def test_no_bounds_is_refused(self) -> None:
        report = validate_design(_design(EnvelopeGrant("shell", {})))
        assert not report.ok
        assert any("no resource bounds" in e for e in report.errors)

    def test_non_mapping_extra_is_refused(self) -> None:
        report = validate_design(_design(EnvelopeGrant("shell", {"step_limit": 1, "extra": 3})))
        assert not report.ok
        assert any("extra must be a mapping" in e for e in report.errors)

    def test_duplicate_envelope_component_is_refused_by_contract(self) -> None:
        with pytest.raises(ValueError):
            _design(
                EnvelopeGrant("shell", {"step_limit": 1}),
                EnvelopeGrant("shell", {"size_cap": 1}),
            )

    def test_compile_refuses_invalid_envelope(self) -> None:
        with pytest.raises(DesignError):
            compile_design(_design(EnvelopeGrant("ghost", {"step_limit": 1})))


class TestDesignHashAdditive:
    def test_design_without_envelopes_is_unchanged(self) -> None:
        design = _design()
        assert "envelopes" not in design.to_dict()
        assert Design.from_dict(design.to_dict()) == design

    def test_envelope_changes_hash_and_round_trips(self) -> None:
        plain = _design()
        granted = _design(EnvelopeGrant("shell", {"step_limit": 5}))
        assert plain.design_hash() != granted.design_hash()
        assert "envelopes" in granted.to_dict()
        assert Design.from_dict(granted.to_dict()) == granted


class TestN8nEnvelopeRoundTrip:
    def test_round_trip_preserves_envelopes(self) -> None:
        design = _design(EnvelopeGrant("shell", {"step_limit": 5, "child_limit": 2}))
        back = from_n8n(to_n8n(design))
        assert back.to_dict() == design.to_dict()
        assert back.design_hash() == design.design_hash()
        assert to_n8n(back) == to_n8n(design)

    def test_workflow_without_envelopes_omits_the_key(self) -> None:
        workflow = to_n8n(_design())
        assert "envelopes" not in workflow["meta"]["cbp"]


class TestLockEnvelope:
    def test_entry_without_envelope_is_unchanged(self) -> None:
        entry = LockEntry(
            name="a",
            repo_url="ssh://x/a.git",
            commit_sha="a" * 40,
            tree_sha256="b" * 64,
            implements=("component.v1",),
        )
        assert "envelope" not in entry.to_dict()
        lock = ComponentsLock(root="a", entries=(entry,), harness_contracts=("component.v1",))
        again = ComponentsLock.from_dict(lock.to_dict())
        assert again.lock_hash() == lock.lock_hash()

    def test_entry_envelope_round_trips_and_changes_hash(self) -> None:
        base = LockEntry(
            name="a",
            repo_url="ssh://x/a.git",
            commit_sha="a" * 40,
            tree_sha256="b" * 64,
            implements=("component.v1",),
        )
        granted = LockEntry(
            name="a",
            repo_url="ssh://x/a.git",
            commit_sha="a" * 40,
            tree_sha256="b" * 64,
            implements=("component.v1",),
            envelope={"step_limit": 3},
        )
        assert granted.to_dict()["envelope"] == {"step_limit": 3}
        plain = ComponentsLock(root="a", entries=(base,), harness_contracts=("component.v1",))
        rich = ComponentsLock(root="a", entries=(granted,), harness_contracts=("component.v1",))
        assert plain.lock_hash() != rich.lock_hash()
        again = ComponentsLock.from_dict(rich.to_dict())
        assert again.entry("a").envelope == {"step_limit": 3}
        assert again.lock_hash() == rich.lock_hash()


pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


def _git(repo: Path, *args: str) -> None:
    flags = ["-c", "user.email=t@example.com", "-c", "user.name=t"]
    subprocess.run(["git", "-C", str(repo), *flags, *args], check=True)


def _repo_from(dir_: Path, dest: Path) -> Path:
    shutil.copytree(dir_, dest)
    subprocess.run(["git", "init", "-q", str(dest)], check=True)
    _git(dest, "add", "-A")
    _git(dest, "commit", "-qm", "component")
    return dest


def _example_pins(tmp_path: Path) -> dict[str, ComponentPin]:
    return {
        "shell": ComponentPin(
            repo=_repo_from(_COMPONENTS / "shell", tmp_path / "shell-repo"),
            repo_url="ssh://git@localhost/shell.git",
            signature="good",
        ),
        "registry": ComponentPin(
            repo=_repo_from(_COMPONENTS / "registry", tmp_path / "registry-repo"),
            repo_url="ssh://git@localhost/registry.git",
            signature="good",
        ),
    }


class TestPinEnvelope:
    def test_pin_records_the_validated_envelope(self, tmp_path: Path) -> None:
        bounds = {"step_limit": 7, "size_cap": 2048}
        design = _design(EnvelopeGrant("shell", bounds))
        lock = pin_design(design, _example_pins(tmp_path), root="shell")
        assert lock.entry("shell").envelope == bounds
        assert lock.entry("registry").envelope == {}
        # The envelope is part of the signed canonical bytes.
        assert json.dumps(lock.to_dict(), sort_keys=True).find("step_limit") != -1

    def test_pin_refuses_invalid_envelope(self, tmp_path: Path) -> None:
        design = _design(EnvelopeGrant("ghost", {"step_limit": 1}))
        with pytest.raises(PinError):
            pin_design(design, _example_pins(tmp_path), root="shell")
