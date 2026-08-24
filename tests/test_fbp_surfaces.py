"""Tests for the external-surfaces (Connect) pane and /surfaces route.

The FBP platform is reachable from outside through two deterministic edge
transports: ACP (an External Agent inside Zed) and MCP (tools for any
MCP-capable host). Both route every call through the FbpDriver verified spine
— a normal directive, parent re-verified, ledgered, replayable, fail-closed —
and neither can produce a verified success that bypasses verification.

These tests prove the landing page makes those surfaces discoverable:
- the sidebar nav exposes a ``connect`` pane;
- the landing page renders the Connect card + its client script;
- the read-only ``/surfaces`` route serves the ACP/MCP catalog (entry points,
  store grant prefix, live model mode, and the command/tool reference);

The surfaces catalog is passive and additive: it launches nothing, changes no
state, and exposes never the OpenRouter key.
"""

from __future__ import annotations

from agent_centric.fbp.web import FbpLandingServer, _render_landing


class TestSurfacesReadout:
    """The /surfaces route serves a deterministic ACP/MCP catalog."""

    def _readout(self) -> dict:
        server = FbpLandingServer()
        try:
            return server._surfaces_readout()
        finally:
            server._driver.close()

    def test_surfaces_route_ok(self) -> None:
        data = self._readout()
        assert data["ok"] is True
        assert {s["kind"] for s in data["surfaces"]} == {"acp", "mcp"}

    def test_acp_entry_points(self) -> None:
        acp = next(s for s in self._readout()["surfaces"] if s["kind"] == "acp")
        assert "agent-centric acp" in acp["entry"]
        assert "agent-centric-acp" in acp["entry"]
        assert any("agent_centric.acp" in e for e in acp["entry"])

    def test_mcp_entry_points(self) -> None:
        mcp = next(s for s in self._readout()["surfaces"] if s["kind"] == "mcp")
        assert "agent-centric mcp" in mcp["entry"]
        assert "agent-centric-mcp" in mcp["entry"]
        assert any("agent_centric.mcp" in e for e in mcp["entry"])

    def test_store_grant_prefixes(self) -> None:
        by_kind = {s["kind"]: s for s in self._readout()["surfaces"]}
        assert by_kind["acp"]["store_prefix"] == "acp-*"
        assert by_kind["mcp"]["store_prefix"] == "mcp-*"

    def test_model_mode_never_leaks_key(self, monkeypatch) -> None:
        # No key => stub mode reported (never the key itself).
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        by_kind = {s["kind"]: s for s in self._readout()["surfaces"]}
        assert by_kind["acp"]["model_mode"] == "stub"
        assert by_kind["mcp"]["model_mode"] == "stub"

    def test_catalog_reference_present(self) -> None:
        for s in self._readout()["surfaces"]:
            terms = " ".join(" ".join(t) for t in s["catalog"]).lower()
            assert "double" in terms or "sum" in terms
            assert "fail-closed" in terms or "fail closed" in terms


class TestConnectPane:
    """The landing page renders the Connect pane and client script."""

    def test_sidebar_has_connect(self) -> None:
        server = FbpLandingServer()
        try:
            html = _render_landing(server._page_state())
        finally:
            server._driver.close()
        assert "data-page='connect'" in html
        assert "pane-connect" in html
        # A Connect section groups it with the Learn nav.
        assert "Connect" in html

    def test_connect_card_rendered(self) -> None:
        server = FbpLandingServer()
        try:
            html = _render_landing(server._page_state())
        finally:
            server._driver.close()
        assert "External surfaces" in html
        assert "surfaces-list" in html
        # The client script fetches the read-only catalog.
        assert "fetch('/surfaces')" in html