"""The node contract for the agent-centric FBP tree (foundation).

Every node in the tree implements three operations — ``init``, ``run``, ``kill``
— and is responsible for its children, providing them their context. Work is
delegated down; responses and responsibility bubble up, verified at each parent.

A ``Response`` is the unit that bubbles up. It carries the value, whether it was
verified on the upward path, and the node that produced it (for responsibility
and audit). A response that failed verification is an explicit, audited failure
— never a verified success.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from .context import Context


@dataclass(frozen=True)
class Response:
    """A response that bubbles up the tree.

    Attributes:
        value: The response value.
        verified: True if the value passed verification on the upward path.
        node: The node that produced this response (for responsibility/audit).
        error: A human-readable error message if verification failed, else None.
    """

    value: Any
    verified: bool
    node: str
    error: str | None = None


class Node(Protocol):
    """The recursive node contract: init / run / kill."""

    def init(self, context: Context) -> None: ...

    def run(self, work: Any) -> Response: ...

    def kill(self) -> None: ...


class AgentNode:
    """A concrete node that owns a domain callable and can delegate to children.

    This is the base every domain node builds on. It:

    - receives a ``Context`` (provided by its parent) in ``init``,
    - owns a domain callable (its task/domain),
    - can delegate work to child nodes it is responsible for,
    - verifies each child's response on the upward path before consolidating it,
    - tries to resolve work locally first (locality), delegating only what it
      cannot resolve itself.

    Subclasses override ``_handle_local`` to resolve work within their domain.
    """

    def __init__(self, name: str, domain: Callable[..., Any] | None = None) -> None:
        self._name = name
        self._domain = domain
        self._context: Context | None = None
        self._children: list[AgentNode] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def context(self) -> Context:
        if self._context is None:
            raise RuntimeError(f"Node {self._name!r} has not been initialised.")
        return self._context

    def add_child(self, child: AgentNode) -> None:
        """Attach a child this node is responsible for."""
        self._children.append(child)

    def init(self, context: Context) -> None:
        """Initialise this node with the context provided by its parent."""
        self._context = context
        for child in self._children:
            child.init(context.child(domain=child._domain))

    def run(self, work: Any) -> Response:
        """Process ``work``: resolve locally or delegate, verify on the way up.

        Locality: if this node can resolve the work itself, it does so. If it
        cannot (or has children that can), it delegates down and consolidates
        the children's responses, verifying each before accepting responsibility.
        """
        local = self._handle_local(work)
        if local is not None:
            return self._verified(local)
        if not self._children:
            return self._failed(f"Node {self._name!r} cannot resolve work.")
        return self._delegate(work)

    def _handle_local(self, work: Any) -> Any | None:
        """Return a locally-resolved value, or None to delegate down.

        Subclasses override this. The default uses the domain callable if set.
        """
        if self._domain is not None:
            return self._domain(work)
        return None

    def _delegate(self, work: Any) -> Response:
        """Delegate work to children and consolidate their responses upward."""
        responses: list[Response] = []
        for child in self._children:
            responses.append(child.run(work))
        return self._consolidate(responses)

    def _consolidate(self, responses: list[Response]) -> Response:
        """Consolidate children's responses, verifying each on the way up.

        If any child's response is unverified, this node does not accept
        responsibility for it and bubbles up an unverified (failed) response.
        Otherwise it returns the first verified value as this node's response.
        """
        for resp in responses:
            if not resp.verified:
                return Response(
                    value=None,
                    verified=False,
                    node=self.name,
                    error=f"child {resp.node!r} returned an unverified response",
                )
        if not responses:
            return self._failed("no children produced a response")
        return responses[0]

    def _verified(self, value: Any) -> Response:
        """Wrap a locally-resolved value, applying this node's verifier."""
        ok = self.context.verify(value)
        return Response(value=value, verified=ok, node=self.name)

    def _failed(self, error: str) -> Response:
        return Response(value=None, verified=False, node=self.name, error=error)

    def kill(self) -> None:
        """Teardown: kill children, then release this node's state."""
        for child in self._children:
            child.kill()
        self._context = None