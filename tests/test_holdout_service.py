"""Hermetic acceptance for the L3-ready holdout validator (SPEC-0019).

Deterministic and offline. These tests drive the **library** and its thin CLI
against the committed, test-signed umbrella composition
(``components.lock`` + ``artifacts/components``), never the real operator-private
root. They prove:

* the ``service`` probe boots a signed composition through the core harness and
  compares a canonical-JSON projection (the shell delegation plan);
* the record is deterministic, wall-clock-free, TCB-pinned, and the ledger is
  idempotent;
* a tampered bundle / lock signature is refused fail-closed (negative probe);
* the principal guard (uid 0 / docker group) refuses and ``--rehearsal`` records
  ``isolation: rehearsal``; the offline test trust is rehearsal-only;
* the ``--self-test`` known-good/known-bad pair separates cleanly; and
* the component-ready boundary exposes only pure operations and is **not**
  placed inside the composition under test.
"""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from agent_centric.cbp import holdout as _M
from agent_centric.contracts.components_lock import ComponentsLock

_REPO = Path(__file__).resolve().parents[1]
_BUNDLES = _REPO / "artifacts" / "components"
_LOCK = _REPO / "components.lock"
_SIG = _REPO / "components.lock.sig"
_PREFIX = "cbp-umbrella-offline-test:"
_ENTRIES = [
    "agent_centric.cbp.shell_component:plan_delegation",
    "agent_centric.cbp.registry_component:catalog_snapshot",
]
_EXPECT = {"mission": "umbrella", "order": ["registry"], "count": 1}

_VALIDATOR_ENV = {_M.CONTEXT_ENV: _M.CONTEXT_VALUE}


@pytest.fixture(autouse=True)
def _isolate_private_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CBP_HOLDOUT_ROOT", str(tmp_path / "priv"))


def _unprivileged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(_M, "_euid", lambda: 1000)
    monkeypatch.setattr(_M, "_docker_gid", lambda: None)


def _lock_hash() -> str:
    return ComponentsLock.from_dict(json.loads(_LOCK.read_text(encoding="utf-8"))).lock_hash()


def _descriptor(
    root: Path,
    *,
    composition_dir: Path | None = None,
    lock: Path | None = None,
    lock_signature: Path | None = None,
    observable: dict[str, object] | None = None,
) -> Path:
    document = {
        "schema": "holdout.deployment/v1",
        "repo_root": str(_REPO),
        "composition_dir": str(composition_dir or _BUNDLES),
        "lock": str(lock or _LOCK),
        "lock_signature": str(lock_signature or _SIG),
        "lock_hash": _lock_hash(),
        "trust": {"kind": "inline", "algorithm": "offline-test", "prefix": _PREFIX},
        "entry_allowlist": _ENTRIES,
        "observable": observable
        or {"kind": "root", "args": {"mission": "umbrella", "components": ["registry"]}},
    }
    path = root / "deployment.json"
    path.write_text(json.dumps(document, sort_keys=True), encoding="utf-8")
    return path


def _write_service_scenario(
    suite: Path,
    sid: str,
    deployment: Path,
    *,
    expect: object = _EXPECT,
    expect_refused: bool = False,
    priority: str = "high",
) -> None:
    probe: dict[str, object] = {
        "kind": "service",
        "deployment": deployment.name,
        "deployment_sha256": _M.sha256_hex(deployment.read_bytes()),
    }
    if expect_refused:
        probe["expect_refused"] = True
    else:
        probe["expect"] = expect
    suite.joinpath(f"{sid}.md").write_text(
        "---\n"
        f"id: {sid}\n"
        "service: cbp\n"
        "feature: umbrella\n"
        f"priority: {priority}\n"
        "---\n\n"
        "The signed composition must boot and its observable must match.\n\n"
        "```check\n"
        f"{json.dumps(probe, sort_keys=True)}\n"
        "```\n",
        encoding="utf-8",
    )


