"""Tool contract (versioned).

A tool is an external capability an agent may request, but only through the
Agent Manager. The tool contract is the versioned, immutable declaration of a
tool: its identity, description, input schema, output schema, and execution
semantics. The Manager is the sole authority that makes tools available to an
agent and that executes tool calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ToolVersion(StrEnum):
    """Version of the tool descriptor contract."""

    V1 = "tool.v1"


@dataclass(frozen=True)
class ToolDescriptor:
    """Immutable declaration of a tool.

    Attributes:
        version: The tool descriptor contract version.
        name: Unique, stable identifier of the tool.
        description: Human-readable description of what the tool does.
        input_schema: Mapping of argument field name to expected type name
            (e.g. ``{"text": "str"}``).
        output_schema: Expected type name of the tool's output (e.g. ``"str"``).
        execution_semantics: A description of the execution semantics, e.g.
            whether the tool is pure and side-effect free.
    """

    version: ToolVersion
    name: str
    description: str
    input_schema: dict[str, str] = field(default_factory=dict)
    output_schema: str = ""
    execution_semantics: str = "pure, deterministic, side-effect free"

    def __post_init__(self) -> None:
        if self.version is not ToolVersion.V1:
            raise ValueError(f"Unsupported tool version: {self.version!r}")
        if not self.name:
            raise ValueError("Tool name must be non-empty.")
        if not self.description:
            raise ValueError("Tool description must be non-empty.")