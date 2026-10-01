#!/usr/bin/env python3
"""Umbrella ``components.lock`` producer / verifier (SPEC-0011, SPEC-0007 §4).

The **Core umbrella lock** is built from an **offline directory source** with
**local signing**: each component directory is canonicalized to a bundle, pinned
by its content address, and signed; the lock's ``lock_hash`` is signed too and
verified at boot. This tool is deterministic and fully offline.

It never writes a repo file. Subcommands print to stdout; the caller pipes them
through ``tools/safe-replace.sh`` (see ``tools/umbrella-lock.sh``).

Keys
----
``--key offline-test`` selects a deterministic, clearly-labelled **test**
signer/verifier so the slice runs without key material. The operator regenerates
with ``--key gpg`` or ``--key minisign`` (real crypto) and publishes only the
**public** trust root. **Never use the offline test key for production.**
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(_ROOT / "src"))

from agent_centric.cbp.component_bundle import (  # noqa: E402
    build_bundle_from_dir,
)
from agent_centric.cbp.lockfile import DirectoryPin, pin_design  # noqa: E402
from agent_centric.cbp.signing import SignatureError  # noqa: E402
from agent_centric.contracts.components_lock import ComponentsLock  # noqa: E402
from agent_centric.contracts.design import Design  # noqa: E402

_PREFIX = b"cbp-umbrella-offline-test:"


class OfflineTestSigner:
    """Deterministic **test** signer (never production)."""

    key_id = "cbp-umbrella-offline-test-1"

    def sign(self, payload: bytes) -> bytes:
        return hashlib.sha256(_PREFIX + payload).hexdigest().encode()


class OfflineTestVerifier:
    """Verifier matching :class:`OfflineTestSigner`."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        if signature != hashlib.sha256(_PREFIX + payload).hexdigest().encode():
            raise SignatureError("offline test signature does not verify")


def _signer(args: argparse.Namespace) -> Any:
    if args.key == "offline-test":
        return OfflineTestSigner()
    if args.key == "gpg":
        from agent_centric.cbp.signing_service import GpgSigner

        return GpgSigner(args.key_id)
    if args.key == "minisign":
        from agent_centric.cbp.signing_service import MinisignSigner

        return MinisignSigner(args.minisign_secret_key, key_id=args.key_id)
    raise SystemExit(f"unknown --key {args.key!r}")


def _verifier(args: argparse.Namespace) -> Any:
    if args.key == "offline-test":
        return OfflineTestVerifier()
    if args.key == "gpg":
        from agent_centric.cbp.signing import GpgVerifier

        return GpgVerifier()
    if args.key == "minisign":
        from agent_centric.cbp.signing import MinisignVerifier

        return MinisignVerifier(args.minisign_public_key)
    raise SystemExit(f"unknown --key {args.key!r}")


def _components_dir(args: argparse.Namespace) -> Path:
    return (Path(args.repo_root) / args.components_dir).resolve()


def _load_design(path: Path) -> Design:
    data = json.loads(path.read_text(encoding="utf-8"))
    return Design.from_dict(data)


def _load_lock(path: Path) -> ComponentsLock:
    return ComponentsLock.from_dict(json.loads(path.read_text(encoding="utf-8")))


def _bundle_for(name: str, components_dir: Path) -> bytes:
    return build_bundle_from_dir(components_dir / name)


def cmd_bundle(args: argparse.Namespace) -> int:
    bundle = _bundle_for(args.component, _components_dir(args))
    sys.stdout.buffer.write(bundle)
    return 0


def cmd_pin(args: argparse.Namespace) -> int:
    components_dir = _components_dir(args)
    design = _load_design(Path(args.design))
    signer = _signer(args)
    rel = args.components_dir.rstrip("/")
    sources: dict[str, DirectoryPin] = {}
    for component in design.components:
        directory = components_dir / component.name
        signature = signer.sign(_bundle_for(component.name, components_dir)).decode()
        sources[component.name] = DirectoryPin(
            path=directory,
            repo_url=f"dir://{rel}/{component.name}",
            signature=signature,
        )
    lock = pin_design(design, sources, root=args.root)
    sys.stdout.write(json.dumps(lock.to_dict(), indent=2, sort_keys=True) + "\n")
    return 0


def cmd_sign_lock(args: argparse.Namespace) -> int:
    lock = _load_lock(Path(args.lock))
    signature = _signer(args).sign(lock.lock_hash().encode("utf-8"))
    sys.stdout.write(signature.decode("utf-8") + "\n")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    lock = _load_lock(Path(args.lock))
    verifier = _verifier(args)
    bundles_dir = (Path(args.repo_root) / args.bundles_dir).resolve()
    for entry in lock.entries:
        data = (bundles_dir / f"{entry.name}.tar").read_bytes()
        if hashlib.sha256(data).hexdigest() != entry.tree_sha256:
            sys.stderr.write(f"verify: {entry.name}: bundle hash mismatch\n")
            return 1
        verifier.verify(data, entry.signature.encode("utf-8"))
    if args.sig:
        signature = Path(args.sig).read_text(encoding="utf-8").strip().encode("utf-8")
        verifier.verify(lock.lock_hash().encode("utf-8"), signature)
    sys.stdout.write(f"OK lock_hash={lock.lock_hash()}\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", default=str(_ROOT))
    parser.add_argument("--components-dir", default="examples/components")
    parser.add_argument("--bundles-dir", default="artifacts/components")
    parser.add_argument(
        "--key", choices=("offline-test", "gpg", "minisign"), default="offline-test"
    )
    parser.add_argument("--key-id", default="")
    parser.add_argument("--minisign-public-key", default="")
    parser.add_argument("--minisign-secret-key", default="")

    sub = parser.add_subparsers(dest="command", required=True)

    p_bundle = sub.add_parser("bundle", help="print one component's canonical bundle")
    p_bundle.add_argument("--component", required=True)
    p_bundle.set_defaults(func=cmd_bundle)

    p_pin = sub.add_parser("pin", help="print the umbrella components.lock")
    p_pin.add_argument("--design", required=True)
    p_pin.add_argument("--root", default="shell")
    p_pin.set_defaults(func=cmd_pin)

    p_sign = sub.add_parser("sign-lock", help="print the detached lock signature")
    p_sign.add_argument("--lock", required=True)
    p_sign.set_defaults(func=cmd_sign_lock)

    p_verify = sub.add_parser("verify", help="verify the lock, bundles, and signature")
    p_verify.add_argument("--lock", required=True)
    p_verify.add_argument("--sig", default="")
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
