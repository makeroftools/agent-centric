"""Hierarchical context for the agent-centric FBP tree (foundation).

The context is the governance mechanism of the agent-centric design. A parent
node provides its children with a ``Context`` that carries the domain, the
rules, and the verification constraints they operate within. Context composes
down the tree: a child's context is its parent's context, narrowed by the
parent's domain.

The context is immutable and deterministic so that the tree is reconstructible
and auditable. It carries:

- ``domain`` — the callable/domain this node is responsible for (may be None
  for a pure delegator).
- ``rules`` — a tuple of hard rules (e.g. "no unverified money") that constrain
  this subtree.
- ``verifier`` — the verification gate applied to responses on the upward path.
- ``parent`` — the parent context (None at the root), forming the hierarchy.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

# A verifier is a pure callable: given a response value, return True if it is
# verified correct, else False. The foundation keeps this minimal; richer
# verifiers (recompute-from-payload) are layered on later.
Verifier = Callable[[Any], bool]


@dataclass(frozen=True)
class Context:
    """Immutable, hierarchical context provided by a parent to its children.

    Attributes:
        domain: The callable/domain this node is responsible for, or None.
        rules: Hard constraints that apply to this subtree.
        verifier: The verification gate for the upward path, or None (no gate).
        parent: The parent context, or None at the root.
        depth: The depth in the tree (root is 0).
    """

    domain: Callable[..., Any] | None = None
    rules: tuple[str, ...] = ()
    verifier: Verifier | None = None
    parent: Context | None = None
    depth: int = 0

    def __post_init__(self) -> None:
        if self.depth < 0:
            raise ValueError("Context depth must be non-negative.")
        if self.parent is not None and self.parent.depth + 1 != self.depth:
            raise ValueError(
                "Child context depth must be exactly parent.depth + 1 "
                "(hierarchical context)."
            )

    def child(
        self,
        *,
        domain: Callable[..., Any] | None = None,
        rules: tuple[str, ...] | None = None,
        verifier: Verifier | None = None,
    ) -> Context:
        """Return a child context that narrows this one (hierarchical compose).

        The child inherits this context's rules and verifier unless overridden,
        and its depth is one greater. This is how governance composes down the
        tree: a child operates within its parent's constraints, narrowed by its
        own domain.
        """
        return Context(
            domain=domain,
            rules=self.rules if rules is None else rules,
            verifier=self.verifier if verifier is None else verifier,
            parent=self,
            depth=self.depth + 1,
        )

    def has_rule(self, rule: str) -> bool:
        """Return True if ``rule`` applies anywhere in this context chain."""
        node: Context | None = self
        while node is not None:
            if rule in node.rules:
                return True
            node = node.parent
        return False

    def verify(self, value: Any) -> bool:
        """Apply this context's verifier to ``value`` (True if no verifier)."""
        if self.verifier is None:
            return True
        return self.verifier(value)