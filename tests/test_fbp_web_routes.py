"""End-to-end HTTP route coverage for the FBP landing page.

This is the "is the interface really working" test: it drives EVERY served route
over a real bound stdlib HTTP server (not by calling server methods directly),
so it catches wiring, JSON-shape, status-code, and content-type bugs that
direct-method unit tests cannot. It mirrors how a human actually clicks the UI.

Routes are grouped by how the UI reaches them:
- the landing page + action/ledger/state links (GET, HTML or JSON),
- the Chat pane: model box, streaming, history, orchestrate + schema form,
- the Registry pane: experts, domains, artifacts, slm readouts,
- the Provision pane: domains, catalog, run-provision,
- the Designer pane: network run / save / list / load,
- the dashboard Activity card,
- the demonstration bills backend (no tab; routes still served),
- fail-closed unknown paths.
"""

from __future__ import annotations

import asyncio
import json
import socket
import threading
import urllib.request
from http.server import HTTPServer

from agent_centric.fbp.web import FbpLandingServer


def _read_sse(port: int, path: str, body: str) -> str:
    """POST to an SSE route and read events until the stream ends.

    ``urllib.urlopen`` blocks on a keep-alive SSE stream (no Content-Length), so
    we open a raw socket, write the request, and read incrementally until the
    server closes the stream (after the ``done`` event). Returns the raw SSE
    body. This is how a browser actually consumes the stream.
    """
    import socket as _socket

    s = _socket.create_connection(("127.0.0.1", port), timeout=10)
    try:
        req = (
            f"POST {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            "Content-Type: application/json\r\n"
            f"Content-Length: {len(body)}\r\n"
            "Connection: close\r\n"
            "\r\n"
            + body
        )
        s.sendall(req.encode("utf-8"))
        chunks: list[str] = []
        while True:
            data = s.recv(4096)
            if not data:
                break
            chunks.append(data.decode("utf-8", errors="replace"))
            # The SSE stream ends with a ``done`` event; stop once we have it.
            # (The server keeps the connection alive, so we must not wait for
            # the socket to close.)
            if b'"type":"done"' in data or b'"type": "done"' in data:
                break
        return "".join(chunks)
    finally:
        s.close()


