"""Signature verification for component artifacts (SPEC-0007 §6).

Signatures are **mandatory** and verification **fails closed**. The default
backends shell out to a system tool (``minisign`` or ``gpg``) — no Python crypto
dependency is added. A verifier is injected into the resolver; when no verifier
is available, resolution refuses (fail-closed) rather than proceeding unsigned.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Protocol


class SignatureError(Exception):
    """Signature verification failed or was unavailable (fail-closed)."""


class SignatureVerifier(Protocol):
    """Verifies a detached signature over a payload."""

    def verify(self, payload: bytes, signature: bytes) -> None:
        """Return None if valid; raise :class:`SignatureError` otherwise."""
        ...


def tool_available(name: str) -> bool:
    """Return whether an executable is on ``PATH``."""
    return shutil.which(name) is not None


def _run(argv: list[str]) -> subprocess.CompletedProcess[bytes]:
    try:
        return subprocess.run(argv, capture_output=True, check=False)
    except FileNotFoundError as exc:  # pragma: no cover - environment-dependent
        raise SignatureError(f"verifier not found: {argv[0]}") from exc


class MinisignVerifier:
    """Verify a detached minisign signature against a public key string."""

    def __init__(self, public_key: str, *, executable: str = "minisign") -> None:
        if not public_key:
            raise SignatureError("minisign public key is required")
        self._public_key = public_key
        self._executable = executable

    def verify(self, payload: bytes, signature: bytes) -> None:
        if not signature:
            raise SignatureError("missing signature")
        with tempfile.TemporaryDirectory(prefix="minisign-") as tmp:
            payload_path = Path(tmp) / "payload"
            signature_path = Path(tmp) / "payload.sig"
            payload_path.write_bytes(payload)
            signature_path.write_bytes(signature)
            result = _run(
                [
                    self._executable,
                    "-V",
                    "-m",
                    str(payload_path),
                    "-x",
                    str(signature_path),
                    "-P",
                    self._public_key,
                ]
            )
        if result.returncode != 0:
            raise SignatureError(
                f"minisign rejected the signature: {result.stderr.decode(errors='replace').strip()}"
            )


class GpgVerifier:
    """Verify a detached GPG signature with the local keyring."""

    def __init__(self, *, executable: str = "gpg") -> None:
        self._executable = executable

    def verify(self, payload: bytes, signature: bytes) -> None:
        if not signature:
            raise SignatureError("missing signature")
        with tempfile.TemporaryDirectory(prefix="gpg-") as tmp:
            payload_path = Path(tmp) / "payload"
            signature_path = Path(tmp) / "payload.sig"
            payload_path.write_bytes(payload)
            signature_path.write_bytes(signature)
            result = _run(
                [self._executable, "--verify", str(signature_path), str(payload_path)]
            )
        if result.returncode != 0:
            raise SignatureError(
                f"gpg rejected the signature: {result.stderr.decode(errors='replace').strip()}"
            )


class SystemVerifier:
    """Select an available system verifier, or refuse (fail-closed).

    Prefers ``minisign`` when a public key is supplied; otherwise ``gpg``.
    """

    def __init__(self, *, minisign_public_key: str = "") -> None:
        self._impl: SignatureVerifier
        if minisign_public_key and tool_available("minisign"):
            self._impl = MinisignVerifier(minisign_public_key)
        elif tool_available("gpg"):
            self._impl = GpgVerifier()
        else:
            raise SignatureError(
                "no signature verifier available (need minisign with a public key, or gpg)"
            )

    def verify(self, payload: bytes, signature: bytes) -> None:
        self._impl.verify(payload, signature)
