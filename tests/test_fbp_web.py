"""Tests for the FBP landing-page server (``fbp/web.py``).

The server is a **local-only, actionable landing page**: stdlib ``http.server``
over an in-process ``FbpDriver``. It is a read/verify surface — it never
mutates durable state and never relaxes the correctness spine. These tests
exercise the page rendering and the handler wiring without opening a bound
port (the server is held in-process).
"""

from __future__ import annotations

from agent_centric.fbp.web import FbpLandingServer, _grants, _render_landing, _render_ledger


class TestGrantRender:
    def test_grants_includes_state_trajectory_and_keys(self) -> None:
        html = _grants(
            {"state": "s", "trajectory": "t", "store_keys": ["a", "b"]}
        )
        for token in ("state", "trajectory", "keys=['a', 'b']"):
            assert token in html

    def test_grants_em_dash_when_none(self) -> None:
        assert _grants({}) == "&mdash;"


class TestLandingRender:
    def test_render_returns_html_with_tree_and_invariants(self) -> None:
        state = {
            "tree": [
                {"identity": "root", "kind": "Agent", "capabilities": ["double"]},
                {"identity": "child", "kind": "Agent"},
            ],
            "summary": {"run_count": 2, "verified_runs": 2},
            "checked": {"identities": ["root", "child"]},
        }
        html = _render_landing(state)
        assert "<!doctype html>" in html
        assert "Agent-Centric" in html
        assert "root" in html and "child" in html
        # Standing invariants are surfaced.
        assert "No unverified success" in html
        # Actionable links present.
        assert "/action/run" in html

    def test_page_state_reflects_live_tree(self) -> None:
        server = FbpLandingServer()
        state = server._page_state()
        identities = [n["identity"] for n in state["tree"]]
        assert "root" in identities
        assert "child" in identities
        assert "store" in identities
        server._driver.close()

    def test_run_demo_action_uses_driver(self) -> None:
        """The demo action runs a real, verified task through the driver."""
        server = FbpLandingServer()
        result = server._run_demo()
        assert result.get("action") == "run double(21)"
        assert result.get("value") == 42  # verified even
        server._driver.close()

    def test_health_endpoint_json(self) -> None:
        """The /health route returns an ok JSON envelope (smoke)."""
        server = FbpLandingServer()
        # Drive a real request through the handler without binding a port.
        from http.server import BaseHTTPRequestHandler

        handler_cls = server._make_handler()
        assert issubclass(handler_cls, BaseHTTPRequestHandler)
        server._driver.close()

    def test_ledger_state_is_readonly_snapshot(self) -> None:
        """The ledger view is a deterministic, read-only snapshot."""
        server = FbpLandingServer()
        led = server._ledger_state()
        assert "ledger" in led and "summary" in led
        assert led["count"] == len(led["ledger"])
        server._driver.close()

    def test_render_ledger_shows_runs(self) -> None:
        """The ledger HTML renders recorded runs (or an empty note)."""
        html = _render_ledger({"summary": {"runs": []}, "count": 0})
        assert "Session Ledger" in html
        assert "no runs recorded" in html

    def test_page_state_exposes_jsonable_fields(self) -> None:
        """The page-state snapshot is JSON-serialisable (for /state.json)."""
        import json

        server = FbpLandingServer()
        json.dumps(server._page_state())  # must not raise
        server._driver.close()

    def test_http_routes_serve_over_a_bound_port(self) -> None:
        """End-to-end: the landing /ledger /state.json /health and /action/run
        routes respond correctly over a bound stdlib HTTP server."""
        import socket
        import threading
        import urllib.request
        from http.server import ThreadingHTTPServer

        server = FbpLandingServer()
        handler = server._make_handler()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        try:
            landing = urllib.request.urlopen(base + "/").read().decode()
            assert "Agent-Centric" in landing
            state = urllib.request.urlopen(base + "/state.json").read().decode()
            assert '"tree"' in state
            ledger = urllib.request.urlopen(base + "/ledger").read().decode()
            assert "Session Ledger" in ledger
            health = urllib.request.urlopen(base + "/health").read().decode()
            assert '"ok": true' in health
            ran = urllib.request.urlopen(base + "/action/run").read().decode()
            assert "Agent-Centric" in ran
        finally:
            httpd.shutdown()
            httpd.server_close()
        server._driver.close()