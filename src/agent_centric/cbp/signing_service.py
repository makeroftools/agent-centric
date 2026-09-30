"""Automated release signing (SPEC-0007 §6, Phase 0.5b).

The root of trust is an **operator-held signing key** operated by a
machine-performed service scoped to signing releases. This module signs a lock's
content address (``lock_hash``) with a system tool (``gpg`` or ``minisign``) and
records the signature in the append-only transparency log. No Python crypto
dependency is added and **no private key material is read by this process** — the
key stays with the system tool.

A :class:`Signer` is injected by the operator; when none is available, signing
refuses (fail-closed). The protocol is minimal so an HSM/agent-backed signer can
be substituted without changing callers.
"""

from __future__ import annotations

import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..contracts.components_lock import ComponentsLock
from .signing import tool_available
from .transparency import TransparencyEntry, TransparencyLog


class SigningError(Exception):
    """A release could not be signed (fail-closed)."""


class Signer(Protocol):
    """A machine-performed signer over a payload."""

    @property
    def key_id(self) -> str:
        """The id of the signing key (recorded in the transparency log)."""
        ...

    def sign(self, payload: bytes) -> bytes:
        """Return a detached signature over ``payload`` (raise on failure)."""
        ...


def _run(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, capture_output=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise SigningError(f"signing tool not found: {argv[0]}") from exc


class GpgSigner:
    """Sign a payload with a local GPG key (detached, armored)."""

    def __init__(self, key_id: str, *, executable: str = "gpg") -> None:
        if not key_id:
            raise SigningError("gpg key id is required")
        self._key_id = key_id
        self._executable = executable

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="gpg-sign-") as tmp:
            payload_path = Path(tmp) / "payload"
            signature_path = Path(tmp) / "payload.asc"
            payload_path.write_bytes(payload)
            result = _run(
                [
                    self._executable,
                    "--batch",
                    "--yes",
                    "--armor",
                    "--detach-sign",
                    "--local-user",
                    self._key_id,
                    "--output",
                    str(signature_path),
                    str(payload_path),
                ]
            )
            if result.returncode != 0:
                raise SigningError(
                    "gpg failed to sign: "
                    f"{result.stderr.decode(errors='replace').strip()}"
                )
            return signature_path.read_bytes()


class MinisignSigner:
    """Sign a payload with a local minisign secret key."""

    def __init__(
        self,
        secret_key: str | Path,
        *,
        key_id: str = "",
        executable: str = "minisign",
    ) -> None:
        path = Path(secret_key)
        if not path.is_file():
            raise SigningError(f"minisign secret key not found: {path}")
        self._secret_key = path
        self._key_id = key_id or f"minisign:{path.name}"
        self._executable = executable

    @property
    def key_id(self) -> str:
        return self._key_id

    def sign(self, payload: bytes) -> bytes:
        with tempfile.TemporaryDirectory(prefix="minisign-sign-") as tmp:
            payload_path = Path(tmp) / "payload"
            signature_path = Path(tmp) / "payload.minisig"
            payload_path.write_bytes(payload)
            result = _run(
                [
                    self._executable,
                    "-S",
                    "-s",
                    str(self._secret_key),
                    "-m",
                    str(payload_path),
                    "-x",
                    str(signature_path),
                ]
            )
            if result.returncode != 0:
                raise SigningError(
                    "minisign failed to sign: "
                    f"{result.stderr.decode(errors='replace').strip()}"
                )
            return signature_path.read_bytes()


def signing_backend() -> str | None:
    """Return the available system signing backend, or ``None`` (fail-closed)."""
    if tool_available("gpg"):
        return "gpg"
    if tool_available("minisign"):
        return "minisign"
    return None


@dataclass(frozen=True)
class SignedLock:
    """A signed lock content address, ready to be recorded."""

    lock_hash: str
    key_id: str
    signature: str


def sign_lock(lock: ComponentsLock, signer: Signer) -> SignedLock:
    """Sign a lock's content address (``lock_hash``) with ``signer``."""
    lock_hash = lock.lock_hash()
    signature = signer.sign(lock_hash.encode("utf-8")).decode("utf-8")
    return SignedLock(lock_hash=lock_hash, key_id=signer.key_id, signature=signature)


def record_release(
    lock: ComponentsLock, signer: Signer, log: TransparencyLog
) -> tuple[SignedLock, TransparencyEntry]:
    """Sign a lock and append the signed record to the transparency log."""
    signed = sign_lock(lock, signer)
    entry = log.append(signed.lock_hash, signed.key_id, signed.signature)
    return signed, entry
