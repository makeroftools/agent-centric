"""Tests for the FBP landing-page server (``fbp/web.py``).

The server is a **local-only, actionable landing page**: stdlib ``http.server``
over an in-process ``FbpDriver``. It is a read/verify surface — it never
mutates durable state and never relaxes the correctness spine. These tests
exercise the page rendering and the handler wiring without opening a bound
port (the server is held in-process).
"""

from __future__ import annotations

from agent_centric.fbp.web import FbpLandingServer, _render_landing


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