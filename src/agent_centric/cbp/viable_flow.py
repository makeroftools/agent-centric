"""Pure, allowlisted entries for the SPEC-0011 "viable" typed-flow demo.

Installed in the harness and allowlisted (``component_runtime``) like the other
example component entries. Deliberately tiny and pure so the integration
(boot a signed lock -> run typed-port dataflow -> replay) is deterministic and
independently verifiable.
"""

from __future__ import annotations

from typing import Any


class ViableFlowError(TypeError):
    """A viable-flow payload is malformed (fail-closed)."""


def is_even(value: Any) -> bool:
    """Return ``True`` iff ``value`` is an even integer (pure).

    A non-integer (or a ``bool``) fails closed with :class:`ViableFlowError`, so
    a mis-typed edge is an explicit, audited failure — never a silent coercion.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise ViableFlowError("is_even expects an integer")
    return value % 2 == 0
