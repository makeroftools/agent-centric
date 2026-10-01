"""End-to-end SPEC-0011 "viable" demo (offline, deterministic).

Boots a signed ``components.lock`` and executes its typed-port ``network.v1`` over
the **verified** component entries, with a spawned ``model`` boundary that is
recorded and confidence-scored, then replays the durable directive ledger. No
network, no server, no real keys: it uses clearly-labelled ``_Demo``
signer/verifiers so the slice runs without key material. Production uses the
operator key (see ``tools/umbrella-lock.sh`` and ``designs/README.md``).
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from agent_centric.cbp.boot_network import run_booted_network
from agent_centric.cbp.cache import ContentAddressedCache
from agent_centric.cbp.component_boot import boot_from_lock
from agent_centric.cbp.component_bundle import build_bundle_from_dir
from agent_centric.cbp.driver import CbpDriver, replay_ledger
from agent_centric.cbp.lockfile import DirectoryPin, pin_design
from agent_centric.cbp.model_agent import TASK_MODEL
from agent_centric.cbp.network import network_from_dict
from agent_centric.cbp.resolver import DirectorySource
from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import sign_lock
from agent_centric.contracts.design import Design, RequestedComponent

_HERE = Path(__file__).resolve().parent
_COMPONENTS = _HERE / "components"
_DIRS = {"shell": _COMPONENTS / "viable_shell", "even": _COMPONENTS / "even"}
_ENTRIES = frozenset(
    {
        "agent_centric.cbp.shell_component:plan_delegation",
        "agent_centric.cbp.viable_flow:is_even",
    }
)
_PREFIX = b"viable-demo:"
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


class _DemoSigner:
    """DEMO ONLY: deterministic signer (never use in production)."""

    key_id = "viable-demo-key"

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(_PREFIX + payload).hexdigest().encode()


class _DemoVerifier:
    """DEMO ONLY: verifier matching :class:`_DemoSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != hashlib.sha256(_PREFIX + payload).hexdigest().encode():
            raise SignatureError("demo verifier rejected the signature")


def main() -> None:
    signer = _DemoSigner()
    with tempfile.TemporaryDirectory(prefix="viable-") as tmp:
        root = Path(tmp)
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
        bundles = root / "bundles"
        bundles.mkdir()
        for name, directory in _DIRS.items():
            (bundles / f"{name}.tar").write_bytes(build_bundle_from_dir(directory))

        signed = sign_lock(lock, signer)
        tree = boot_from_lock(
            lock,
            source=DirectorySource(bundles),
            cache=ContentAddressedCache(root / "cache"),
            verifier=_DemoVerifier(),
            entry_allowlist=_ENTRIES,
            state_root=root / "state",
            expected_lock_hash=lock.lock_hash(),
            lock_signature=signed.signature,
            lock_verifier=_DemoVerifier(),
        )
        ledger = root / "ledger.sqlite"
        with CbpDriver(ledger_path=str(ledger)) as driver:
            driver.spawn("model", kind="model")
            result = run_booted_network(tree, network_from_dict(_NETWORK), driver=driver)
            records = driver.model_records("model")
        replay = replay_ledger(str(ledger))
        print(f"lock_hash={lock.lock_hash()[:12]} root={lock.root} verified={tree.lock_verified}")
        print(f"typed_flow_ok={result['ok']} results={[r['id'] for r in result['results']]}")
        print(
            f"model_records={len(records)} confidence={records[0].confidence if records else None}"
        )
        print(f"replay_ok={replay['ok']} passed={replay['passed']}/{replay['runs']}")


if __name__ == "__main__":
    main()
