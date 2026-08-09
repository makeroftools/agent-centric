"""Subprocess worker entry point (runs in the child process).

This module is intentionally **not imported by the Manager**. It is spawned via
``python -m meta_harness.control_plane.worker`` so that it is executed exactly
once as ``__main__`` — avoiding the double-import that would otherwise break
class identity across the IPC boundary.

The child runs only the agent's generator loop. It never mediates tools,
enforces envelopes, applies policy, records the trajectory, or verifies output
— all of that stays in the Manager. It communicates solely via the versioned
JSON-lines protocol on stdin/stdout.
"""

from __future__ import annotations

import contextlib
import json
import sys
from typing import Any

from ..agents.interface import AgentResult, AgentStep, ToolRequest
from .execution import (
    IPC_VERSION,
    _decode_sent,
    _decode_tool_context,
    _encode_yield,
    instantiate_agent,
)


def _write_message(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _subprocess_main() -> None:
    start_line = sys.stdin.readline()
    if not start_line:
        return
    try:
        start = json.loads(start_line)
    except json.JSONDecodeError:
        return
    if start.get("version") != IPC_VERSION:
        _write_message({"type": "error", "message": "IPC version mismatch."})
        return

    try:
        agent = instantiate_agent(start["entry_point"])
        tool_context = _decode_tool_context(start.get("tools") or [])
        gen = agent(start.get("payload"), int(start.get("step_budget", 0)), tool_context)
    except Exception as exc:  # noqa: BLE001 - reported to the Manager
        _write_message({"type": "error", "message": f"startup failed: {exc}"})
        return

    sent: Any = None
    while True:
        # Wait for the Manager to deliver the next value (or close). The value
        # is the resume for the *previous* yield, matching the Manager's
        # ``next_step(sent)`` semantics, so it must be read *before* resuming.
        line = sys.stdin.readline()
        if not line:
            return  # Manager closed the channel
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            _write_message({"type": "error", "message": "malformed Manager message."})
            return
        msg_type = message.get("type")
        if msg_type == "send":
            sent = _decode_sent(message.get("value") or {})
        elif msg_type == "close":
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort
                gen.close()
            return
        else:
            _write_message({"type": "error", "message": f"unknown message: {msg_type!r}"})
            return

        try:
            item = gen.send(sent)
        except StopIteration as stop:
            result = stop.value
            if not isinstance(result, AgentResult):
                _write_message(
                    {"type": "error", "message": "Agent returned a non-AgentResult value."}
                )
                return
            _write_message({"type": "result", "value": {"output": result.output}})
            return
        except Exception as exc:  # noqa: BLE001 - reported to the Manager
            _write_message({"type": "error", "message": f"{type(exc).__name__}: {exc}"})
            return

        if isinstance(item, (AgentStep, ToolRequest)):
            _write_message({"type": "step", "value": _encode_yield(item)})
        else:
            _write_message({"type": "error", "message": f"unsupported yield: {item!r}"})
            return


if __name__ == "__main__":
    _subprocess_main()