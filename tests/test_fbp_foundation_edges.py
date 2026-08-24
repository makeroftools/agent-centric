"""Branch coverage for the FBP tree foundation (node / context / shell).

The core correctness spine must fully cover its fail-closed and
verification-on-the-way-up branches:

- a node with a domain callable resolves locally and applies its context
  verifier (verified vs demoted),
- an empty consolidation / an unresolvable pure-delegator shell asserts an
  explicit audited failure — never a silent success,
- the shell applies its top-level verifier last (so nothing reaches the caller
  unverified), including the demotion path,
- context inheritance lets a child override rules or verifier while still
  inheriting the parent chain,
- ``AgentNode.context`` raises before init (not-yet-initialised guard).

All tests are deterministic and offline.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_centric.fbp import AgentNode, Context, Shell


def _even(v: Any) -> bool:
    return isinstance(v, int) and v % 2 == 0


def _always(v: Any) -> bool:
    return True


class _DomainNode(AgentNode):
    """A leaf that resolves locally via its domain callable (not an override)."""


class _UnresolvableNode(AgentNode):
    def _handle_local(self, work: Any) -> Any | None:
        return None


class _OddNode(AgentNode):
    """A leaf whose local result fails an even-verifier."""

    def _handle_local(self, work: Any) -> Any | None:
        return 3


class TestAgentNodeDomainLocal:
    def test_domain_callable_resolves_locally(self) -> None:
        shell = Shell(verifier=_always)
        leaf = _DomainNode("leaf", domain=lambda work: work * 2)
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(21)
        assert resp.verified is True
        assert resp.value == 42

    def test_domain_result_fails_verifier_demotes(self) -> None:
        # domain returns an odd value; the even-verifier propagates via context.
        shell = Shell(verifier=_even)
        leaf = _DomainNode("leaf", domain=lambda work: work)  # returns odd for odd input
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(3)
        assert resp.verified is False
        assert resp.error is not None


class TestEmptyAndUnresolvable:
    def test_empty_shell_fails_closed(self) -> None:
        """A shell with no children cannot resolve work — explicit failure."""
        shell = Shell()
        shell.build()
        resp = shell.run(1)
        assert resp.verified is False
        assert "no children produced a response" in (resp.error or "")

    def test_unresolvable_child_asserts_failure(self) -> None:
        shell = Shell()
        node = _UnresolvableNode("cannot")  # no domain, no children
        shell.add_child(node)
        shell.build()
        resp = shell.run(1)
        assert resp.verified is False
        # The child's unverified response bubbles up and is surfaced by the
        # parent's consolidation as an explicit audited failure.
        assert "unverified" in (resp.error or "")


class TestShellTopLevelVerification:
    def test_verified_child_passes_top_level(self) -> None:
        """A child verified by the shared even-verifier reaches the shell's
        top-level check and returns a verified response."""
        shell = Shell(verifier=_even)
        leaf = _DomainNode("leaf", domain=lambda work: work * 2)  # 4*2=8 even
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(4)
        assert resp.verified is True
        assert resp.value == 8

    def test_odd_child_fails_closed_before_top_level_check(self) -> None:
        """The even-verifier propagates to children via context, so an odd
        leaf is demoted before the shell's top-level check runs — nothing
        unverified reaches the caller."""
        shell = Shell(verifier=_even)
        leaf = _OddNode("odd")  # returns 3 (odd)
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(1)
        assert resp.verified is False
        # The child's demotion is surfaced as-is; it is not re-labelled by the
        # top-level check (which only runs on an already-verified response).
        assert "top-level" not in (resp.error or "")


class TestContextInheritance:
    def test_child_overrides_local_rules_keeps_parent_chain(self) -> None:
        """A child's own rules may be overridden, but ``has_rule`` walks the
        whole chain, so a parent rule still applies to the subtree."""
        root = Context(rules=("hard-rule",))
        child = root.child(rules=("other",))
        assert child.has_rule("other")      # the child's own (overridden) rule
        assert child.has_rule("hard-rule")  # still inherited from the parent chain

    def test_child_inherits_when_no_override(self) -> None:
        root = Context(rules=("hard-rule",), verifier=_even)
        child = root.child()
        assert child.has_rule("hard-rule")
        assert child.verify(2) is True

    def test_context_negative_depth_rejected(self) -> None:
        with pytest.raises(ValueError, match="depth"):
            Context(depth=-1)


class TestNodeContextGuard:
    def test_uninit_context_raises(self) -> None:
        node = _DomainNode("a")
        with pytest.raises(RuntimeError, match="initialised"):
            _ = node.context