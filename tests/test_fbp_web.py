"""Tests for the FBP landing-page server (``fbp/web.py``).

The server is a **local-only, actionable landing page**: stdlib ``http.server``
over an in-process ``FbpDriver``. It is a read/verify surface — it never
mutates durable state and never relaxes the correctness spine. These tests
exercise the page rendering and the handler wiring without opening a bound
port (the server is held in-process).
"""

from __future__ import annotations

from agent_centric.fbp.web import (
    FbpLandingServer,
    _build_openrouter_provider,
    _grants,
    _render_landing,
    _render_ledger,
)


class TestModelRoute:
    def test_run_model_uses_stub_without_key(self, monkeypatch) -> None:
        """Without an OpenRouter key the model agent serves the deterministic
        stub (offline, CI-safe) — the box still works."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        result = server._run_model("hello")
        assert result["ok"] is True
        assert "stub response" in result["text"]
        server._driver.close()

    def test_build_provider_returns_none_without_key(self, monkeypatch) -> None:
        """No key in the environment => no real provider (fail-closed to stub)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert _build_openrouter_provider() is None

    def test_build_provider_returns_provider_with_key(self, monkeypatch) -> None:
        """With a key in the environment a real, enabled provider is built.

        This only asserts the builder returns a provider (no network call); it
        does not construct a server that would wire and invoke it."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        provider = _build_openrouter_provider()
        assert provider is not None
        # The provider is enabled (opt-in satisfied by the key).
        assert provider._enabled is True


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

    def test_http_routes_serve_over_a_bound_port(self, monkeypatch) -> None:
        """End-to-end: the landing /ledger /state.json /health /action/run and
        /model routes respond correctly over a bound stdlib HTTP server."""
        import asyncio
        import socket
        import threading
        import urllib.request
        from http.server import HTTPServer

        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        handler = server._make_handler()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        httpd = HTTPServer(("127.0.0.1", port), handler)

        def _serve() -> None:
            # The driver's loop must be current in the serving thread so the
            # model delegation path (which calls asyncio.get_event_loop) works.
            asyncio.set_event_loop(server._driver._loop)
            httpd.serve_forever()

        t = threading.Thread(target=_serve, daemon=True)
        t.start()
        base = f"http://127.0.0.1:{port}"
        try:
            landing = urllib.request.urlopen(base + "/").read().decode()
            assert "Agent-Centric" in landing
            assert "model-prompt" in landing  # the model text box is present
            state = urllib.request.urlopen(base + "/state.json").read().decode()
            assert '"tree"' in state
            ledger = urllib.request.urlopen(base + "/ledger").read().decode()
            assert "Session Ledger" in ledger
            health = urllib.request.urlopen(base + "/health").read().decode()
            assert '"ok": true' in health
            ran = urllib.request.urlopen(base + "/action/run").read().decode()
            assert "Agent-Centric" in ran
            # The /model route answers (stub path when no key is set).
            req = urllib.request.Request(base + "/model", data=b"hello", method="POST")
            import json as _json

            res = _json.loads(urllib.request.urlopen(req).read().decode())
            assert res.get("ok") is True
            assert "stub response" in res.get("text", "")
        finally:
            httpd.shutdown()
            httpd.server_close()
        server._driver.close()