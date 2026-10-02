"""`Agent`→`Component` adapter (SPEC-0002 Phase-1 item 1) — additive, no rewrite.

The runtime ``Agent`` already implements the component lifecycle
(``init``/``run``/``kill`` over the ZeroMQ event loop). This module exposes that
live agent through the **one** frozen declarative contract, ``component.v1``
(:mod:`agent_centric.contracts.component`), without changing any behavior of the
agent itself.

The adapter is deliberately thin:

- **Lifecycle passthrough** — ``init``/``run``/``kill``/``poll`` delegate to the
  wrapped agent unchanged.
- **Entry bridge** — :meth:`AgentComponent.run_once` dispatches a directive
  through the agent's canonical synchronous handler
  (:meth:`agent_centric.cbp.agent.Agent._handle`), so a booted tree or the
  conformance harness can drive an agent through the component entry convention.
- **Declarative projection** — :meth:`AgentComponent.manifest` is a **pure
  function** of the agent's live state (identity, granted capabilities, children,
  state store), so two projections of the same state are byte-identical.

Additive only: no existing code path imports or depends on this module. It is the
first, equivalence-tested step of the SPEC-0002 Phase-1 migration (see
``docs/agent/spec-0002-phase1-adapter-design.md``); the removal of the
module-level registry and ``_child_class_for`` follows in later steps.
"""

from __future__ import annotations

from ..contracts.capability import Capability
from ..contracts.component import (
    ChildRef,
    ComponentKind,
    ComponentManifest,
    ComponentVersion,
    EntryDescriptor,
    StateDescriptor,
)
from .agent import Agent
from .message import Directive, Response

# The frozen harness contract every component implements (SPEC-0007/0012). The
# boot path cross-checks a manifest's ``implements`` against its lock entry.
COMPONENT_CONTRACT = "component.v1"


class ComponentAdapterError(TypeError):
    """An ``Agent`` could not be projected as a component (fail-closed)."""


class AgentComponent:
    """A component face over a live :class:`~agent_centric.cbp.agent.Agent`.

    Attributes:
        agent: The wrapped runtime agent (read-only).
    """

    def __init__(
        self,
        agent: Agent,
        *,
        entry: EntryDescriptor,
        implements: tuple[str, ...] = (COMPONENT_CONTRACT,),
    ) -> None:
        """Wrap ``agent`` as a component with the given distribution ``entry``.

        Args:
            agent: The live agent to adapt.
            entry: The distribution entry descriptor. The adapter cannot infer a
                packaging entrypoint from a runtime object, so the caller (the
                packager) supplies it explicitly.
            implements: The harness contracts the component implements; defaults
                to ``component.v1`` and must be non-empty (fail-closed).

        Raises:
            ComponentAdapterError: If ``agent`` is not an ``Agent`` or
                ``implements`` is empty.
        """
        if not isinstance(agent, Agent):
            raise ComponentAdapterError(
                f"expected an Agent, got {type(agent).__name__}"
            )
        if not implements:
            raise ComponentAdapterError(
                "a component must implement at least one harness contract"
            )
        self._agent = agent
        self._entry = entry
        self._implements = tuple(implements)

    @property
    def agent(self) -> Agent:
        """The wrapped runtime agent (read-only)."""
        return self._agent

    # -- lifecycle passthrough (the SPEC-0012 runtime contract) --------------

    def init(self) -> None:
        """Initialise the wrapped agent (delegates unchanged)."""
        self._agent.init()

    async def run(self) -> None:
        """Run the wrapped agent's event loop (delegates unchanged)."""
        await self._agent.run()

    def kill(self) -> None:
        """Tear the wrapped agent down (delegates unchanged)."""
        self._agent.kill()

    async def poll(self, timeout: float = 0.0) -> list[Response]:
        """Service one batch of events on the wrapped agent (delegates unchanged)."""
        return await self._agent.poll(timeout=timeout)

    # -- entry bridge (component entry convention: payload -> response) ------

    def run_once(self, directive: Directive) -> Response:
        """Dispatch ``directive`` through the agent's canonical handler (pure sync).

        This is the equivalence seam: it returns exactly the :class:`Response`
        the agent produces when the same directive is delivered over the bus.
        No corpus of state is mutated beyond what the directive itself does.

        Raises:
            ComponentAdapterError: If ``directive`` is not a Directive.
        """
        if not isinstance(directive, Directive):
            raise ComponentAdapterError(
                f"expected a Directive, got {type(directive).__name__}"
            )
        return self._agent._handle(directive)

    # -- declarative projection (component.v1) -------------------------------

    def kind(self) -> str:
        """The component kind: ``model`` > ``composite`` > ``atomic``.

        A model agent keeps its ``model`` kind (SPEC-0009); an agent that owns
        children is a ``composite``; otherwise it is ``atomic``. This is a pure
        function of live state.
        """
        if type(self._agent).__name__ == "ModelAgent":
            return ComponentKind.MODEL
        if self._agent.children:
            return ComponentKind.COMPOSITE
        return ComponentKind.ATOMIC

    def manifest(self) -> ComponentManifest:
        """Project the agent's live state into a ``component.v1`` manifest (pure).

        The projection is derived only from what the agent owns: its identity,
        granted capabilities (its instance registry), declared children, and an
        explicitly granted state path. No clock, no I/O, no ambient global.
        """
        kind = self.kind()
        children = (
            tuple(ChildRef(embed=name) for name in sorted(self._agent.children))
            if kind == ComponentKind.COMPOSITE
            else ()
        )
        state_path = self._agent._state_path
        state = (
            StateDescriptor(kind="sqlite", path=str(state_path))
            if state_path
            else None
        )
        capabilities = tuple(
            Capability(name=name) for name in self._agent._registry.names()
        )
        return ComponentManifest(
            version=ComponentVersion.V1,
            name=self._agent.identity,
            kind=kind,
            entry=self._entry,
            implements=self._implements,
            state=state,
            children=children,
            directive="",
            capabilities=capabilities,
            provenance=None,
            ports={},
        )
