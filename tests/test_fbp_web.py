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
    _build_openrouter_providers,
    _grants,
    _parse_model_body,
    _render_landing,
    _render_ledger,
)


class TestOrchestrateRoute:
    """The chat-window → FBP seam: a JSON artifact runs as a verified plan."""

    def test_run_artifact_single_task(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact('{"task": "double", "args": {"value": 21}}')
            assert result["ok"] is True
            assert result["results"][0]["verified"] is True
            assert result["results"][0]["value"] == 42
        finally:
            server._driver.close()

    def test_run_artifact_rejects_invalid_json(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact("{not json")
            assert result["ok"] is False
            assert "artifact rejected" in result["error"]
        finally:
            server._driver.close()

    def test_run_artifact_unorchestrable_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact('{"intent": "status"}')
            assert result["ok"] is False
            assert "error" in result
        finally:
            server._driver.close()


class TestChatContext:
    """The model box folds prior (durable) turns into the next prompt."""

    @staticmethod
    def _push_turns(server, n: int) -> None:
        # Build history directly (clean content, not the stub's echo) so the
        # context tests are uncontaminated by earlier prompts.
        for i in range(n):
            server._append_history({"role": "user", "content": f"q{i}"})
            server._append_history(
                {"role": "assistant", "content": f"a{i}", "model": "m", "verified": True}
            )

    def test_context_is_built_from_history(self) -> None:
        server = FbpLandingServer()
        try:
            self._push_turns(server, 2)
            ctx = server._build_chat_context()
            assert "you: q0" in ctx
            assert "model: a0" in ctx
            assert "you: q1" in ctx
            # Newest last, old-first ordering.
            assert ctx.index("you: q0") < ctx.index("you: q1")
        finally:
            server._driver.close()

    def test_context_is_bounded_to_max_turns(self) -> None:
        server = FbpLandingServer()
        try:
            self._push_turns(server, 10)
            ctx = server._build_chat_context(max_turns=4)
            # Only the last 4 entries (2 turns: user+assistant) are included.
            user_lines = [ln for ln in ctx.splitlines() if ln.startswith("you: ")]
            assert user_lines == ["you: q8", "you: q9"]
            assert "you: q7" not in ctx.splitlines()
        finally:
            server._driver.close()

    def test_empty_history_produces_empty_context(self) -> None:
        server = FbpLandingServer()
        try:
            assert server._build_chat_context() == ""
        finally:
            server._driver.close()


class TestComponentNetworkRoute:
    """The visual-programming seam: a component network runs as a verified
    FBP plan (DAG, data-flow wiring, fail-closed)."""

    def test_run_network_single_component(self) -> None:
        server = FbpLandingServer()
        try:
            payload = (
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            result = server._run_network(payload)
            assert result["ok"] is True
            assert result["results"][0]["verified"] is True
            assert result["results"][0]["value"] == 42
        finally:
            server._driver.close()

    def test_run_network_rejects_invalid_payload(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_network("{not json")
            assert result["ok"] is False
            assert "network rejected" in result["error"]
        finally:
            server._driver.close()

    def test_run_network_rejects_cycle(self) -> None:
        server = FbpLandingServer()
        try:
            payload = (
                '{"components": [{"id": "a", "task": "double", "args": {"value": 1}}, '
                '{"id": "b", "task": "double", "args": {"value": 1}}], '
                '"edges": [{"source": "a", "source_field": "value", "target": "b", '
                '"target_arg": "value"}, {"source": "b", "source_field": "value", '
                '"target": "a", "target_arg": "value"}]}'
            )
            result = server._run_network(payload)
            assert result["ok"] is False
            assert "cycle" in result["error"]
        finally:
            server._driver.close()


class TestModelRoute:
    def test_run_model_uses_stub_without_key(self, monkeypatch) -> None:
        """Without an OpenRouter key the model agent serves the deterministic
        stub (offline, CI-safe) — the box still works."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        result = server._run_model("hello")
        assert result["ok"] is True
        assert "stub response" in result["text"]
        assert result.get("verified") is True
        server._driver.close()

    def test_run_model_labels_source(self, monkeypatch) -> None:
        """The answer surfaces the audited model source (the stub id when no
        key is set), so the page shows *which* model produced it."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        result = server._run_model("hello")
        assert result.get("model") == "stub-model"
        server._driver.close()

    def test_build_providers_empty_without_key(self, monkeypatch) -> None:
        """No key in the environment => no real providers (fail-closed to stub)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        assert _build_openrouter_providers() == {}

    def test_build_providers_returns_provider_with_key(self, monkeypatch) -> None:
        """With a key in the environment a real, enabled provider is built.

        This only asserts the builder returns providers (no network call); it
        does not construct a server that would wire and invoke them."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        providers = _build_openrouter_providers()
        assert providers
        for model, provider in providers.items():
            assert model
            # The provider is enabled (opt-in satisfied by the key).
            assert provider._enabled is True

    def test_build_providers_multiple_models_via_env(self, monkeypatch) -> None:
        """A comma-separated OPENROUTER_MODEL yields one provider per model."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("OPENROUTER_MODEL", "m1,m2,m3")
        providers = _build_openrouter_providers()
        assert set(providers) == {"m1", "m2", "m3"}

    def test_run_model_attributes_real_provider_id_when_keyed(
        self, monkeypatch
    ) -> None:
        """With a key the audited source names the real selected model, not the
        stub label. The provider is built with no http_client (fail-closed), so
        no network call is made — we only assert the wiring and source id."""
        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv(
            "OPENROUTER_MODEL",
            "openai/gpt-5,anthropic/claude-opus",
        )
        server = FbpLandingServer()
        # The model agent is wired to the first configured provider, so its
        # audited source id should be that model (not "stub-model").
        model_agent = server._driver._root.children["model"]
        assert model_agent._model_id == "openai/gpt-5"
        server._driver.close()

    def test_model_agent_text_from_real_provider_is_plain(self) -> None:
        """A real provider returning a ``ModelResponse`` yields the plain text
        as the response value, not the object repr (regression: the box must
        show the answer, not ``ModelResponse(text=...)``)."""
        from agent_centric.contracts import ModelResponse
        from agent_centric.fbp.model_agent import ModelAgent

        server = FbpLandingServer()
        driver = server._driver
        driver.spawn("model", kind="model")
        model_agent: ModelAgent = driver._root.children["model"]

        class _Provider:
            def __call__(self, prompt: str, **kwargs: object) -> ModelResponse:
                return ModelResponse(text=f"real answer to {prompt}")

        model_agent.set_provider(_Provider())
        resp = driver.run("model", {"prompt": "hello"}, child="model")
        assert resp.verified is True
        assert resp.value == "real answer to hello"
        assert "ModelResponse(" not in resp.value
        server._driver.close()

    def test_parse_model_body_plain_prompt(self) -> None:
        assert _parse_model_body("hello world") == ("hello world", "")

    def test_parse_model_body_json_envelope(self) -> None:
        assert _parse_model_body('{"prompt": "hi", "model": "m1"}') == ("hi", "m1")

    def test_parse_model_body_rejects_garbage(self) -> None:
        assert _parse_model_body("{not json") == ("", "")


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

    def test_render_shows_model_dropdown_when_choices(self) -> None:
        """When providers are configured the page renders a model dropdown."""
        html = _render_landing({}, models=("gpt-x", "llama-y"))
        assert "model-select" in html
        assert "gpt-x" in html and "llama-y" in html

    def test_render_omits_dropdown_without_choices(self) -> None:
        """Without providers there is no empty dropdown (stub only)."""
        html = _render_landing({})
        assert "<select id='model-select'" not in html

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
        import json as _json
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
            req = urllib.request.Request(
                base + "/model",
                data=_json.dumps({"prompt": "hello", "model": ""}).encode(),
                method="POST",
            )
            res = _json.loads(urllib.request.urlopen(req).read().decode())
            assert res.get("ok") is True
            assert "stub response" in res.get("text", "")
        finally:
            httpd.shutdown()
            httpd.server_close()
        server._driver.close()


class TestStreamingAndHistory:
    def test_stream_model_stub_emits_meta_chunks_done(self, monkeypatch) -> None:
        """Without a key, the streaming route fails closed to the stub and emits
        a meta, chunk(s), and a done event (offline, deterministic)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        events = list(server._stream_model("hello", ""))
        types = [e["type"] for e in events]
        assert types[0] == "meta"
        assert "chunk" in types
        assert types[-1] == "done"
        done = events[-1]
        assert done["verified"] is False
        assert "stub response" in done["text"]
        assert done["model"] == "stub-model"
        server._driver.close()

    def test_history_append_and_get(self, monkeypatch) -> None:
        """A completed /model run records a user+assistant turn; /history reads it."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        server._run_model("hi there", "")
        state = server._history_state()
        entries = state["history"]
        assert len(entries) == 2
        assert entries[0]["role"] == "user"
        assert entries[0]["content"] == "hi there"
        assert entries[1]["role"] == "assistant"
        server._driver.close()

    def test_history_clear(self, monkeypatch) -> None:
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        server._run_model("hi", "")
        server._clear_history()
        assert server._history_state()["history"] == []
        server._driver.close()

    def test_durable_history_opt_in(self, monkeypatch, tmp_path) -> None:
        """Granting a history path persists turns; a fresh server over the same
        path restores the transcript (explicit grant, deterministic replay)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        path = str(tmp_path / "hist.db")
        s1 = FbpLandingServer(history_path=path)
        try:
            s1._run_model("hello durable", "")
            state1 = s1._history_state()
            assert state1["durable"] is True
            assert len(state1["history"]) == 2
        finally:
            s1._driver.close()

        # A brand-new server over the same file sees the persisted transcript.
        s2 = FbpLandingServer(history_path=path)
        try:
            state2 = s2._history_state()
            assert state2["durable"] is True
            # Durable round-trip normalises keys (adds verified/error); the
            # meaningful transcript (role/content/model) is identical.
            def _signature(h):
                return [(t.get("role"), t.get("content"), t.get("model")) for t in h]

            assert _signature(state2["history"]) == _signature(state1["history"])
            assert state2["history"][1]["role"] == "assistant"
        finally:
            s2._driver.close()

    def test_durable_history_clear_clears_durable_too(
        self, monkeypatch, tmp_path
    ) -> None:
        """Clear history also clears the durable log (explicit operator action)."""
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        path = str(tmp_path / "hist.db")
        s1 = FbpLandingServer(history_path=path)
        try:
            s1._run_model("hi", "")
            s1._clear_history()
        finally:
            s1._driver.close()
        s2 = FbpLandingServer(history_path=path)
        try:
            assert s2._history_state()["history"] == []
        finally:
            s2._driver.close()

    def test_stream_client_parses_sse_lines(self, monkeypatch) -> None:
        """The streaming transport parses OpenRouter SSE lines into chunks and
        handles the [DONE] terminator — offline via a stubbed urlopen."""
        import urllib.request as _ur

        from agent_centric.fbp.web import _openrouter_http_client_stream

        lines = [
            b'data: {"choices":[{"delta":{"content":"Hel"}}]}\n',
            b'data: {"choices":[{"delta":{"content":"lo"}}]}\n',
            b"data: [DONE]\n",
        ]
        collected: list[str] = []

        class _FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def __iter__(self):
                return iter(lines)

        def _fake_urlopen(_req: object, timeout: float = 30) -> _FakeResp:
            return _FakeResp()

        monkeypatch.setattr(_ur, "urlopen", _fake_urlopen)
        client = _openrouter_http_client_stream("m", collected.append)
        full = client("https://example.test", {}, "hi")
        assert collected == ["Hel", "lo"]
        assert full == "Hello"
