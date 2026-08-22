"""Tests for the ModelAgent — an LLM as an ordinary, first-class agent.

The model agent is delegated to via the normal run directive; its output is
re-verified by the parent's verifier (correctness spine), audited, and carries
source references (the model id). Deterministic by default (stub provider);
a real provider is an opt-in hook that never relaxes verification.
"""

from __future__ import annotations

from agent_centric.fbp import FbpDriver
from agent_centric.fbp.model_agent import TASK_MODEL


class TestModelAgent:
    def test_spawn_and_run_model(self) -> None:
        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            r = driver.run(TASK_MODEL, {"prompt": "summarize that"}, child="model")
            assert r.verified is True
            assert r.node == "model"
            assert isinstance(r.value, str)
            assert "summarize that" in r.value  # deterministic stub echo
            # The model id is attached as a source reference.
            assert r.sources is not None
            assert r.sources[0]["kind"] == "model"
            assert r.sources[0]["id"] == "stub-model"

    def test_model_is_an_ordinary_child_reverified_by_parent(self) -> None:
        """The parent re-verifies the model's value; a failing verifier demotes
        it to an audited failure (not trusted on its own word)."""
        from agent_centric.fbp import register_callable

        def _reject(_v) -> bool:
            return False

        register_callable("no_verify", _reject)
        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.register("no_verify", _reject)
            driver.configure(verifiers=("no_verify",), verifier="no_verify")
            # The parent re-verifies the model's response; it fails -> audited error.
            r = driver.run(TASK_MODEL, {"prompt": "x"}, child="model")
            assert r.verified is False
            assert r.error is not None

    def test_absent_prompt_fails_closed(self) -> None:
        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            r = driver.run(TASK_MODEL, {}, child="model")
            assert r.verified is False
            assert r.error is not None

    def test_model_run_is_replayable(self, tmp_path) -> None:
        ledger = tmp_path / "ledger.db"
        with FbpDriver(ledger_path=str(ledger)) as driver:
            driver.spawn("model", kind="model")
            r = driver.run(TASK_MODEL, {"prompt": "hi there"}, child="model")
            assert r.verified is True
            result = driver.replay_session()
            assert result["ok"] is True, result["failed"]

    def test_configure_provider_wires_callable(self) -> None:
        """A plain callable provider (e.g. the hardened OptionalRealModelProvider)
        can be attached via the driver surface, and its output is used."""

        def _backend(prompt: str, **kwargs):
            return f"REAL[{prompt}]".upper()

        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            resp = driver.configure_provider("model", _backend)
            assert resp.verified is True
            r = driver.run(TASK_MODEL, {"prompt": "hi"}, child="model")
            assert r.verified is True
            assert r.value == "REAL[HI]"

    def test_configure_provider_wires_complete_method(self) -> None:
        """A provider object with a ``.complete`` method works too."""

        class MyBackend:
            def complete(self, prompt: str, **kwargs):
                return f"complete:{prompt}"

        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            assert driver.configure_provider("model", MyBackend()).verified is True
            r = driver.run(TASK_MODEL, {"prompt": "yo"}, child="model")
            assert r.verified is True
            assert r.value == "complete:yo"

    def test_configure_provider_keeps_verification_spine(self) -> None:
        """A real provider output is still re-verified by the parent; a failing
        verifier demotes it (a real provider never relaxes the spine)."""
        from agent_centric.fbp import register_callable

        def _reject(_v):
            return False

        register_callable("no_verify2", _reject)
        with FbpDriver() as driver:
            driver.spawn("model", kind="model")
            driver.register("no_verify2", _reject)
            driver.configure(verifiers=("no_verify2",), verifier="no_verify2")
            driver.configure_provider(
                "model", lambda prompt: "model-output"
            )
            r = driver.run(TASK_MODEL, {"prompt": "x"}, child="model")
            assert r.verified is False
            assert r.error is not None

    def test_configure_provider_fails_closed_on_unknown_child(self) -> None:
        with FbpDriver() as driver:
            resp = driver.configure_provider("ghost", lambda p: "x")
            assert resp.verified is False
            assert "no spawned child" in (resp.error or "")

    def test_configure_provider_fails_closed_on_non_model(self) -> None:
        with FbpDriver() as driver:
            driver.spawn("store", kind="store")
            resp = driver.configure_provider("store", lambda p: "x")
            assert resp.verified is False
            assert "not a model agent" in (resp.error or "")
