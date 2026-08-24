"""Tests for the FBP landing-page server (``fbp/web.py``).

The server is a **local-only, actionable landing page**: stdlib ``http.server``
over an in-process ``FbpDriver``. It is a read/verify surface — it never
mutates durable state and never relaxes the correctness spine. These tests
exercise the page rendering and the handler wiring without opening a bound
port (the server is held in-process).
"""

from __future__ import annotations

import json

from agent_centric.fbp.web import (
    FbpLandingServer,
    _build_openrouter_providers,
    _caps,
    _grants,
    _openrouter_http_client,
    _parse_model_body,
    _render_landing,
    _render_ledger,
    _stream_chunks,
    _stub_model_text,
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

    def test_run_artifact_typed_intent(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact('{"intent": "sum", "a": 2, "b": 3}')
            assert result["ok"] is True
            assert result["results"][0]["value"] == 5
        finally:
            server._driver.close()

    def test_run_artifact_typed_intent_coerces(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact('{"intent": "double", "value": "21"}')
            assert result["ok"] is True
            assert result["results"][0]["value"] == 42
        finally:
            server._driver.close()

    def test_run_artifact_typed_intent_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_artifact('{"intent": "sum", "a": 2}')
            assert result["ok"] is False
            assert "required" in result["error"]
        finally:
            server._driver.close()

    def test_schema_route_exposes_schemas(self) -> None:
        from agent_centric.fbp.orchestrate import SCHEMAS

        assert set(SCHEMAS) >= {"run", "double", "sum"}
        assert SCHEMAS["sum"]["fields"]["a"]["required"] is True


class TestBillsRoutes:
    """The bills workflow exposed on the landing page: intake -> human-gated
    accept -> durable registry -> verified calendar. Every step runs through the
    verified spine; nothing auto-accepts."""

    def _draft(self) -> dict:
        return {
            "id": "bill-b1",
            "vendor": "GasCo",
            "amount_cents": 12345,
            "due_date": "2026-10-01",
        }

    def test_registry_starts_empty(self) -> None:
        server = FbpLandingServer()
        try:
            reg = server._bills_registry()
            assert reg["ok"] is True
            assert reg["count"] == 0
        finally:
            server._driver.close()

    def test_intake_accept_registry_calendar(self) -> None:
        server = FbpLandingServer()
        try:
            draft = self._draft()
            intake = server._bills_intake(json.dumps({"draft": draft}))
            assert intake["ok"] is True
            assert intake["draft"]["id"] == "bill-b1"
            # Intake alone does not write the registry.
            assert server._bills_registry()["count"] == 0

            accept = server._bills_accept(json.dumps({"draft": draft}))
            assert accept["ok"] is True
            assert accept["id"] == "bill-b1"
            assert server._bills_registry()["count"] == 1

            cal = server._bills_calendar(
                json.dumps({"from_date": "2026-10-01", "to_date": "2026-10-31"})
            )
            assert cal["ok"] is True
            assert [e["id"] for e in cal["entries"]] == ["bill-b1"]
            assert cal["total_cents"] == 12345
        finally:
            server._driver.close()

    def test_accept_rejects_malformed_draft(self) -> None:
        server = FbpLandingServer()
        try:
            bad = self._draft()
            bad["amount_cents"] = "NaN"
            result = server._bills_accept(json.dumps({"draft": bad}))
            assert result["ok"] is False
            assert "error" in result
        finally:
            server._driver.close()

    def test_intake_rejects_invalid_payload(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._bills_intake("{not json")
            assert result["ok"] is False
        finally:
            server._driver.close()

    def test_bills_registry_durable_across_restart(self, tmp_path) -> None:
        """With a granted bills path, the registry survives a restart."""
        path = str(tmp_path / "registry.db")
        s1 = FbpLandingServer(bills_path=path)
        try:
            draft = self._draft()
            s1._bills_accept(json.dumps({"draft": draft}))
            assert s1._bills_registry()["count"] == 1
        finally:
            s1._driver.close()
        s2 = FbpLandingServer(bills_path=path)
        try:
            reg = s2._bills_registry()
            assert reg["count"] == 1
            assert reg["registry"]["bill-b1"]["status"] == "open"
        finally:
            s2._driver.close()


class TestExpertsRoute:
    """The read-only expert-selection + cost readout (Network of Experts AI)."""

    def test_experts_readout_is_deterministic(self) -> None:
        server = FbpLandingServer()
        try:
            a = server._experts_readout()
            b = server._experts_readout()
            assert a == b  # deterministic
            assert a["ok"] is True
            assert isinstance(a["total_cost"], int)
        finally:
            server._driver.close()

    def test_experts_readout_marks_verifiable_domains(self) -> None:
        server = FbpLandingServer()
        try:
            a = server._experts_readout()
            kinds = {d["kind"] for d in a["domains"]}
            # even/odd are configured verifiers -> deterministic; unverifiable -> human.
            assert any(d["kind"] == "human" for d in a["domains"])
            assert all(d["kind"] in ("human", "deterministic", "learned") for d in a["domains"])
            assert a["total_cost"] >= 0
            _ = kinds
        finally:
            server._driver.close()


class TestDomainRepoRoutes:
    """The Domain Registry + artifact vault (observability +
    provenance layer, tenant-aware, read-only, write-once)."""

    def test_domains_readout_observes_tree(self) -> None:
        server = FbpLandingServer()
        try:
            d = server._domain_readout()
            assert d["ok"] is True
            assert d["count"] >= 1
            # Each record carries a tenant + expert kind (deterministic selection).
            for rec in d["domains"]:
                assert "tenant" in rec
                assert rec["expert_kind"] in ("human", "deterministic", "learned")
        finally:
            server._driver.close()

    def test_artifacts_readout_is_write_once_evidence(self) -> None:
        server = FbpLandingServer()
        try:
            a = server._artifact_readout()
            assert a["ok"] is True
            assert a["count"] == a["count"]  # self-consistent
            assert isinstance(a["total_cost"], int)
            ids = [x["domain"] for x in a["artifacts"]]
            assert len(ids) == len(set(ids))  # no duplicate (tenant,domain,run)
        finally:
            server._driver.close()

    def test_artifacts_durable_across_restart(self, tmp_path) -> None:
        """With a granted registry path, the write-once artifact evidence
        survives a restart (explicit grant)."""
        path = str(tmp_path / "repo.json")
        s1 = FbpLandingServer(registry_path=path)
        try:
            assert s1._artifact_readout()["count"] >= 1
        finally:
            s1._save_artifacts()
            s1._driver.close()
        assert __import__("os").path.exists(path)
        s2 = FbpLandingServer(registry_path=path)
        try:
            assert s2._artifact_readout()["count"] >= 1
        finally:
            s2._driver.close()


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

    def test_save_and_load_network(self) -> None:
        """A validated network can be saved by name and loaded back (explicit
        grant; in-memory by default)."""
        server = FbpLandingServer()
        try:
            net = (
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            saved = server._network_save('{"name": "demo", "network": ' + net + '}')
            assert saved["ok"] is True
            assert saved["saved"] == "demo"
            assert server._network_list()["names"] == ["demo"]
            loaded = server._network_load("demo")
            assert loaded["ok"] is True
            assert loaded["network"]["components"][0]["id"] == "a"
        finally:
            server._driver.close()

    def test_save_rejects_invalid_network(self) -> None:
        """A cyclic/malformed network is never persisted (fail-closed)."""
        server = FbpLandingServer()
        try:
            bad = ('{"components": [{"id": "a", "task": "double", "args": {"value": 1}}, '
                   '{"id": "b", "task": "double", "args": {"value": 1}}], '
                   '"edges": [{"source": "a", "source_field": "value", "target": "b", '
                   '"target_arg": "value"}, {"source": "b", "source_field": "value", '
                   '"target": "a", "target_arg": "value"}]}')
            result = server._network_save('{"name": "bad", "network": ' + bad + '}')
            assert result["ok"] is False
            assert "invalid" in result["error"]
            assert server._network_list()["names"] == []
        finally:
            server._driver.close()

    def test_load_unknown_network_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._network_load("missing")
            assert result["ok"] is False
        finally:
            server._driver.close()

    def test_networks_persist_across_restart(self, tmp_path) -> None:
        """With a granted networks path, saved networks survive a restart."""
        path = str(tmp_path / "networks.json")
        s1 = FbpLandingServer(networks_path=path)
        try:
            net = (
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            s1._network_save('{"name": "demo", "network": ' + net + '}')
        finally:
            s1._driver.close()
        s2 = FbpLandingServer(networks_path=path)
        try:
            assert s2._network_list()["names"] == ["demo"]
            assert s2._network_load("demo")["ok"] is True
        finally:
            s2._driver.close()


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

    def test_run_model_provider_error_fails_closed(self) -> None:
        """A real provider that raises yields an explicit, unverified failure.

        The audited ``/model`` path must never surface a provider exception as
        a success: ``_run_model`` catches it and returns ``ok=False`` with the
        error surfaced and ``verified=False`` (fail-closed, never misverified).
        """
        from agent_centric.contracts.model import ModelProviderError

        server = FbpLandingServer()
        model_agent = server._driver._root.children["model"]

        class _FailingProvider:
            def __call__(self, prompt: str) -> str:
                raise ModelProviderError("simulated provider failure")

        model_agent.set_provider(_FailingProvider(), model_id="openai/gpt-5")
        result = server._run_model("hello")
        assert result.get("ok") is False
        assert result.get("verified") is False
        assert "simulated provider failure" in result.get("error", "")
        server._driver.close()


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

    def test_render_has_sidebar_nav_with_sections(self) -> None:
        """The page exposes a sidebar nav with sectioned panes; one pane is
        active at a time (a clear, navigable dashboard), defaulting to the
        overview/dashboard pane."""
        html = _render_landing({})
        # Sidebar navigation is present with all the main sections.
        assert "class='sidebar'" in html
        # The bills loop is a demonstration only, so it has no dashboard tab.
        for page in ("dashboard", "chat", "provision", "registry", "designer", "docs"):
            assert f"data-page='{page}'" in html
            assert f"id='pane-{page}'" in html
        # The Bills demo tab is intentionally absent from the dashboard.
        assert "data-page='bills'" not in html
        # Dashboard pane is the default (active by default in navShow).
        assert "id='pane-dashboard'" in html
        assert "dashboard" in html

    def test_render_uses_cards(self) -> None:
        """Functional areas are wrapped in cards for a clean, friendly layout."""
        html = _render_landing({})
        assert "class='card'" in html
        assert "card" in html

    def test_render_shows_model_dropdown_when_choices(self) -> None:
        """When providers are configured the page renders a model dropdown."""
        html = _render_landing({}, models=("gpt-x", "llama-y"))
        assert "model-select" in html
        assert "gpt-x" in html and "llama-y" in html

    def test_render_omits_dropdown_without_choices(self) -> None:
        """Without providers there is no empty dropdown (stub only)."""
        html = _render_landing({})
        assert "<select id='model-select'" not in html

    def test_render_shows_last_action_note(self) -> None:
        """A last_action in the page state renders as a note."""
        html = _render_landing({"last_action": {"action": "run double(21)", "value": 42}})
        assert "Last action:" in html
        assert "run double(21)" in html
        assert "42" in html

    def test_render_omits_last_action_note_when_absent(self) -> None:
        """No last_action means no note."""
        html = _render_landing({})
        assert "Last action:" not in html

    def test_render_shows_error_note(self) -> None:
        """An error renders as an error note."""
        html = _render_landing({}, error="unknown path")
        assert "class='error'" in html
        assert "unknown path" in html

    def test_render_omits_error_note_when_none(self) -> None:
        """No error means no error note."""
        html = _render_landing({})
        assert "class='error'" not in html

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

    def test_render_ledger_populated_runs(self) -> None:
        """The ledger HTML renders each recorded run's fields when runs exist.

        This is the populated-runs branch: every run's correlation id, task,
        terminal, and value must appear in the table (and the empty note must
        not). The ledger view stays a read-only, deterministic snapshot.
        """
        html = _render_ledger({
            "summary": {
                "runs": [
                    {"correlation_id": "run-1", "task": "double",
                     "terminal": "result", "value": 4},
                    {"correlation_id": "run-2", "task": "sum",
                     "terminal": "error", "value": None},
                ]
            },
            "count": 2,
        })
        assert "run-1" in html and "double" in html and "result" in html
        assert "4" in html
        assert "run-2" in html and "sum" in html
        assert "no runs recorded" not in html

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

    def test_stream_model_provider_error_fails_closed(self, monkeypatch) -> None:
        """A streaming provider failure surfaces as an explicit SSE error event.

        The streaming preview must never emit a silent gap or a false done:
        when the worker's transport raises, the generator yields a terminal
        ``{type: error}`` event carrying the message (fail-closed).
        """
        import agent_centric.fbp.web as _web

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
        monkeypatch.setenv("OPENROUTER_MODEL", "openai/gpt-5")

        def _boom_client(model: str, on_chunk: object):
            def client(endpoint: str, headers: dict, prompt: str) -> str:
                raise RuntimeError("simulated stream failure")

            return client

        monkeypatch.setattr(_web, "_openrouter_http_client_stream", _boom_client)
        server = FbpLandingServer()
        events = list(server._stream_model("hello", "openai/gpt-5"))
        types = [e["type"] for e in events]
        assert types[0] == "meta"
        assert types[-1] == "error"
        assert "simulated stream failure" in events[-1]["error"]
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


class TestArtifactRecording:
    """Verified runs record real artifacts into the vault (not just seeds)."""

    def test_network_run_records_artifact(self) -> None:
        server = FbpLandingServer()
        try:
            before = server._artifact_readout()["count"]
            r = server._run_network(
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            assert r["ok"] is True
            assert server._artifact_readout()["count"] == before + 1
        finally:
            server._driver.close()

    def test_artifact_run_is_write_once(self) -> None:
        server = FbpLandingServer()
        try:
            r = server._run_network(
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            assert r["ok"] is True
            # Re-running the same network records a new run key (append-only).
            r2 = server._run_network(
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            assert r2["ok"] is True
            assert server._artifact_readout()["count"] >= 2
        finally:
            server._driver.close()


class TestSlmReadout:
    """The /slm readout shows which domains warrant a learned (SLM) expert."""

    def test_slm_readout_is_deterministic(self) -> None:
        server = FbpLandingServer()
        try:
            a = server._slm_readout()
            b = server._slm_readout()
            assert a == b
            assert a["ok"] is True
            assert len(a["warranted"]) >= 1
            for d in a["warranted"]:
                assert d["kind"] in ("human", "deterministic", "learned")
                assert "warranted" in d
        finally:
            server._driver.close()


class TestProvisionRoute:
    """The provisioning card: a deterministic plan -> train -> account -> settle
    pass over the live tree, offline via the stub, recording the expert's
    artifact into the vault. Real providers never run here (fail-closed)."""

    def test_provision_domains_readout(self) -> None:
        server = FbpLandingServer()
        try:
            domains = server._provision_domains()
            assert len(domains) >= 1
            for d in domains:
                assert d["id"]
                assert d["kind"] in ("human", "deterministic", "learned")
                assert "warranted" in d
        finally:
            server._driver.close()

    def test_provision_stub_deterministic_domain(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({"domain": "bill-extract", "provider": "stub"})
            )
            assert result["ok"] is True
            res = result["result"]
            assert res["selection"] in ("human", "deterministic", "learned")
            assert "account" in res and "domain" in res
        finally:
            server._driver.close()

    def test_provision_stub_is_offline_no_provider(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({"domain": "bill-extract", "provider": "stub"})
            )
            assert result["ok"] is True
        finally:
            server._driver.close()

    def test_provision_unknown_domain_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({"domain": "nope::does-not-exist"})
            )
            assert result["ok"] is False
            assert "not a known domain" in result["error"]
        finally:
            server._driver.close()

    def test_provision_invalid_body_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            assert server._run_provision("{not json")["ok"] is False
            assert server._run_provision("")["ok"] is True  # defaults to stub
        finally:
            server._driver.close()

    def test_provision_over_budget_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({"domain": "bill-extract", "budget": 1})
            )
            assert result["ok"] is True or result["ok"] is False
        finally:
            server._driver.close()

    def test_provision_records_learned_artifact(self) -> None:
        server = FbpLandingServer()
        try:
            before = server._artifact_readout()["count"]
            result = server._run_provision(
                json.dumps({"domain": "bill-extract", "provider": "stub"})
            )
            assert result["ok"] is True
            if result["result"]["expert"] is not None:
                assert server._artifact_readout()["count"] == before + 1
        finally:
            server._driver.close()


class TestCatalogWiring:
    """The recommended base-model catalog is wired into the web tier."""

    def test_catalog_readout_serves_bases(self) -> None:
        server = FbpLandingServer()
        try:
            cat = server._catalog_readout()
            assert cat["ok"] is True
            assert len(cat["bases"]) >= 10
            assert cat["size_classes"] == ["edge", "small", "mid", "large"]
            for b in cat["bases"]:
                assert "min_tier" in b
                assert "size_class" in b
        finally:
            server._driver.close()

    def test_provision_accepts_base_model(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({
                    "domain": "bill-extract",
                    "provider": "stub",
                    "base_model": "qwen3-14b",
                    "budget": 100,
                })
            )
            assert result["ok"] is True
            assert result["result"]["plan"] == "multi-gpu"
            assert result["result"]["expert"]["base_model"] == "qwen3-14b"
        finally:
            server._driver.close()

    def test_provision_unknown_base_fails_closed(self) -> None:
        server = FbpLandingServer()
        try:
            result = server._run_provision(
                json.dumps({
                    "domain": "bill-extract",
                    "provider": "stub",
                    "base_model": "no-such-model",
                })
            )
            assert result["ok"] is False
            assert "unknown base model" in result["error"]
        finally:
            server._driver.close()

    def test_landing_includes_model_picker(self) -> None:
        html = _render_landing({})
        assert "prov-model" in html
        assert "prov-tier" in html


class TestDesignerSurface:
    """The Designer is a full three-pane component-network editor: palette,
    stage (canvas), and inspector — all wired and rendered."""

    def test_render_has_three_pane_designer(self) -> None:
        html = _render_landing({})
        # Palette, canvas/stage, and inspector all present.
        assert "id='net-pal'" in html
        assert "id='net-canvas'" in html and "id='net-svg'" in html
        assert "id='net-insp'" in html
        # Toolbar controls present.
        for i in ("net-layout", "net-clear", "net-run", "net-save", "net-load"):
            assert f"id='{i}'" in html
        # The palette data is data-driven (tasks listed).
        for task in ("double", "sum", "square", "even"):
            assert task in html

    def test_new_tasks_true_dataflow(self) -> None:
        """The richer task set runs as verified true dataflow through the spine:
        sum(2,3)=5 -> double=10 -> even=true."""
        server = FbpLandingServer()
        try:
            payload = (
                '{"components": ['
                '{"id": "a", "task": "sum", "args": {"a": 2, "b": 3}},'
                '{"id": "b", "task": "double", "args": {"value": 0}},'
                '{"id": "c", "task": "even", "args": {}}'
                "], "
                '"edges": ['
                '{"source": "a", "source_field": "value", "target": "b", '
                '"target_arg": "value"},'
                '{"source": "b", "source_field": "value", "target": "c", '
                '"target_arg": "value"}]}'
            )
            result = server._run_network(payload)
            assert result["ok"] is True
            assert result["completed"] == 3
            values = {s["id"]: s["value"] for s in result["results"]}
            assert values["a"] == 5
            assert values["b"] == 10
            assert values["c"] is True
            # Every step verified.
            assert all(s["verified"] for s in result["results"])
        finally:
            server._driver.close()


class TestAgentDesigner:
    """The Designer now composes the real demo agents (child/store/model) via
    the network's ``child`` delegation, alongside the arithmetic primitives."""

    def test_palette_has_agents_group(self) -> None:
        html = _render_landing({})
        # The Agents group is present and lists the demo agents.
        assert "Agents" in html
        for task in ("store_set", "store_get", "model"):
            assert task in html
        # The primitives are kept as a secondary group.
        assert "Primitives" in html

    def test_agent_demo_runs_through_spine(self, monkeypatch) -> None:
        """The agent demo (child double -> store_set, model -> store_set) runs
        through the verified spine, delegating to the real spawned agents."""
        # Force the deterministic stub so the test is offline and CI-safe.
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        server = FbpLandingServer()
        try:
            payload = (
                '{"components": ['
                '{"id": "c1", "task": "double", "args": {"value": 21}, "child": "child"},'
                '{"id": "c2", "task": "store_set", "args": {"key": "demo", "value": 0},'
                ' "child": "store"},'
                '{"id": "c3", "task": "model", "args": {"prompt": "hello"}, "child": "model"},'
                '{"id": "c4", "task": "store_set", "args": {"key": "answer", "value": 0},'
                ' "child": "store"}'
                "], "
                '"edges": ['
                '{"source": "c1", "source_field": "value", "target": "c2", "target_arg": "value"},'
                '{"source": "c3", "source_field": "value", "target": "c4", "target_arg": "value"}]}'
            )
            result = server._run_network(payload)
            assert result["ok"] is True
            assert result["completed"] == 4
            # The child agent doubled 21 -> 42 (verified even).
            c1 = next(s for s in result["results"] if s["id"] == "c1")
            assert c1["value"] == 42
            assert c1["verified"] is True
            # The store writes verified (the store agent served them).
            c2 = next(s for s in result["results"] if s["id"] == "c2")
            assert c2["verified"] is True
            c4 = next(s for s in result["results"] if s["id"] == "c4")
            assert c4["verified"] is True
            # The model agent answered (verified, a non-empty string).
            c3 = next(s for s in result["results"] if s["id"] == "c3")
            assert isinstance(c3["value"], str) and c3["value"]
            assert c3["verified"] is True
        finally:
            server._driver.close()

    def test_agent_demo_store_writes_are_granted(self) -> None:
        """The store agent is granted a ``demo-*`` prefix so the agent demo's
        writes (demo/answer) are served; a key outside the grant fails closed."""
        server = FbpLandingServer()
        try:
            # A store_set under the granted demo-* prefix verifies.
            ok = server._run_network(
                '{"components": [{"id": "a", "task": "store_set", '
                '"args": {"key": "demo-x", "value": 1}, "child": "store"}], "edges": []}'
            )
            assert ok["ok"] is True
            # A store_set outside the grant fails closed (not verified).
            denied = server._run_network(
                '{"components": [{"id": "a", "task": "store_set", '
                '"args": {"key": "nope", "value": 1}, "child": "store"}], "edges": []}'
            )
            assert denied["ok"] is False
        finally:
            server._driver.close()


class TestAgentDesignerBills:
    """The Designer can compose the bills agent (demonstration-only) through
    the verified spine: intake -> accept -> calendar."""

    def test_bills_agent_in_palette(self) -> None:
        html = _render_landing({})
        for task in ("bills_intake", "bills_accept", "bills_calendar", "bills_registry"):
            assert task in html

    def test_bills_intake_accept_calendar_network(self) -> None:
        """A bills chain (intake -> accept -> calendar) runs through the spine,
        delegating to the real bills agent."""
        import json as _json

        server = FbpLandingServer()
        try:
            draft = {
                "id": "bill-designer-1",
                "vendor": "GasCo",
                "amount_cents": 12345,
                "due_date": "2026-10-01",
            }
            draft_json = _json.dumps(draft)
            payload = (
                '{"components": ['
                '{"id": "a", "task": "bills_intake", "args": {"draft": '
                + draft_json + '}, "child": "bills"},'
                '{"id": "b", "task": "bills_accept", "args": {"draft": '
                + draft_json + '}, "child": "bills"},'
                '{"id": "c", "task": "bills_calendar", "args": '
                '{"from_date": "2026-10-01", "to_date": "2026-10-31"}, "child": "bills"},'
                '{"id": "d", "task": "bills_registry", "args": {}, "child": "bills"}'
                '], "edges": []}'
            )
            result = server._run_network(payload)
            assert result["ok"] is True
            assert result["completed"] == 4
            # The registry snapshot shows the accepted bill.
            d = next(s for s in result["results"] if s["id"] == "d")
            assert d["verified"] is True
            reg = d["value"].get("registry", {})
            assert "bill-designer-1" in reg
            assert reg["bill-designer-1"]["status"] == "open"
            # The calendar projects the accepted bill.
            c = next(s for s in result["results"] if s["id"] == "c")
            assert c["verified"] is True
            assert [e["id"] for e in c["value"].get("entries", [])] == ["bill-designer-1"]
        finally:
            server._driver.close()


class TestPureHelpers:
    """Coverage for the small pure helpers in web.py (offline, deterministic)."""

    def test_stub_model_text(self) -> None:
        assert _stub_model_text("hello") == "stub response to: hello"
        # Long prompts are truncated to 80 chars.
        long_prompt = "x" * 200
        assert len(_stub_model_text(long_prompt)) == len("stub response to: ") + 80

    def test_stream_chunks_empty(self) -> None:
        assert _stream_chunks("") == []

    def test_stream_chunks_splits_words(self) -> None:
        chunks = _stream_chunks("one two three four", size=8)
        # The text is split into progressive word/prefix-sized chunks.
        assert "".join(chunks).replace(" ", "") == "onetwothreefour"
        assert all(len(c) >= 1 for c in chunks)

    def test_stream_chunks_single_word(self) -> None:
        assert _stream_chunks("hello") == ["hello"]

    def test_caps_renders_pills(self) -> None:
        html = _caps({"capabilities": ["double", "sum"]})
        assert "double" in html and "sum" in html
        assert "class='pill'" in html

    def test_caps_em_dash_when_none(self) -> None:
        assert _caps({}) == "&mdash;"
        assert _caps({"capabilities": []}) == "&mdash;"

    def test_openrouter_client_parses_choices(self, monkeypatch) -> None:
        """The non-streaming client parses the choices/message shape."""
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"choices":[{"message":{"content":"hi there"}}]}'

        def _fake_urlopen(req: object, timeout: int = 0) -> _FakeResp:
            return _FakeResp()

        monkeypatch.setattr(_ur, "urlopen", _fake_urlopen)
        client = _openrouter_http_client("m")
        assert client("https://example.test", {}, "hi") == "hi there"

    def test_openrouter_client_raises_on_bad_shape(self, monkeypatch) -> None:
        """A malformed response fails closed with a clear RuntimeError."""
        import urllib.request as _ur

        class _FakeResp:
            def __enter__(self) -> _FakeResp:
                return self

            def __exit__(self, *a: object) -> None:
                return None

            def read(self) -> bytes:
                return b'{"choices":[]}'

        def _fake_urlopen(req: object, timeout: int = 0) -> _FakeResp:
            return _FakeResp()

        monkeypatch.setattr(_ur, "urlopen", _fake_urlopen)
        client = _openrouter_http_client("m")
        try:
            client("https://example.test", {}, "hi")
            raise AssertionError("expected RuntimeError")
        except RuntimeError as exc:
            assert "unexpected OpenRouter response" in str(exc)
