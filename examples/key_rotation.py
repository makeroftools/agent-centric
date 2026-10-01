"""Demo: overlapping signing-key rotation (SPEC-0007 §6).

Offline, deterministic. It shows a release-signing key rotation:

1. before rotation, a single key (``release-key-1``) signs and verifies releases;
2. at rotation, ``release-key-2`` becomes the only signing key while
   ``release-key-1`` stays trusted to **verify** over the overlap window, so a
   lock signed just before the change is still valid;
3. every release records the ``key_id`` that signed it in the transparency log,
   and the whole log verifies against the rotated key ring.

No wall-clock is read: rotation happens at an explicit, recorded instant.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from agent_centric.cbp.component_bundle import build_bundle, bundle_sha256
from agent_centric.cbp.signing_service import record_release
from agent_centric.cbp.transparency import TransparencyLog
from agent_centric.cbp.trust import TrustStore, TrustWindow
from agent_centric.contracts.component import (
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
)
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry

_ENTRY = "agent_centric.cbp.registry_component:catalog_snapshot"


class _DemoSigner:
    """DEMO ONLY: deterministic signer, signature = sha256(key_id ':' payload)."""

    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(self._key_id.encode() + b":" + payload).hexdigest().encode()


class _DemoVerifier:
    """DEMO ONLY: verifier matching :class:`_DemoSigner`."""

    def __init__(self, key_id: str) -> None:
        self._key_id = key_id

    def verify(self, payload: bytes, signature: bytes) -> None:
        expected = hashlib.sha256(self._key_id.encode() + b":" + payload).hexdigest().encode()
        if signature != expected:
            raise RuntimeError("demo verifier rejected the signature")


def _pair(key_id: str) -> tuple[_DemoSigner, _DemoVerifier]:
    return _DemoSigner(key_id), _DemoVerifier(key_id)


def _demo_lock() -> ComponentsLock:
    manifest = ComponentManifest(
        version=ComponentVersion.V1,
        name="shell",
        kind=ComponentKind.ATOMIC,
        entry=EntryDescriptor(runtime="python", entrypoint=_ENTRY),
        implements=("component.v1",),
    )
    bundle = build_bundle(manifest)
    entry = LockEntry(
        name="shell",
        repo_url="ssh://git@localhost/shell.git",
        commit_sha="a" * 40,
        tree_sha256=bundle_sha256(bundle),
        implements=("component.v1",),
        signature="good",
    )
    return ComponentsLock(root="shell", entries=(entry,), harness_contracts=("component.v1",))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="key-rotation-") as tmp:
        k1_signer, k1_verifier = _pair("release-key-1")
        k2_signer, k2_verifier = _pair("release-key-2")

        before = TrustStore(
            [TrustWindow("release-key-1", k1_verifier, 0, 100, signer=k1_signer)]
        )
        after = TrustStore(
            [
                TrustWindow("release-key-1", k1_verifier, 0, 100),  # verify-only
                TrustWindow("release-key-2", k2_verifier, 50, 200, signer=k2_signer),
            ]
        )

        lock = _demo_lock()
        log = TransparencyLog(Path(tmp) / "log.jsonl")

        first, _ = record_release(lock, before.signer_at(10), log)
        print(f"before signer={first.key_id} active={before.active_key_ids(10)}")

        second, _ = record_release(lock, after.signer_at(120), log)
        print(f"after signer={second.key_id} active={after.active_key_ids(60)}")

        entries = log.verify(after.log_verifiers())
        print(f"log keys={[entry.key_id for entry in entries]} verified=True")


if __name__ == "__main__":
    main()
