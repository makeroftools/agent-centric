"""SPEC-0011 "viable" end-to-end, offline: boot a signed lock -> typed flow -> replay.

Pins a typed ``design.v1`` (shell -> even, plus a model boundary) from the
example component directories, signs it with the clearly-labelled **offline test
key**, boots it from a ``DirectorySource`` (verifying the lock signature and every
component signature), executes the typed-port network over the **booted** entries,
and asserts the model node is recorded and confidence-scored. Deterministic and
offline; no keys, no network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from agent_centric.cbp.boot_network import run_booted_network
from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle_from_dir
from agent_centric.cbp.driver import CbpDriver, replay_ledger
from agent_centric.cbp.lockfile import DirectoryPin, pin_design
from agent_centric.cbp.model_agent import TASK_MODEL
from agent_centric.cbp.network import network_from_dict
from agent_centric.cbp.resolver import DirectorySource
from agent_centric.cbp.signing_service import sign_lock
from agent_centric.contracts.design import Design, RequestedComponent

_REPO = Path(__file__).resolve().parents[1]
_COMPONENTS = _REPO / "examples" / "components"
_DIRS = {"shell": _COMPONENTS / "viable_shell", "even": _COMPONENTS / "even"}
_ENTRIES = frozenset(
    {
        "agent_centric.cbp.shell_component:plan_delegation",
        "agent_centric.cbp.viable_flow:is_even",
    }
)
_NETWORK = {
    "components": [
        {
            "id": "shell",
            "task": "plan",
            "args": {"payload": {"mission": "viable", "components": ["even"]}},
            "ports": {"out": ["count"]},
            "port_types": {"out": {"count": "int"}},
        },
        {
            "id": "even",
            "task": "even",
            "ports": {"in": ["value"]},
            "port_types": {"in": {"value": "int"}},
        },
        {
            "id": "model",
            "task": TASK_MODEL,
            "child": "model",
            "args": {"prompt": "score the viable mission"},
        },
    ],
    "edges": [
        {
            "source": "shell",
            "source_field": "count",
            "target": "even",
            "target_arg": "value",
            "capacity": 1,
        }
    ],
}


def _tool():
    spec = importlib.util.spec_from_file_location(
        "cbp_umbrella_lock", _REPO / "tools" / "umbrella-lock.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_TOOL = _tool()


def _signer():
    return _TOOL.OfflineTestSigner()


def _lock_and_bundles(tmp_path: Path):
    signer = _signer()
    design = Design(
        id="viable",
        network=_NETWORK,
        components=(
            RequestedComponent("shell", "viable"),
            RequestedComponent("even", "viable"),
        ),
    )
    sources = {
        name: DirectoryPin(
            path=directory,
            repo_url=f"dir://examples/components/{directory.name}",
            signature=signer.sign(build_bundle_from_dir(directory)).decode(),
        )
        for name, directory in _DIRS.items()
    }
    lock = pin_design(design, sources, root="shell")
    bundles = tmp_path / "bundles"
    bundles.mkdir()
    for name, directory in _DIRS.items():
        (bundles / f"{name}.tar").write_bytes(build_bundle_from_dir(directory))
    return lock, bundles, signer


def _booted_tree(lock, bundles, signer, tmp_path):
    signed = sign_lock(lock, signer)
    return boot_from_lock(
        lock,
        source=DirectorySource(bundles),
        cache=ContentAddressedCache(tmp_path / "cache"),
        verifier=_TOOL.OfflineTestVerifier(),
        entry_allowlist=_ENTRIES,
        state_root=tmp_path / "state",
        expected_lock_hash=lock.lock_hash(),
        lock_signature=signed.signature,
        lock_verifier=_TOOL.OfflineTestVerifier(),
    )


class TestViableEndToEnd:
    def test_boot_then_typed_flow_with_model(self, tmp_path: Path) -> None:
        lock, bundles, signer = _lock_and_bundles(tmp_path)
        tree = _booted_tree(lock, bundles, signer, tmp_path)
        assert tree.lock_verified is True
        driver = CbpDriver()
        try:
            driver.spawn("model", kind="model")
            result = run_booted_network(tree, network_from_dict(_NETWORK), driver=driver)
            assert result["ok"] is True, result
            by_id = {row["id"]: row for row in result["results"]}
            assert by_id["shell"]["value"] == {
                "mission": "viable",
                "order": ["even"],
                "count": 1,
            }
            assert by_id["even"]["value"] is False
            assert by_id["even"]["verified"] is True
            assert by_id["model"]["value"] is not None
            records = driver.model_records("model")
            assert len(records) == 1
            assert 0.0 < records[0].confidence < 1.0
        finally:
            driver.close()

    def test_durable_ledger_replays(self, tmp_path: Path) -> None:
        lock, bundles, signer = _lock_and_bundles(tmp_path)
        tree = _booted_tree(lock, bundles, signer, tmp_path)
        ledger = tmp_path / "ledger.sqlite"
        with CbpDriver(ledger_path=str(ledger)) as driver:
            driver.spawn("model", kind="model")
            result = run_booted_network(tree, network_from_dict(_NETWORK), driver=driver)
            assert result["ok"] is True
        replay = replay_ledger(str(ledger))
        assert replay["ok"] is True, replay
        assert replay["failed"] == []

    def test_flow_is_deterministic(self, tmp_path: Path) -> None:
        lock, bundles, signer = _lock_and_bundles(tmp_path)
        tree = _booted_tree(lock, bundles, signer, tmp_path)
        network = network_from_dict(_NETWORK)
        with CbpDriver() as first, CbpDriver() as second:
            first.spawn("model", kind="model")
            second.spawn("model", kind="model")
            a = run_booted_network(tree, network, driver=first)
            b = run_booted_network(tree, network, driver=second)
        assert a == b

    def test_unmapped_component_is_refused(self, tmp_path: Path) -> None:
        lock, bundles, signer = _lock_and_bundles(tmp_path)
        tree = _booted_tree(lock, bundles, signer, tmp_path)
        bad = dict(_NETWORK)
        bad["components"] = [
            {"id": "ghost", "task": "even", "ports": {"in": ["value"]}}
        ]
        bad["edges"] = []
        with CbpDriver() as driver:
            result = run_booted_network(tree, network_from_dict(bad), driver=driver)
        assert result["ok"] is False
        assert "not in the booted lock" in result["error"]

    def test_mis_typed_edge_fails_closed(self, tmp_path: Path) -> None:
        lock, bundles, signer = _lock_and_bundles(tmp_path)
        tree = _booted_tree(lock, bundles, signer, tmp_path)
        bad = {
            "components": [
                {
                    "id": "shell",
                    "task": "plan",
                    "args": {"payload": {"mission": "viable", "components": []}},
                    "ports": {"out": ["count"]},
                    "port_types": {"out": {"count": "int"}},
                },
                {
                    "id": "even",
                    "task": "even",
                    "ports": {"in": ["value"]},
                    "port_types": {"in": {"value": "str"}},
                },
            ],
            "edges": [
                {
                    "source": "shell",
                    "source_field": "count",
                    "target": "even",
                    "target_arg": "value",
                    "capacity": 1,
                }
            ],
        }
        with CbpDriver() as driver:
            result = run_booted_network(tree, network_from_dict(bad), driver=driver)
        assert result["ok"] is False


class TestDemo:
    def test_demo_runs(self, capsys: pytest.CaptureFixture[str]) -> None:
        import examples.viable_demo as demo

        demo.main()
        out = capsys.readouterr().out
        assert "typed_flow_ok=True" in out
        assert "replay_ok=True" in out
