"""Tests for Volley 024 — Read-Only Email Tool + Agent.

These tests prove the read-only email specialty agent is governed by the same
invariants as every other agent: mediated tools (grant, policy, envelope,
recording, verification), fail-closed rejection of invalid/failed operations,
a real IMAP path that is optional and off by default, and secrets redacted from
any error path. No network is ever touched when using the fake gateway; the
real backend is only exercised with a fail-closed stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from agent_centric.contracts.capability import Capability
from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from agent_centric.contracts.policy import Policy, PolicyVersion
from agent_centric.contracts.result import FailureReason
from agent_centric.contracts.task import ResourceEnvelope, TaskSpecification, TaskSpecVersion
from agent_centric.control_plane.email_tools import EmailTools, email_tool_impls
from agent_centric.control_plane.manager import AgentManager
from agent_centric.control_plane.tools import (
    EMAIL_FETCH_DESCRIPTOR,
    EMAIL_LIST_DESCRIPTOR,
    ToolExecutionError,
    ToolRegistry,
)
from agent_centric.control_plane.trajectory_store import FileTrajectoryStore
from agent_centric.control_plane.verifier import verify_email_output
from agent_centric.providers.email import (
    EmailGatewayError,
    FakeEmailGateway,
    OptionalRealEmailGateway,
    build_optional_email_gateway,
)

EMAIL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="email",
    entry_point="agent_centric.agents.email:create_email_agent",
    description="Performs a read-only email operation via mediated tools.",
    declared_capabilities=frozenset({Capability(name="email.read", version="1")}),
)

MAILBOX = {
    "INBOX": (
        {
            "id": "m1",
            "subject": "Hello",
            "from": "a@example.test",
            "date": "2026-08-01",
            "body": "first",
        },
        {
            "id": "m2",
            "subject": "Hi",
            "from": "b@example.test",
            "date": "2026-08-02",
            "body": "second",
        },
        {
            "id": "m3",
            "subject": "Re: Hi",
            "from": "a@example.test",
            "date": "2026-08-03",
            "body": "third",
        },
    ),
    "Archive": (
        {
            "id": "a1",
            "subject": "Old",
            "from": "c@example.test",
            "date": "2026-07-01",
            "body": "old",
        },
    ),
}


def _email_manager(
    mailbox: dict[str, Any] | None = None,
    *,
    default_folders: tuple[str, ...] = ("INBOX",),
    max_list_limit: int = 50,
) -> AgentManager:
    tools = ToolRegistry()
    gateway = FakeEmailGateway(mailbox=mailbox if mailbox is not None else MAILBOX)
    email = EmailTools(gateway, default_folders=default_folders, max_list_limit=max_list_limit)
    for name, impl in email_tool_impls(email).items():
        tools.register_impl(email_tool_descriptor(name), impl)
    m = AgentManager(tools=tools)
    m.register(EMAIL_MANIFEST)
    return m


def email_tool_descriptor(name: str):
    if name == "email_list":
        return EMAIL_LIST_DESCRIPTOR
    if name == "email_fetch":
        return EMAIL_FETCH_DESCRIPTOR
    raise AssertionError(f"unknown email tool {name!r}")


def _list_task(task_id: str, folder: str = "INBOX", limit: int = 5) -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="email",
        payload={"operation": "list", "folder": folder, "limit": limit},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=("email_list",),
    )


def _fetch_task(task_id: str, folder: str = "INBOX", message_id: str = "m1") -> TaskSpecification:
    return TaskSpecification(
        version=TaskSpecVersion.V5,
        task_id=task_id,
        agent_name="email",
        payload={"operation": "fetch", "folder": folder, "message_id": message_id},
        envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
        granted_tools=("email_fetch",),
    )


class TestFakeGateway:
    def test_list_success(self) -> None:
        gateway = FakeEmailGateway(mailbox=MAILBOX)
        result = gateway.list_messages("INBOX", 2)
        assert result.folder == "INBOX"
        assert result.limit == 2
        assert len(result.messages) == 2
        assert [m.subject for m in result.messages] == ["Hello", "Hi"]

    def test_fetch_success(self) -> None:
        gateway = FakeEmailGateway(mailbox=MAILBOX)
        msg = gateway.fetch_message("INBOX", "m2")
        assert msg.id == "m2"
        assert msg.body == "second"

    def test_list_bounded_by_limit(self) -> None:
        gateway = FakeEmailGateway(mailbox=MAILBOX)
        result = gateway.list_messages("INBOX", 2)
        # Even with more messages available, only `limit` are returned.
        assert len(result.messages) == 2

    def test_unknown_folder_fails_closed(self) -> None:
        gateway = FakeEmailGateway(mailbox=MAILBOX)
        with pytest.raises(EmailGatewayError, match="Unknown folder"):
            gateway.list_messages("Nope", 5)
        with pytest.raises(EmailGatewayError, match="Unknown folder"):
            gateway.fetch_message("Nope", "m1")

    def test_unknown_message_fails_closed(self) -> None:
        gateway = FakeEmailGateway(mailbox=MAILBOX)
        with pytest.raises(EmailGatewayError, match="Unknown message id"):
            gateway.fetch_message("INBOX", "missing")


class TestEmailTools:
    def test_list_bounded_by_max(self) -> None:
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX), max_list_limit=2)
        out = email.email_list(folder="INBOX", limit=10)
        assert out["folder"] == "INBOX"
        assert len(out["messages"]) == 2  # capped by max_list_limit

    def test_list_folders_allowed_in_config(self) -> None:
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX), default_folders=("INBOX",))
        with pytest.raises(ToolExecutionError, match="not allowed"):
            email.email_list(folder="Archive", limit=5)

    def test_fetch_requires_message_id(self) -> None:
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX))
        with pytest.raises(ToolExecutionError, match="message_id"):
            email.email_fetch(folder="INBOX")

    def test_list_requires_valid_limit(self) -> None:
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX))
        with pytest.raises(ToolExecutionError, match="limit"):
            email.email_list(folder="INBOX", limit=0)

    def test_fetch_requires_valid_folder(self) -> None:
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX))
        with pytest.raises(ToolExecutionError, match="folder"):
            email.email_fetch(folder="", message_id="m1")


class TestEmailAgent:
    def test_list_verifies(self) -> None:
        m = _email_manager()
        outcome = m.run(_list_task("list"))
        assert outcome.result is not None
        assert outcome.result.output["count"] == 3

    def test_fetch_verifies(self) -> None:
        m = _email_manager()
        outcome = m.run(_fetch_task("fetch", message_id="m2"))
        assert outcome.result is not None
        assert outcome.result.output["body"] == "second"

    def test_ungranted_tool_fails_closed(self) -> None:
        m = _email_manager()
        # email_list is NOT granted, so the agent cannot list and returns None,
        # which fails the verification gate (never an unverified success).
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="list-ungranted",
            agent_name="email",
            payload={"operation": "list", "folder": "INBOX", "limit": 5},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=(),  # email_list NOT granted
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_disallowed_folder_fails_closed(self) -> None:
        m = _email_manager(default_folders=("INBOX",))
        outcome = m.run(_list_task("disallowed", folder="Archive"))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_unknown_message_fails_closed(self) -> None:
        m = _email_manager()
        outcome = m.run(_fetch_task("badid", message_id="nope"))
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.VERIFICATION_FAILED

    def test_bad_payload_fails_closed(self) -> None:
        m = _email_manager()
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="badop",
            agent_name="email",
            payload={"operation": "unsupported"},  # unsupported operation
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=100),
            granted_tools=("email_list",),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.AGENT_ERROR

    def test_verifier_rejects_bad_output(self) -> None:
        task = _list_task("v", folder="INBOX", limit=5)
        good = {
            "folder": "INBOX",
            "limit": 5,
            "count": 1,
            "messages": [
                {
                    "id": "m1",
                    "folder": "INBOX",
                    "subject": "Hello",
                    "from_address": "a@x.test",
                    "date": "2026-08-01",
                }
            ],
        }
        assert verify_email_output(task, good).passed
        # Wrong folder.
        bad = dict(good, folder="Other")
        assert verify_email_output(task, bad).passed is False
        # Not a mapping.
        assert verify_email_output(task, None).passed is False


class TestEmailPolicy:
    def test_policy_can_deny_the_email_tool(self) -> None:
        m = _email_manager()
        policy = Policy(version=PolicyVersion.V1, deny_tools=frozenset({"email_list"}))
        task = _list_task("deny-tool")
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id=task.task_id,
            agent_name=task.agent_name,
            payload=task.payload,
            envelope=task.envelope,
            granted_tools=task.granted_tools,
            policy=policy,
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION
        assert "email_list" in outcome.failure.message

    def test_policy_can_deny_the_email_agent(self) -> None:
        m = _email_manager()
        policy = Policy(version=PolicyVersion.V1, deny_agents=frozenset({"email"}))
        task = _list_task("deny-agent")
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id=task.task_id,
            agent_name=task.agent_name,
            payload=task.payload,
            envelope=task.envelope,
            granted_tools=task.granted_tools,
            policy=policy,
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.POLICY_VIOLATION


class TestEmailEnvelope:
    def test_step_limit_fails_closed(self) -> None:
        m = _email_manager()
        task = TaskSpecification(
            version=TaskSpecVersion.V5,
            task_id="budget",
            agent_name="email",
            payload={"operation": "list", "folder": "INBOX", "limit": 5},
            envelope=ResourceEnvelope(timeout_seconds=10.0, max_steps=1),
            granted_tools=("email_list",),
        )
        outcome = m.run(task)
        assert outcome.result is None
        assert outcome.failure is not None
        assert outcome.failure.reason is FailureReason.STEP_LIMIT


class TestEmailDeterminism:
    def test_deterministic_and_replayable(self, tmp_path: Path) -> None:
        m = _email_manager()
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        first = m.run(_list_task("det"))
        second = m.run(_list_task("det"))
        assert first.result is not None and second.result is not None
        assert first.result.output == second.result.output

        def sig(outcome) -> list[tuple[int, str, str, Any]]:
            return [
                (s.step_index, s.status.value, s.description, s.output)
                for s in outcome.result.trajectory.steps
            ]

        assert sig(first) == sig(second)


class TestEmailTrajectory:
    def test_tool_interaction_recorded(self, tmp_path: Path) -> None:
        m = _email_manager()
        m._store = FileTrajectoryStore(tmp_path)  # type: ignore[attr-defined]
        outcome = m.run(_fetch_task("traj", message_id="m1"))
        assert outcome.result is not None
        assert outcome.trajectory_id is not None
        stored = m.load(outcome.trajectory_id)
        assert stored is not None
        descriptions = [s.description for s in stored.steps]
        assert any("email_fetch' request" in d for d in descriptions)
        assert any("email_fetch' result" in d for d in descriptions)


class TestEmailRealGatewayOptIn:
    def test_missing_credentials_fails_closed(self) -> None:
        from agent_centric.providers import build_optional_email_gateway

        with pytest.raises(EmailGatewayError, match="missing"):
            build_optional_email_gateway(host=None, user=None, password=None)

    def test_disabled_by_default_fails_closed(self) -> None:
        gateway = build_optional_email_gateway(
            host="mailbox.example.test", user="u", password="pw"
        )
        with pytest.raises(EmailGatewayError, match="not enabled"):
            gateway.list_messages("INBOX", 5)

    def test_secrets_redacted_from_error(self) -> None:
        class BadClient:
            def __init__(self, host: str, user: str, password: str) -> None:
                self._host, self._user, self._password = host, user, password

            def list_messages(self, folder: str, limit: int) -> object:
                raise RuntimeError(
                    f"list failed for user {self._user} pw {self._password} "
                    f"host {self._host}"
                )

        gateway = OptionalRealEmailGateway(
            imap_client=BadClient,
            host="server.example.test",
            user="alice",
            password="supersecret",
            enabled=True,
            secret_values=("alice", "supersecret", "server.example.test"),
        )
        with pytest.raises(EmailGatewayError) as excinfo:
            gateway.list_messages("INBOX", 5)
        text = str(excinfo.value)
        assert "supersecret" not in text
        assert "alice" not in text
        assert "server.example.test" not in text
        assert "[REDACTED]" in text


class TestEmailSubprocess:
    def test_runs_under_subprocess_backend(self, tmp_path: Path) -> None:
        from agent_centric.control_plane.execution import SubprocessBackend

        tools = ToolRegistry()
        email = EmailTools(FakeEmailGateway(mailbox=MAILBOX), default_folders=("INBOX",))
        for name, impl in email_tool_impls(email).items():
            tools.register_impl(email_tool_descriptor(name), impl)
        m = AgentManager(tools=tools, backend=SubprocessBackend())
        m.register(EMAIL_MANIFEST)
        outcome = m.run(_fetch_task("sub", message_id="m1"))
        assert outcome.result is not None
        assert outcome.result.output["body"] == "first"