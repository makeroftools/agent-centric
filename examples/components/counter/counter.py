"""Reference behavior for the example ``counter`` component.

This is the component's self-contained behavior. In Phase 1a the manifest's
``entry`` points at the allowlisted, in-tree implementation
(``agent_centric.agents.counter``); this file documents the component's own
implementation and is included in the bundle (so its bytes are content-hashed).
"""

from __future__ import annotations


def count(text: str, target: str) -> int:
    """Count occurrences of a single-character ``target`` in ``text``."""
    if len(target) != 1:
        raise ValueError("target must be a single character")
    return sum(1 for ch in text if ch == target)