def _write_task_scenario(suite: Path, sid: str, priority: str = "normal") -> None:
    suite.joinpath(f"{sid}.md").write_text(
        "---\n"
        f"id: {sid}\n"
        "service: cbp\n"
        "feature: health\n"
        f"priority: {priority}\n"
        "---\n\n"
        "The named task must exit as expected.\n\n"
        "```check\n"
        '{"kind": "task", "target": "check.sh", "expect_exit": 0}\n'
        "```\n",
        encoding="utf-8",
    )


def _private_suite(root: Path) -> tuple[Path, Path]:
    root.mkdir(parents=True, exist_ok=True)
    suite = root / "suite"
    suite.mkdir()
    os.chmod(suite, 0o700)
    deployments = root / "deployments"
    deployments.mkdir()
    return suite, deployments


def _run(
    root: Path,
    suite: Path,
    deployments: Path,
    *,
    ledger: Path | None = None,
    extra: tuple[str, ...] = (),
    env: dict[str, str] | None = None,
) -> tuple[int, Path]:
    tasks = root / "tasks"
    tasks.mkdir(exist_ok=True)
    lock = root / "holdout.lock"
    if not lock.exists():
        lock.write_text(
            json.dumps(_M.build_lock(_M.load_suite(suite)), sort_keys=True),
            encoding="utf-8",
        )
    resolved_ledger = ledger or (root / "runs.jsonl")
    args = [
        "--suite",
        str(suite),
        "--lock",
        str(lock),
        "--tasks-dir",
        str(tasks),
        "--deployments-dir",
        str(deployments),
        "--ledger",
        str(resolved_ledger),
        "--rehearsal",
        *extra,
    ]
    return _M.main(args, env=env or _VALIDATOR_ENV), resolved_ledger


