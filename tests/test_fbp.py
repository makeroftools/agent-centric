"""Tests for the agent-centric FBP foundation (branch `agent-centric-fbp`).

These tests prove the deterministic core of the new architecture:

- the tree is rooted at a **shell** (a node, not an external orchestrator),
- work is delegated **down** the tree,
- responses and responsibility bubble **up**, verified at each parent,
- each parent provides its children their **hierarchical context**,
- everything stays **local when possible**,
- a response that fails verification is an explicit failure, never a verified
  success (the mission-critical correctness spine).

No network, no ZeroMQ, no FastAPI — the core is pure and offline-testable.
"""

from __future__ import annotations

from typing import Any

import pytest

from meta_harness.fbp import AgentNode, Context, Shell


class _DoubleNode(AgentNode):
    """A leaf node that resolves work locally by doubling it."""

    def _handle_local(self, work: Any) -> Any | None:
        return work * 2


class _DelegatingNode(AgentNode):
    """A node that cannot resolve locally and delegates to its children."""

    def _handle_local(self, work: Any) -> Any | None:
        return None


def _always_verifier(value: Any) -> bool:
    return True


def _even_verifier(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


class TestContextHierarchy:
    def test_child_narrows_parent(self) -> None:
        root = Context(rules=("no-unverified-money",), verifier=_always_verifier)
        child = root.child(domain="intake")
        assert child.depth == 1
        assert child.parent is root
        # Child inherits the parent's rules and verifier.
        assert child.has_rule("no-unverified-money")
        assert child.verify(1) is True

    def test_child_can_override_verifier(self) -> None:
        root = Context(verifier=_always_verifier)
        child = root.child(verifier=_even_verifier)
        assert child.verify(2) is True
        assert child.verify(3) is False

    def test_depth_must_be_contiguous(self) -> None:
        root = Context()
        with pytest.raises(ValueError, match="depth"):
            Context(parent=root, depth=3)

    def test_rule_propagates_up_the_chain(self) -> None:
        root = Context(rules={"hard-rule"})
        child = root.child()
        grandchild = child.child()
        assert grandchild.has_rule("hard-rule")


class TestNodeLifecycle:
    def test_init_provides_context_to_children(self) -> None:
        shell = Shell(rules={"no-unverified-money"})
        leaf = _DoubleNode("leaf")
        shell.add_child(leaf)
        shell.build()
        assert leaf.context.depth == 1
        assert leaf.context.has_rule("no-unverified-money")

    def test_kill_releases_state(self) -> None:
        shell = Shell()
        leaf = _DoubleNode("leaf")
        shell.add_child(leaf)
        shell.build()
        shell.kill()
        with pytest.raises(RuntimeError, match="initialised"):
            _ = leaf.context


class TestDelegationAndLocality:
    def test_local_resolution(self) -> None:
        shell = Shell()
        leaf = _DoubleNode("leaf")
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(21)
        assert resp.verified is True
        assert resp.value == 42

    def test_delegation_down_the_tree(self) -> None:
        shell = Shell()
        delegator = _DelegatingNode("delegator")
        leaf = _DoubleNode("leaf")
        delegator.add_child(leaf)
        shell.add_child(delegator)
        shell.build()
        resp = shell.run(10)
        assert resp.verified is True
        assert resp.value == 20

    def test_unresolvable_leaf_fails_closed(self) -> None:
        shell = Shell()
        leaf = _DelegatingNode("leaf")  # cannot resolve, no children
        shell.add_child(leaf)
        shell.build()
        resp = shell.run(1)
        assert resp.verified is False
        assert resp.error is not None


class TestVerificationOnUpwardPath:
    def test_unverified_child_fails_closed(self) -> None:
        # The shell's even-verifier propagates down via context. A leaf that
        # returns an odd value fails verification and is rejected on the way up.
        shell = Shell(verifier=_even_verifier)
        delegator = _DelegatingNode("delegator")
        bad = _TestContextingNode("bad")  # returns work (odd) -> fails even check
        delegator.add_child(bad)
        shell.add_child(delegator)
        shell.build()
        resp = shell.run(3)
        assert resp.verified is False
        assert "unverified" in (resp.error or "")

    def test_verified_child_bubbles_up(self) -> None:
        shell = Shell(verifier=_even_verifier)
        delegator = _DelegatingNode("delegator")
        good = _DoubleNode("good")  # returns work*2 (even) -> passes even check
        delegator.add_child(good)
        shell.add_child(delegator)
        shell.build()
        resp = shell.run(4)
        assert resp.verified is True
        assert resp.value == 8

    def test_shell_top_level_verifier_applies_last(self) -> None:
        shell = Shell(verifier=_even_verifier)
        leaf = _DoubleNode("leaf")
        shell.add_child(leaf)
        shell.build()
        # 3*2 = 6 is even -> verified.
        assert shell.run(3).verified is True
        # A leaf returning an odd value directly fails the shell's verifier.
        shell2 = Shell(verifier=_even_verifier)
        odd = _TestContextingNode("odd")
        shell2.add_child(odd)
        shell2.build()
        assert shell2.run(3).verified is False


def _always_verifier(value: Any) -> bool:
    return True


def _even_verifier(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


class _TestContextingNode(AgentNode):
    """A node whose local result fails its context's verifier."""

    def _handle_local(self, work: Any) -> Any | None:
        return work