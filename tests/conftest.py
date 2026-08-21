"""Shared fixtures for Agent-centric tests."""

from __future__ import annotations

import pytest

from agent_centric.contracts.capability import Capability
from agent_centric.contracts.manifest import AgentComponentManifest, AgentManifestVersion
from agent_centric.control_plane.manager import AgentManager

COUNTER_CAPABILITY = Capability(name="count", version="1")
REVERSE_CAPABILITY = Capability(name="reverse", version="1")
MODEL_CAPABILITY = Capability(name="llm", version="1")

COUNTER_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="counter",
    entry_point="agent_centric.agents.counter:create_counter_agent",
    description="Counts occurrences of a target character in a string.",
    declared_capabilities=frozenset({COUNTER_CAPABILITY}),
)

REVERSE_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="reverse",
    entry_point="agent_centric.agents.reverse:create_reverse_agent",
    description="Reverses a string.",
    declared_capabilities=frozenset({REVERSE_CAPABILITY}),
)

CASE_TOOL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="case_tool",
    entry_point="agent_centric.agents.case_tool:create_case_tool_agent",
    description="Uppercases a string via a mediated tool.",
    declared_capabilities=frozenset(),
)

MODEL_MANIFEST = AgentComponentManifest(
    version=AgentManifestVersion.V2,
    name="model",
    entry_point="agent_centric.agents.model_agent:create_model_agent",
    description="Answers a constrained prompt via a mediated language model.",
    declared_capabilities=frozenset({MODEL_CAPABILITY}),
)


@pytest.fixture()
def manager() -> AgentManager:
    m = AgentManager()
    m.register(COUNTER_MANIFEST)
    m.register(REVERSE_MANIFEST)
    m.register(CASE_TOOL_MANIFEST)
    return m
