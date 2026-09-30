"""Tests for signature verification (system-tool backends, no crypto dep)."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_centric.fbp.signing import (
    GpgVerifier,
    MinisignVerifier,
    SignatureError,
    SystemVerifier,
    tool_available,
)


def _scrip_tool(path: Path, code: int) -> str:
    """Create a fake verifier executable that exits with ``code``."""
    script = path / f"tool-{code}.sh"
    script.write_text(f"#!/bin/sh\nexit {code}\n")
    script.chmod(0o755)
    return str(script)


class TestMinisignVerifier:
    def test_accepts_zero_exit(self, tmp_path: Path) -> None:
        MinisignVerifier("PUBKEY", executable=_scrip_tool(tmp_path, 0)).verify(b"p", b"s")

    def test_rejects_nonzero_exit(self, tmp_path: Path) -> None:
        with pytest.raises(SignatureError):
            MinisignVerifier("PUBKEY", executable=_scrip_tool(tmp_path, 1)).verify(b"p", b"s")

    def test_missing_signature_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(SignatureError):
            MinisignVerifier("PUBKEY", executable=_scrip_tool(tmp_path, 0)).verify(b"p", b"")

    def test_missing_tool_fails_closed(self, tmp_path: Path) -> None:
        v = MinisignVerifier("PUBKEY", executable=str(tmp_path / "absent"))
        with pytest.raises(SignatureError):
            v.verify(b"p", b"s")

    def test_requires_public_key(self) -> None:
        with pytest.raises(SignatureError):
            MinisignVerifier("")


class TestGpgVerifier:
    def test_accepts_zero_exit(self, tmp_path: Path) -> None:
        GpgVerifier(executable=_scrip_tool(tmp_path, 0)).verify(b"p", b"s")

    def test_rejects_nonzero_exit(self, tmp_path: Path) -> None:
        with pytest.raises(SignatureError):
            GpgVerifier(executable=_scrip_tool(tmp_path, 1)).verify(b"p", b"s")


def test_tool_available_reflects_path() -> None:
    assert tool_available("sh") is True
    assert tool_available("definitely-not-a-real-tool-xyz") is False


class TestSystemVerifier:
    def test_refuses_when_no_tool(self, monkeypatch: pytest.MonkeyPatch) -> None:
        import agent_centric.fbp.signing as signing

        monkeypatch.setattr(signing, "tool_available", lambda name: False)
        with pytest.raises(SignatureError):
            SystemVerifier(minisign_public_key="K")
