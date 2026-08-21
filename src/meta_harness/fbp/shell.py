"""The shell — the root node of the agent-centric FBP tree (foundation).

The shell is a node, not an external orchestrator. It is the root of the tree:
it bootstraps the topology, provides the top-level context (the mission and the
hard rules), is the origin of work, and is the final owner of responsibility.

Work is delegated down the tree; responses and responsibility bubble up,
verified at each parent. The shell applies its own top-level verification last,
so nothing reaches the caller unverified.
"""

from __future__ import annotations

from typing import Any

from .context import Context, Verifier
from .node import AgentNode, Response


class Shell(AgentNode):
    """The root node that builds the tree and runs work through it.

    The shell owns the top-level context (mission + hard rules + a top-level
    verifier) and delegates work to its children. It is the final owner of
    responsibility: it verifies the consolidated response before returning it.
    """

    def __init__(
        self,
        *,
        rules: tuple[str, ...] = (),
        verifier: Verifier | None = None,
    ) -> None:
        super().__init__("shell")
        self._rules = rules
        self._top_verifier = verifier

    def build(self, root_context: Context | None = None) -> None:
        """Initialise the tree with the top-level context.

        The shell's context is the root context (depth 0). If none is supplied,
        one is built from the shell's rules and top-level verifier. Children are
        then initialised with their narrowed contexts.
        """
        base = root_context or Context(rules=self._rules, verifier=self._top_verifier)
        self.init(base)

    def run(self, work: Any) -> Response:
        """Run ``work`` through the tree and return a verified response.

        Delegates to children; the consolidated response is then checked against
        the shell's top-level verifier before being returned to the caller.
        """
        response = self._delegate(work)
        if not response.verified:
            return response
        if self.context.verify(response.value):
            return response
        return Response(
            value=None,
            verified=False,
            node=self.name,
            error="shell top-level verification failed",
        )

    def _handle_local(self, work: Any) -> Any | None:
        # The shell is a pure delegator; it never resolves work itself.
        return None