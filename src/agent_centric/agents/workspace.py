"""A deterministic workspace specialty agent (Volley 023).

This agent operates on a local, allowlisted workspace through the Manager. It
validates its payload, may *request* the mediated workspace file tools
(``list_workspace``, ``read_workspace_file``, ``write_workspace_file``,
``create_workspace_dir`` — only if explicitly granted), and returns a
deterministic result that the Manager's mandatory verification gate recomputes
independently.

The payload is a mapping describing a workspace operation (see the verifier for
the exact shape). The output is a plain, JSON-serialisable mapping. The agent
never gains broad filesystem powers: every tool call is resolved against the
workspace allowlist by the Manager, and any disallowed path is an explicit,
audited, fail-closed failure.
"""

from __future__ import annotations

from collections.abc import Generator
from typing import Any

from .interface import AgentResult, AgentStep, ToolContext, ToolRequest, ToolResult


class WorkspaceAgent:
    """Deterministic agent that performs an allowlisted workspace operation."""

    def __call__(
        self, payload: Any, step_budget: int, tools: ToolContext
    ) -> Generator[AgentStep | ToolRequest, None, AgentResult]:
        if not isinstance(payload, dict):
            raise TypeError("WorkspaceAgent payload must be a mapping.")

        operation = payload.get("operation")
        if not isinstance(operation, str) or not operation:
            raise TypeError("WorkspaceAgent payload['operation'] must be a non-empty string.")

        yield AgentStep(
            description="validated workspace payload",
            detail={"operation": operation},
        )

        if operation == "list":
            if not tools.available("list_workspace"):
                yield AgentStep(description="tool list_workspace not granted")
                return AgentResult(output=None)
            sent: Any = yield ToolRequest(name="list_workspace", args={})
            return (yield from self._finish_tool(sent, "list_workspace"))

        if operation == "read":
            relative_path = payload.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                raise TypeError("WorkspaceAgent payload['relative_path'] must be a string.")
            if not tools.available("read_workspace_file"):
                yield AgentStep(description="tool read_workspace_file not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="read_workspace_file", args={"relative_path": relative_path}
            )
            return (yield from self._finish_tool(sent, "read_workspace_file"))

        if operation == "write":
            relative_path = payload.get("relative_path")
            content = payload.get("content")
            if not isinstance(relative_path, str) or not relative_path:
                raise TypeError("WorkspaceAgent payload['relative_path'] must be a string.")
            if not isinstance(content, str):
                raise TypeError("WorkspaceAgent payload['content'] must be a string.")
            if not tools.available("write_workspace_file"):
                yield AgentStep(description="tool write_workspace_file not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="write_workspace_file",
                args={"relative_path": relative_path, "content": content},
            )
            return (yield from self._finish_tool(sent, "write_workspace_file"))

        if operation == "mkdir":
            relative_path = payload.get("relative_path")
            if not isinstance(relative_path, str) or not relative_path:
                raise TypeError("WorkspaceAgent payload['relative_path'] must be a string.")
            if not tools.available("create_workspace_dir"):
                yield AgentStep(description="tool create_workspace_dir not granted")
                return AgentResult(output=None)
            sent = yield ToolRequest(
                name="create_workspace_dir", args={"relative_path": relative_path}
            )
            return (yield from self._finish_tool(sent, "create_workspace_dir"))

        raise TypeError(f"WorkspaceAgent does not support operation {operation!r}.")

    @staticmethod
    def _finish_tool(
        sent: Any, tool_name: str
    ) -> Generator[AgentStep, None, AgentResult]:
        """Handle the ToolResult from a workspace tool call.

        On success the tool's mapping output is returned as the agent output
        (the verifier recomputes it). On failure the agent returns ``None``,
        which the verification gate rejects — a failed or disallowed workspace
        operation never produces a verified success.
        """
        if not isinstance(sent, ToolResult):
            raise TypeError(f"Manager did not deliver a ToolResult for {tool_name}.")
        if not sent.success:
            yield AgentStep(description=f"{tool_name} tool call failed: {sent.error}")
            return AgentResult(output=None)
        output = sent.output
        if not isinstance(output, dict):
            raise TypeError(f"{tool_name} tool returned a non-mapping result.")
        yield AgentStep(
            description=f"received {tool_name} tool result",
            detail={"relative_path": output.get("relative_path")},
        )
        return AgentResult(output=output)


def create_workspace_agent() -> WorkspaceAgent:
    """Factory matching the manifest entry-point convention."""
    return WorkspaceAgent()