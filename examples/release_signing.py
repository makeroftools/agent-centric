"""End-to-end demo of release signing + transparency (SPEC-0007 Phase 0.5b).

Offline, no network, no external key server. It shows the trust layer the
committed umbrella lock needs:

1. a lock's content address (``lock_hash``) is signed by a signer;
2. the signed release is appended to an append-only, hash-chained transparency
   log;
3. the whole log verifies: chain links intact, signatures valid, head as pinned.

Signing note: this demo uses a clearly-labelled ``_DemoSigner`` so the slice runs
without an operator key. Production signing uses ``gpg``/``minisign`` via
:class:`agent_centric.cbp.signing_service.GpgSigner` /
``MinisignSigner`` — never the demo signer.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

from agent_centric.cbp.signing import SignatureError
from agent_centric.cbp.signing_service import record_release
from agent_centric.cbp.transparency import TransparencyLog
from agent_centric.contracts.components_lock import ComponentsLock, LockEntry


class _DemoSigner:
    """DEMO ONLY: signature = sha256(b'demo:' + payload). Never use in production."""

    @property
    def key_id(self) -> str:
        return "demo-key"

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(b"demo:" + payload).hexdigest().encode("utf-8")


class _DemoVerifier:
    """DEMO ONLY: verifier matching :class:`_DemoSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != hashlib.sha256(b"demo:" + payload).hexdigest().encode("utf-8"):
            raise SignatureError("demo verifier rejected the signature")


def _demo_lock() -> ComponentsLock:
    entry = LockEntry(
        name="shell",
        repo_url="ssh://git@localhost/shell.git",
        commit_sha="a" * 40,
        tree_sha256="b" * 64,
        implements=("component.v1",),
        signature="demo",
    )
    return ComponentsLock(root="shell", entries=(entry,), harness_contracts=("component.v1",))


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="release-signing-") as tmp:
        lock = _demo_lock()
        log = TransparencyLog(Path(tmp) / "transparency.jsonl")
        signed, entry = record_release(lock, _DemoSigner(), log)
        print(f"lock_hash={signed.lock_hash[:12]} key={signed.key_id} seq={entry.seq}")

        head = log.head()
        entries = log.verify(_DemoVerifier(), expected_head=head)
        print(f"transparency records={len(entries)} head={head[:12]} verified=True")


if __name__ == "__main__":
    main()
