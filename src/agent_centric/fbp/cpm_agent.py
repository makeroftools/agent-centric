"""A domain CPM agent: deterministic, read-only critical-path analysis as a service.

This is the FBP realization of Critical Path Method as a first-class,
deterministic, **read-only** observational tool (see ``spec.md`` §3b and
``protocol.md`` §10). A ``CpmAgent`` is a pure, side-effect-free service: it
takes a network of activities (ids, durations, dependencies) in a ``run`` and
returns the critical-path analysis. It never mutates tasks, schedules, or
accounting, and it never writes any store — it only *observes*.

Because it is stateless and read-only, it needs no state grant and no key
allowlist; it serves a single ``run`` task, ``cpm``, which is deterministic and
fail-closed (a cyclic, malformed, or self-referential network is rejected,
never ambiguous).
"""

from __future__ import annotations

from typing import Any

from .agent import Agent
from .critical_path import CpmError, CpmNode, analyse_cpm
from .message import (
    DIRECTIVE_RUN,
    RESPONSE_RESULT,
    Directive,
    Response,
)

# The run-task name this CPM agent serves.
CPM_TASK = "cpm"


class CpmAgent(Agent):
    """A read-only agent that computes deterministic critical-path analyses."""

    def _handle(self, directive: Directive) -> Response:
        if directive.kind == DIRECTIVE_RUN:
            task = directive.payload.get("task")
            if task == CPM_TASK:
                return self._op_cpm(directive)
        return super()._handle(directive)

    def _run_args(self, directive: Directive) -> dict[str, Any]:
        """The run payload's arguments (``args`` dict, else the payload itself)."""
        args = directive.payload.get("args")
        if isinstance(args, dict):
            return args
        return dict(directive.payload)

    def _op_cpm(self, directive: Directive) -> Response:
        """Compute a deterministic CPM from a ``nodes`` argument.

        The run payload carries ``nodes`` as a list of dicts::

            {"id": str, "duration": number, "depends_on": [str, ...]}

        The analysis is read-only and fail-closed: an invalid network (duplicate
        id, non-positive duration, unknown dependency, or a cycle) is an
        explicit, audited error — never an ambiguous result.
        """
        args = self._run_args(directive)
        raw_nodes = args.get("nodes")
        if not isinstance(raw_nodes, list) or not raw_nodes:
            return self._error(directive, "cpm requires a non-empty 'nodes' list")
        try:
            nodes = [self._parse_node(n) for n in raw_nodes]
            analysis = analyse_cpm(nodes)
        except (CpmError, ValueError, TypeError) as exc:
            return self._error(directive, f"cpm rejected: {exc}")
        return Response(
            correlation_id=directive.correlation_id,
            kind=RESPONSE_RESULT,
            value=analysis.to_dict(),
            verified=True,
            node=self.identity,
        )

    @staticmethod
    def _parse_node(raw: Any) -> CpmNode:
        """Parse one raw node dict into a ``CpmNode`` (fail closed on shape)."""
        if not isinstance(raw, dict):
            raise ValueError(f"node is not a dict: {raw!r}")
        nid = raw.get("id")
        duration = raw.get("duration")
        deps = raw.get("depends_on", ())
        if not isinstance(nid, str) or not nid:
            raise ValueError("node requires a string 'id'")
        if not isinstance(duration, (int, float)) or isinstance(duration, bool):
            raise ValueError(f"node {nid!r} requires a numeric 'duration'")
        if not isinstance(deps, (list, tuple)):
            raise ValueError(f"node {nid!r} 'depends_on' must be a list")
        return CpmNode(
            id=nid,
            duration=duration,
            depends_on=tuple(str(d) for d in deps),
        )