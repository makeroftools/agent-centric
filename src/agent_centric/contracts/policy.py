"""Policy contract (versioned).

A Policy is a thin, deterministic governance rule owned by the control plane.
It constrains what a task or composition is allowed to do — which agents,
capabilities, and tools — before execution begins. The Agent Manager evaluates
and enforces the policy; a violation produces an explicit, audited failure and
no restricted work starts.

Semantics (deterministic, deny-overrides-allow):
- An item is denied if it is in the corresponding deny set.
- Otherwise, if the corresponding allow set is non-empty, the item is allowed
  only if it is in that allow set.
- Otherwise (allow set empty), the item is allowed.

Evaluation is pure and side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .capability import Capability


class PolicyVersion(StrEnum):
    """Version of the policy contract."""

    V1 = "policy.v1"


@dataclass(frozen=True)
class PolicyDecision:
    """Outcome of a single policy check.

    Attributes:
        allowed: True if the item is permitted by the policy.
        reason: A human-readable explanation, set when the item is denied.
    """

    allowed: bool
    reason: str | None = None


@dataclass(frozen=True)
class Policy:
    """A thin, immutable governance rule constraining agents, capabilities, tools.

    Attributes:
        version: The policy contract version.
        allow_agents: If non-empty, only these agent names are permitted.
        deny_agents: These agent names are always denied.
        allow_capabilities: If non-empty, only these exact capabilities are
            permitted.
        deny_capabilities: These exact capabilities are always denied.
        allow_tools: If non-empty, only these tool names are permitted.
        deny_tools: These tool names are always denied.
    """

    version: PolicyVersion
    allow_agents: frozenset[str] = frozenset()
    deny_agents: frozenset[str] = frozenset()
    allow_capabilities: frozenset[Capability] = frozenset()
    deny_capabilities: frozenset[Capability] = frozenset()
    allow_tools: frozenset[str] = frozenset()
    deny_tools: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if self.version is not PolicyVersion.V1:
            raise ValueError(f"Unsupported policy version: {self.version!r}")
        if any(not name for name in self.allow_agents | self.deny_agents):
            raise ValueError("Agent names must be non-empty.")
        if any(not name for name in self.allow_tools | self.deny_tools):
            raise ValueError("Tool names must be non-empty.")

    def check_agent(self, agent_name: str) -> PolicyDecision:
        """Check whether an agent name is permitted by this policy."""
        if agent_name in self.deny_agents:
            return PolicyDecision(False, f"agent {agent_name!r} denied by policy")
        if self.allow_agents and agent_name not in self.allow_agents:
            return PolicyDecision(False, f"agent {agent_name!r} not allowed by policy")
        return PolicyDecision(True)

    def check_capability(self, capability: Capability) -> PolicyDecision:
        """Check whether an exact capability is permitted by this policy."""
        label = f"capability {capability.name!r} (v{capability.version})"
        if capability in self.deny_capabilities:
            return PolicyDecision(False, f"{label} denied by policy")
        if self.allow_capabilities and capability not in self.allow_capabilities:
            return PolicyDecision(False, f"{label} not allowed by policy")
        return PolicyDecision(True)

    def check_tool(self, tool_name: str) -> PolicyDecision:
        """Check whether a tool name is permitted by this policy."""
        if tool_name in self.deny_tools:
            return PolicyDecision(False, f"tool {tool_name!r} denied by policy")
        if self.allow_tools and tool_name not in self.allow_tools:
            return PolicyDecision(False, f"tool {tool_name!r} not allowed by policy")
        return PolicyDecision(True)