class _BoundServer:
    """Start the landing server on a real loopback port and drive it."""

    def __init__(self, monkeypatch, **server_kwargs: object) -> None:
        # No OpenRouter key => the model box serves the deterministic stub.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        self._server = FbpLandingServer(**server_kwargs)
        handler = self._server._make_handler()
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        self.port = s.getsockname()[1]
        s.close()
        self._httpd = HTTPServer(("127.0.0.1", self.port), handler)

        def _serve() -> None:
            # The driver's loop must be current in the serving thread so the
            # model delegation path (which calls asyncio.get_event_loop) works.
            asyncio.set_event_loop(self._server._driver._loop)
            self._httpd.serve_forever()

        self._thread = threading.Thread(target=_serve, daemon=True)
        self._thread.start()
        self.base = f"http://127.0.0.1:{self.port}"

    def get(self, path: str) -> tuple[int, str, str]:
        with urllib.request.urlopen(self.base + path) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()

    def headers(self, path: str) -> dict:
        """GET a path and return its full response headers (for hardening checks)."""
        with urllib.request.urlopen(self.base + path) as resp:
            return dict(resp.headers.items())

    def post(self, path: str, body: str = "") -> tuple[int, str, str]:
        req = urllib.request.Request(
            self.base + path,
            data=body.encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read().decode()

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._server._driver.close()


class TestLandingAndActionLinks:
    def test_landing_page_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, html = srv.get("/")
            assert status == 200
            assert "text/html" in ctype
            assert "Agent-Centric" in html
            # The sidebar nav is present with the main panes.
            for page in ("dashboard", "chat", "provision", "registry", "designer", "docs"):
                assert f"data-page='{page}'" in html
        finally:
            srv.close()

    def test_index_alias_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, html = srv.get("/index.html")
            assert status == 200
            assert "Agent-Centric" in html
        finally:
            srv.close()

    def test_action_run_serves_html(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, html = srv.get("/action/run")
            assert status == 200
            assert "text/html" in ctype
            assert "Agent-Centric" in html
        finally:
            srv.close()

    def test_ledger_serves_html(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, html = srv.get("/ledger")
            assert status == 200
            assert "text/html" in ctype
            assert "Session Ledger" in html
        finally:
            srv.close()

    def test_state_json_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/state.json")
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert "tree" in data and "summary" in data
        finally:
            srv.close()

    def test_health_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/health")
            assert status == 200
            assert "application/json" in ctype
            assert json.loads(body)["ok"] is True
        finally:
            srv.close()


class TestChatPane:
    def test_model_uses_stub(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.post("/model", json.dumps({"prompt": "hello"}))
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert "stub response" in data["text"]
            assert data["verified"] is True
        finally:
            srv.close()

    def test_model_stream_serves_sse(self, monkeypatch) -> None:
        """The streaming route emits real SSE events (meta/chunk/done).

        ``urlopen`` would block on the keep-alive stream, so we read the raw
        socket incrementally and stop once the ``done`` event arrives — the way
        a browser consumes the SSE stream.
        """
        srv = _BoundServer(monkeypatch)
        try:
            body = _read_sse(srv.port, "/model/stream", json.dumps({"prompt": "hi"}))
            assert "data: " in body
            assert "stub response" in body
            assert "\"type\":\"done\"" in body or "\"type\": \"done\"" in body
        finally:
            srv.close()

    def test_history_and_clear(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            # A model run records a user+assistant turn.
            srv.post("/model", json.dumps({"prompt": "hello"}))
            status, _, body = srv.get("/history")
            assert status == 200
            data = json.loads(body)
            assert len(data["history"]) == 2
            # Clear empties it.
            status, _, body = srv.post("/history/clear")
            assert status == 200
            assert json.loads(body)["ok"] is True
            status, _, body = srv.get("/history")
            assert json.loads(body)["history"] == []
        finally:
            srv.close()

    def test_orchestrate_runs_artifact(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.post(
                "/orchestrate", json.dumps({"task": "double", "args": {"value": 21}})
            )
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert data["results"][0]["value"] == 42
            assert data["results"][0]["verified"] is True
        finally:
            srv.close()

    def test_orchestrate_schema_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/orchestrate/schema")
            assert status == 200
            assert "application/json" in ctype
            schemas = json.loads(body)["schemas"]
            assert "run" in schemas and "sum" in schemas and "double" in schemas
        finally:
            srv.close()


class TestRegistryPane:
    def test_experts_readout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/experts")
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert isinstance(data["total_cost"], int)
            assert len(data["domains"]) >= 1
        finally:
            srv.close()

    def test_domains_readout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, body = srv.get("/domains")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert data["count"] >= 1
        finally:
            srv.close()

    def test_artifacts_readout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, body = srv.get("/artifacts")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert isinstance(data["total_cost"], int)
        finally:
            srv.close()

    def test_slm_readout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, body = srv.get("/slm")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert len(data["warranted"]) >= 1
        finally:
            srv.close()


class TestProvisionPane:
    def test_provision_domains(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, body = srv.get("/provision/domains")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert len(data["domains"]) >= 1
        finally:
            srv.close()

    def test_catalog(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, body = srv.get("/catalog")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert len(data["bases"]) >= 10
        finally:
            srv.close()

    def test_provision_runs(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.post(
                "/provision", json.dumps({"domain": "bill-extract", "provider": "stub"})
            )
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert "account" in data["result"]
        finally:
            srv.close()


class TestDesignerPane:
    def test_network_run(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            payload = (
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            status, ctype, body = srv.post("/network", payload)
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert data["results"][0]["value"] == 42
        finally:
            srv.close()

    def test_network_save_list_load(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            net = (
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            # Save
            status, _, body = srv.post(
                "/network/save", json.dumps({"name": "demo", "network": json.loads(net)})
            )
            assert status == 200
            assert json.loads(body)["ok"] is True
            # List
            status, _, body = srv.get("/network/list")
            assert json.loads(body)["names"] == ["demo"]
            # Load
            status, _, body = srv.get("/network/load/demo")
            data = json.loads(body)
            assert data["ok"] is True
            assert data["network"]["components"][0]["id"] == "a"
        finally:
            srv.close()


class TestDashboardActivity:
    def test_activity_readout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            # A demo action records an activity entry.
            srv.get("/action/run")
            status, ctype, body = srv.get("/activity")
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["count"] >= 1
            assert data["entries"][0]["kind"] == "action"
        finally:
            srv.close()


class TestBillsDemoBackend:
    """The bills loop is demonstration-only (no tab), but its routes are still
    served so the loop stays runnable via its routes / tests / examples."""

    def test_bills_intake_accept_calendar(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            draft = {
                "id": "bill-http-1",
                "vendor": "GasCo",
                "amount_cents": 12345,
                "due_date": "2026-10-01",
            }
            status, _, body = srv.post("/bills/intake", json.dumps({"draft": draft}))
            assert status == 200
            assert json.loads(body)["ok"] is True

            status, _, body = srv.post("/bills/accept", json.dumps({"draft": draft}))
            assert status == 200
            assert json.loads(body)["ok"] is True

            status, _, body = srv.get("/bills/registry")
            assert status == 200
            assert json.loads(body)["count"] == 1

            status, _, body = srv.post(
                "/bills/calendar",
                json.dumps({"from_date": "2026-10-01", "to_date": "2026-10-31"}),
            )
            assert status == 200
            assert [e["id"] for e in json.loads(body)["entries"]] == ["bill-http-1"]
        finally:
            srv.close()


class TestExternalSurfaces:
    """The Connect pane's /surfaces route serves ACP/MCP over HTTP.

    Proves the external edge transports are discoverable from the landing page
    as a read-only catalog, with the sidebar Connect pane rendered.
    """

    def test_surfaces_route_serves(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/surfaces")
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ok"] is True
            assert {s["kind"] for s in data["surfaces"]} == {"acp", "mcp"}
            assert data["surfaces"][0]["entry"][0] == "agent-centric acp"
        finally:
            srv.close()

    def test_connect_pane_on_landing(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, _, html = srv.get("/")
            assert status == 200
            assert "data-page='connect'" in html
            assert "pane-connect" in html
        finally:
            srv.close()


class TestProductionHardening:
    """Loopback landing server ships conservative, additive HTTP hardening."""

    def test_security_response_headers(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            hdr = srv.headers("/")
            assert hdr.get("X-Content-Type-Options") == "nosniff"
            assert hdr.get("X-Frame-Options") == "DENY"
            assert hdr.get("Referrer-Policy") == "no-referrer"
        finally:
            srv.close()

    def test_no_server_version_disclosure(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            hdr = srv.headers("/")
            server = hdr.get("Server", "")
            assert "http.server" not in server
            assert "Python" not in server
            assert server.startswith("Agent-Centric-FBP/")
        finally:
            srv.close()

    def test_handler_has_read_timeout(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            handler_cls = srv._server._make_handler()
            assert handler_cls.timeout >= 1
        finally:
            srv.close()

    def test_oversized_payload_rejected_413(self, monkeypatch) -> None:
        """An oversized POST body is rejected with 413 before being read."""
        srv = _BoundServer(monkeypatch)
        try:
            big = '{"prompt": "' + ("x" * 70000) + '"}'
            req = urllib.request.Request(
                srv.base + "/model",
                data=big.encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            try:
                urllib.request.urlopen(req)
                raise AssertionError("expected 413")
            except urllib.request.HTTPError as exc:
                assert exc.code == 413
        finally:
            srv.close()

    def test_at_bound_payload_accepted(self, monkeypatch) -> None:
        """A body at the size bound is still accepted (not rejected)."""
        srv = _BoundServer(monkeypatch)
        try:
            # Just under the 65,536 bound: a model prompt that fits.
            prompt = "hello " + ("x" * (50000))
            req = urllib.request.Request(
                srv.base + "/model",
                data=json.dumps({"prompt": prompt}).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req) as resp:
                assert resp.status == 200
                data = json.loads(resp.read().decode("utf-8"))
                assert data["ok"] is True
        finally:
            srv.close()


class TestReadiness:
    """The /ready probe exercises the verified spine (fail-closed), unlike /health."""

    def test_ready_reports_verified_when_spine_works(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/ready")
            assert status == 200
            assert "application/json" in ctype
            data = json.loads(body)
            assert data["ready"] is True
            assert data["verified"] is True
            assert data["probe"] == "double(21)=42"
        finally:
            srv.close()

    def test_ready_fails_closed_when_spine_cannot_verify(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            # Sabotage the spine: unregister the task the probe runs, so the
            # readiness probe cannot produce a verified result.
            srv._server._driver._root._registry._entries.pop("double", None)
            import urllib.error

            try:
                srv.get("/ready")
                raise AssertionError("expected 503")
            except urllib.error.HTTPError as exc:
                # A 503 (fail-closed not-ready) surfaces as an HTTPError.
                assert exc.code == 503
                data = json.loads(exc.read().decode("utf-8"))
                assert data["ready"] is False
                assert data["verified"] is False
        finally:
            srv.close()

    def test_health_remains_pure_liveness(self, monkeypatch) -> None:
        """/health stays a liveness check and does not exercise the spine."""
        srv = _BoundServer(monkeypatch)
        try:
            status, ctype, body = srv.get("/health")
            assert status == 200
            data = json.loads(body)
            assert data["ok"] is True
            assert "ready" not in data
        finally:
            srv.close()


class TestFailClosed:
    def test_unknown_path_returns_404(self, monkeypatch) -> None:
        srv = _BoundServer(monkeypatch)
        try:
            import urllib.error

            try:
                srv.get("/no-such-route")
                raise AssertionError("expected 404")
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
        finally:
            srv.close()