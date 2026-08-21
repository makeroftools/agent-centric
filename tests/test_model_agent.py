"""Tests for the first model-mediated agent (Volley 012).

These tests prove that a language-model call is governed by the same invariants
as every other action: it is mediated through a Manager-owned tool, only usable
when explicitly granted, bounded by resource envelopes, subject to policy, and
never sufficient on its own for a verified result. All tests use the
deterministic stub provider — no network access or API key is ever required.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.contracts.model import ModelProviderError
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.tools import ToolExecutionError, ToolRegistry
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from agent_centric.providers import (
    FailingStubModelProvider,
    OptionalRealModelProvider,
    StubModelProvider,
    build_real_model_provider,
    redact_secrets,
)
from tests.conftest import MODEL_CAPABILITY, MODEL_MANIFEST


def _model_task(
    task_id: str,
    prompt: str,
    expected: str,
    *,
    granted: tuple[str, ...] = ("llm_complete",),
    envelope: ResourceEnvelope | None = None,
    policy: Policy | None = None,
) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="model",
        payload={"prompt": prompt, "expected": expected},
        envelope=envelope or ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=granted,
        policy=policy,
    )


def _manager_with_stub(
    responses: dict[str, str] | None = None,
    default_text: str = "stub response",
) -> tuple[AgentManager, StubModelProvider]:
    provider = StubModelProvider(responses=responses, default_text=default_text)
    m = AgentManager(tools=ToolRegistry(model_provider=provider))
    m.register(MODEL_MANIFEST)
    return m, provider


class TestModelProviderContract:
    def test_stub_provider_is_deterministic(self) -> None:
        p = StubModelProvider(responses={"hi": "hello"})
        assert p("hi") == p("hi")
        assert p("hi").text == "hello"
        assert p("other").text == "stub response"

    def test_stub_records_calls(self) -> None:
        p = StubModelProvider()
        p("one")
        p("two")
        assert p.calls == ["one", "two"]

    def test_failing_provider_raises_explicit_error(self) -> None:
        p = FailingStubModelProvider()
        with pytest.raises(ModelProviderError):
            p("anything")

    def test_optional_real_provider_adapter(self) -> None:
        p = OptionalRealModelProvider(lambda prompt: f"echo:{prompt}", enabled=True)
        assert p("x").text == "echo:x"

    def test_optional_real_provider_rejects_non_string(self) -> None:
        p = OptionalRealModelProvider(lambda prompt: 42, enabled=True)  # type: ignore[arg-type]
        with pytest.raises(ModelProviderError):
            p("x")

    def test_real_provider_disabled_by_default(self) -> None:
        """A real provider is disabled by default and fails closed on call."""
        p = OptionalRealModelProvider(lambda prompt: f"echo:{prompt}")  # no opt-in
        with pytest.raises(ModelProviderError):
            p("x")

    def test_real_provider_missing_endpoint_fails_closed(self) -> None:
        """Requesting a real provider without an endpoint is a clear error."""
        with pytest.raises(ModelProviderError, match="endpoint"):
            build_real_model_provider(endpoint=None, api_key="sk-test")

    def test_real_provider_missing_credentials_fails_closed(self) -> None:
        """Requesting a real provider without credentials is a clear error."""
        with pytest.raises(ModelProviderError, match="credentials"):
            build_real_model_provider(
                endpoint="https://example.test", http_client=lambda e, h, p: p
            )

    def test_real_provider_no_http_client_fails_closed_on_call(self) -> None:
        """A provider built without a transport never reaches the network."""
        p = build_real_model_provider(
            endpoint="https://example.test", api_key="sk-secret"
        )
        with pytest.raises(ModelProviderError, match="No HTTP transport"):
            p("hi")

    def test_real_provider_http_error_maps_fail_closed(self) -> None:
        """An HTTP/API error is mapped to an explicit ModelProviderError."""

        def http_client(
            endpoint: str, headers: dict[str, str], payload: str
        ) -> str:
            raise RuntimeError("upstream 503")

        p = build_real_model_provider(
            endpoint="https://example.test",
            api_key="sk-secret",
            http_client=http_client,
        )
        with pytest.raises(ModelProviderError, match="upstream 503"):
            p("hi")

    def test_real_provider_timeout_is_fail_closed(self) -> None:
        """A slow real provider is bounded and fails closed on timeout."""
        import time

        def slow(_prompt: str) -> str:
            time.sleep(0.5)
            return "late"

        p = OptionalRealModelProvider(slow, enabled=True, timeout_seconds=0.05)
        with pytest.raises(ModelProviderError, match="timed out"):
            p("hi")

    def test_real_provider_redacts_secrets_from_errors(self) -> None:
        """Secrets are redacted from provider error messages."""

        def http_client(
            endpoint: str, headers: dict[str, str], payload: str
        ) -> str:
            raise RuntimeError("request failed with key sk-supersecret")

        p = build_real_model_provider(
            endpoint="https://example.test",
            api_key="sk-supersecret",
            http_client=http_client,
        )
        with pytest.raises(ModelProviderError) as excinfo:
            p("hi")
        assert "sk-supersecret" not in str(excinfo.value)
        assert "[REDACTED]" in str(excinfo.value)

    def test_redact_secrets_scrubs_provided_values(self) -> None:
        assert redact_secrets("key=abc123", ("abc123",)) == "key=[REDACTED]"


class TestModelToolRegistry:
    def test_llm_tool_not_registered_without_provider(self) -> None:
        reg = ToolRegistry()
        assert reg.descriptor("llm_complete") is None
        with pytest.raises(ToolExecutionError):
            reg.execute("llm_complete", {"prompt": "hi"})

    def test_llm_tool_registered_with_provider(self) -> None:
        reg = ToolRegistry(model_provider=StubModelProvider())
        desc = reg.descriptor("llm_complete")
        assert desc is not None
        assert desc.name == "llm_complete"
        assert reg.execute("llm_complete", {"prompt": "hi"}) == "stub response"


class TestModelToolGrant:
    def test_granted_model_tool_succeeds_and_verifies(self) -> None:
        m, provider = _manager_with_stub(responses={"hi": "hello"})
        outcome = m.run(_model_task("granted", "hi", "hello"))
        assert outcome.result is not None
        assert outcome.result.output == "hello"
        assert provider.calls == ["hi"]

    def test_ungranted_model_tool_cannot_be_used(self) -> None:
        """An agent cannot call the model without an explicit grant."""
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        outcome = m.run(_model_task("ungranted", "hi", "hello", granted=()))
        # Without the model the agent returns UNVERIFIED, which fails the gate.
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_granting_a_different_tool_does_not_grant_llm(self) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        outcome = m.run(_model_task("wrong-grant", "hi", "hello", granted=("add",)))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED


class TestModelToolRecording:
    def test_request_and_response_recorded_in_trajectory(self, tmp_path: Path) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        outcome = m.run(_model_task("traj", "hi", "hello"))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("llm_complete' request" in d for d in descriptions)
        assert any("llm_complete' result" in d for d in descriptions)
        result_steps = [s for s in stored.steps if "llm_complete' result" in s.description]
        assert result_steps and result_steps[0].output == "hello"

    def test_ungranted_model_request_rejection_is_recorded(self, tmp_path: Path) -> None:
        """The Manager rejects and records an ungranted llm_complete request."""
        from tests.fake_agent import UNGUARDED_MODEL_AGENT_MANIFEST

        m = AgentManager(tools=ToolRegistry(model_provider=StubModelProvider()))
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        m.register(UNGUARDED_MODEL_AGENT_MANIFEST)
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="reject",
            agent_name="unguarded_model",
            payload="hi",  # the unguarded agent passes this to llm_complete
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),  # llm_complete NOT granted
        )
        outcome = m.run(task)
        # The agent yields the rejected ToolResult then returns the passthrough,
        # which verifies, so the run succeeds with a recorded rejection step.
        assert outcome.result is not None
        assert outcome.trajectory_id is not None

        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        rejected = [s for s in stored.steps if s.status.value == "rejected"]
        assert rejected, "expected a REJECTED step for the ungranted model request"
        assert "not granted" in (rejected[0].error or "")


class TestModelEnvelope:
    def test_envelope_exhaustion_cancels_model_agent(self) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        # max_steps=1: the agent yields a validation step, then a tool request.
        # The tool interaction consumes additional steps, so the step limit fires.
        outcome = m.run(
            _model_task(
                "budget",
                "hi",
                "hello",
                envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
            )
        )
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT


class TestModelPolicy:
    def test_policy_can_deny_the_model_tool(self, tmp_path: Path) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        policy = Policy(
            version=PolicyVersion.V1,
            deny_tools=frozenset({"llm_complete"}),
        )
        outcome = m.run(_model_task("deny-tool", "hi", "hello", policy=policy))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        assert "llm_complete" in outcome.failure.message

        stored = m.load(outcome.trajectory_id or "")
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any(d == "policy rejected" for d in descriptions)

    def test_policy_can_deny_the_model_agent(self) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        policy = Policy(
            version=PolicyVersion.V1,
            deny_agents=frozenset({"model"}),
        )
        outcome = m.run(_model_task("deny-agent", "hi", "hello", policy=policy))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION

    def test_policy_can_deny_the_model_capability(self) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        policy = Policy(
            version=PolicyVersion.V1,
            deny_capabilities=frozenset({MODEL_CAPABILITY}),
        )
        # Select the model agent by its declared capability.
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="deny-cap",
            capability=MODEL_CAPABILITY,
            payload={"prompt": "hi", "expected": "hello"},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("llm_complete",),
            policy=policy,
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestModelVerification:
    def test_verification_failure_after_model_use_fails_closed(self) -> None:
        """Model output alone is never a verified result."""
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        # The model returns "hello" but the expected answer is "goodbye".
        outcome = m.run(_model_task("mismatch", "hi", "goodbye"))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_provider_failure_is_explicit_and_fails_closed(self) -> None:
        m = AgentManager(tools=ToolRegistry(model_provider=FailingStubModelProvider()))
        m.register(MODEL_MANIFEST)
        outcome = m.run(_model_task("provider-fail", "hi", "hello"))
        # The provider raises, the agent receives a failed ToolResult and returns
        # UNVERIFIED, which fails verification -> explicit failure.
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED


class TestModelDeterminism:
    def test_deterministic_under_stub_provider(self, tmp_path: Path) -> None:
        m, _ = _manager_with_stub(responses={"hi": "hello"})
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        first = m.run(_model_task("det", "hi", "hello"))
        second = m.run(_model_task("det", "hi", "hello"))
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output == "hello"

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            return [
                (s.step_index, s.status.value, s.description, s.output)
                for s in outcome.result.trajectory.steps
            ]

        assert sig(first) == sig(second)