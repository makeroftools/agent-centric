"""Tests for the external-surfaces e2e demo (examples/fbp_external_demo.py).

The demo drives BOTH edge transports (ACP and MCP) through the shared
FbpDriverHost verified spine, offline and deterministic. These tests prove the
demo runs end-to-end and reports honest verified/fail-closed outcomes — the
same guarantees the operator sees when they run it directly.
"""

from __future__ import annotations

import contextlib
import io

import examples.fbp_external_demo as demo


def _run_demo() -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = demo.main()
    return code, out.getvalue()


class TestExternalDemo:
    def test_demo_runs_both_transports(self) -> None:
        code, out = _run_demo()
        assert code == 0
        assert "ACP — Agent Client Protocol over the verified spine" in out
        assert "MCP — Model Context Protocol tools over the verified spine" in out

    def test_demo_reports_verified_and_fail_closed(self) -> None:
        _, out = _run_demo()
        # Verified arithmetic through both transports.
        assert "verified arithmetic" in out
        assert "verified double -> 42" in out
        # Fail-closed paths are reported honestly, never as verified success.
        assert "ungranted store get fails closed" in out
        assert "unknown command fails closed" in out
        # The demo reaches the same spine from both transports.
        assert "Both transports reached the same verified, fail-closed spine." in out