class TestServiceProbe:
    def test_signed_composition_boots_and_projects(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        _write_service_scenario(suite, "HO-0001", _descriptor(deployments))
        code, ledger = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["verdict"] == "green"
        assert out["scenarios"] == [{"id": "HO-0001", "verdict": "pass"}]
        assert out["schema"] == _M.HOLDOUT_RECORD_SCHEMA
        assert out["isolation"] == "rehearsal"
        assert out["validator_sha256"] == _M.validator_sha256()
        assert out["probe_contract_sha256"] == _M.probe_contract_sha256()
        assert out["lock_sha256"] == _M.sha256_hex(
            (tmp_path / "holdout.lock").read_bytes()
        )
        assert len(out["suite_sha256"]) == 64
        assert "runtime" in out and "platform" in out
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_record_is_deterministic_and_ledger_idempotent(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        _write_service_scenario(suite, "HO-0001", _descriptor(deployments))
        ledger = tmp_path / "runs.jsonl"
        first_code, _ = _run(tmp_path, suite, deployments, ledger=ledger)
        first_out = capsys.readouterr().out
        second_code, _ = _run(tmp_path, suite, deployments, ledger=ledger)
        second_out = capsys.readouterr().out
        assert first_code == second_code == _M.EXIT_OK
        assert first_out == second_out
        assert len(ledger.read_text(encoding="utf-8").splitlines()) == 1

    def test_wrong_projection_is_red(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        _write_service_scenario(
            suite, "HO-0001", _descriptor(deployments), expect={"mission": "wrong"}
        )
        code, _ = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_RED
        assert json.loads(capsys.readouterr().out)["verdict"] == "red"

    def test_tampered_bundle_is_refused_and_the_negative_passes(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bundles = tmp_path / "bundles"
        shutil.copytree(_BUNDLES, bundles)
        target = bundles / "registry.tar"
        data = bytearray(target.read_bytes())
        data[-1] ^= 0xFF
        target.write_bytes(bytes(data))

        refused_suite, refused_deployments = _private_suite(tmp_path / "neg")
        _write_service_scenario(
            refused_suite,
            "HO-NEG",
            _descriptor(refused_deployments, composition_dir=bundles),
            expect_refused=True,
        )
        code, _ = _run(tmp_path / "neg", refused_suite, refused_deployments)
        assert code == _M.EXIT_OK  # a correct refusal is a pass for the negative

        positive_suite, positive_deployments = _private_suite(tmp_path / "pos")
        _write_service_scenario(
            positive_suite,
            "HO-POS",
            _descriptor(positive_deployments, composition_dir=bundles),
        )
        code2, _ = _run(tmp_path / "pos", positive_suite, positive_deployments)
        assert code2 == _M.EXIT_RED  # the same tamper is a failure for the positive

    def test_tampered_lock_signature_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        bad_lock = tmp_path / "components.lock"
        bad_sig = tmp_path / "components.lock.sig"
        shutil.copy(_LOCK, bad_lock)
        bad_sig.write_text("not-a-signature\n", encoding="utf-8")
        suite, deployments = _private_suite(tmp_path)
        _write_service_scenario(
            suite,
            "HO-0001",
            _descriptor(deployments, lock=bad_lock, lock_signature=bad_sig),
            expect_refused=True,
        )
        code, _ = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_OK

    def test_descriptor_drift_refuses_before_running(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        descriptor = _descriptor(deployments)
        _write_service_scenario(suite, "HO-0001", descriptor)
        descriptor.write_text(
            descriptor.read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        code, _ = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_REFUSED
        assert "descriptor drift" in json.loads(capsys.readouterr().out)["reason"]

    def test_missing_descriptor_refuses_before_running(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        descriptor = _descriptor(deployments)
        _write_service_scenario(suite, "HO-0001", descriptor)
        descriptor.unlink()
        code, _ = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_REFUSED
        assert "no deployment descriptor" in json.loads(capsys.readouterr().out)["reason"]


class TestHardening:
    def test_descriptor_path_traversal_is_refused(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        suite, deployments = _private_suite(tmp_path)
        # The descriptor lives at tmp_path/deployment.json, outside the
        # deployments root (tmp_path/deployments), so a ../ reference escapes.
        descriptor = _descriptor(tmp_path)
        probe = {
            "kind": "service",
            "deployment": "../" + descriptor.name,
            "deployment_sha256": _M.sha256_hex(descriptor.read_bytes()),
            "expect_refused": True,
        }
        (suite / "HO-0001.md").write_text(
            "---\nid: HO-0001\nservice: cbp\nfeature: x\npriority: high\n---\n\n"
            "```check\n" + json.dumps(probe, sort_keys=True) + "\n```\n",
            encoding="utf-8",
        )
        code, _ = _run(tmp_path, suite, deployments)
        assert code == _M.EXIT_REFUSED
        assert "escapes the deployments root" in json.loads(
            capsys.readouterr().out
        )["reason"]

    def test_default_deployments_root_is_under_the_private_root(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("CBP_HOLDOUT_ROOT", str(tmp_path / "root"))
        args = _M.build_parser().parse_args([])
        assert args.deployments_dir == str(tmp_path / "root" / "deployments")


class TestPrincipalGuard:
    def test_root_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_M, "_euid", lambda: 0)
        assert "root" in str(_M.principal_refusal())

    def test_docker_group_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(_M, "_euid", lambda: 1000)
        monkeypatch.setattr(_M, "_docker_gid", lambda: 987)
        monkeypatch.setattr(_M, "_process_groups", lambda: (1000, 987))
        assert "docker" in str(_M.principal_refusal())

    def test_unprivileged_principal_is_clean(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _unprivileged(monkeypatch)
        assert _M.principal_refusal() is None

    def test_cli_refuses_a_privileged_principal_without_rehearsal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(_M, "_euid", lambda: 0)
        suite, _ = _private_suite(tmp_path)
        _write_task_scenario(suite, "HO-0001")
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "check.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(script, 0o755)
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks)],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_REFUSED
        assert "root" in json.loads(capsys.readouterr().out)["reason"]

    def test_enforced_isolation_marks_the_record(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _unprivileged(monkeypatch)
        suite, _ = _private_suite(tmp_path)
        _write_task_scenario(suite, "HO-0001")
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        script = tasks / "check.sh"
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        os.chmod(script, 0o755)
        ledger = tmp_path / "runs.jsonl"
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks),
             "--ledger", str(ledger)],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_OK
        out = json.loads(capsys.readouterr().out)
        assert out["isolation"] == "enforced"

    def test_offline_test_trust_is_rehearsal_only(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        _unprivileged(monkeypatch)
        suite, deployments = _private_suite(tmp_path)
        _write_service_scenario(suite, "HO-0001", _descriptor(deployments))
        lock = tmp_path / "holdout.lock"
        lock.write_text(json.dumps(_M.build_lock(_M.load_suite(suite))), encoding="utf-8")
        tasks = tmp_path / "tasks"
        tasks.mkdir()
        code = _M.main(
            ["--suite", str(suite), "--lock", str(lock), "--tasks-dir", str(tasks),
             "--deployments-dir", str(deployments)],
            env=_VALIDATOR_ENV,
        )
        assert code == _M.EXIT_REFUSED
        assert "rehearsal-only" in json.loads(capsys.readouterr().out)["reason"]


class TestSelfTest:
    def test_self_test_is_green(self) -> None:
        document, code = _M.run_self_test()
        assert code == _M.EXIT_OK and document["ok"] is True
        assert {check["name"] for check in document["checks"]} >= {
            "known-good-is-green",
            "known-bad-is-red",
        }

    def test_self_test_cli_exit_zero(self, capsys: pytest.CaptureFixture[str]) -> None:
        code = _M.main(["--self-test"], env=_VALIDATOR_ENV)
        assert code == _M.EXIT_OK
        assert json.loads(capsys.readouterr().out)["ok"] is True


class TestComponentReady:
    def test_component_contract_is_component_v1_ready(self) -> None:
        contract = _M.component_contract()
        assert contract["version"] == "component.v1"
        assert contract["entry"] == {
            "runtime": "python",
            "entrypoint": _M.HOLDOUT_ENTRYPOINT,
        }
        assert _M.HOLDOUT_ENTRYPOINT == "agent_centric.cbp.holdout:component_entry"

    def test_component_entry_dispatches_pure_operations(self, tmp_path: Path) -> None:
        suite = tmp_path / "suite"
        suite.mkdir()
        os.chmod(suite, 0o700)
        _write_task_scenario(suite, "HO-0001")
        built = _M.component_entry({"command": "build-lock", "suite": str(suite)})
        assert built["ok"] is True
        lock = built["lock"]
        assert isinstance(lock, dict) and lock["schema"] == _M.HOLDOUT_LOCK_SCHEMA
        decided = _M.component_entry(
            {
                "command": "decide",
                "scenarios": [
                    {"id": "HO-1", "priority": "high", "runs": ["pass", "pass", "pass"]}
                ],
            }
        )
        assert decided["ok"] is True

    def test_component_entry_refuses_a_malformed_payload(self) -> None:
        with pytest.raises(_M.Refusal):
            _M.component_entry({"command": "build-lock"})
        with pytest.raises(_M.Refusal):
            _M.component_entry({"command": "decide", "threshold": "not-a-number"})
        with pytest.raises(_M.Refusal):
            _M.component_entry({"command": "unknown"})

    def test_validator_is_not_placed_inside_the_composition(self) -> None:
        design = _REPO / "designs" / "core.v1.json"
        text = design.read_text(encoding="utf-8")
        assert _M.HOLDOUT_ENTRYPOINT not in text
        assert "holdout" not in text

    def test_tool_is_a_thin_executable_cli(self) -> None:
        tool = _REPO / "tools" / "holdout-validate.py"
        assert tool.is_file() and os.access(tool, os.X_OK)
        text = tool.read_text(encoding="utf-8")
        assert "CBP_HOLDOUT_CONTEXT" in text
        assert "holdout import main" in text
