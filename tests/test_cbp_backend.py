"""Tests for inproc-only execution-backend enforcement (SPEC-0002 Phase-1 item 6).

The reference tree admits only the deterministic, offline ``inproc`` backend.
Any other backend name fails closed; subprocess isolation remains a separate,
opt-in path (``component_boot(allow_subprocess=True)``), not a tree backend.
"""

from __future__ import annotations

import pytest

from agent_centric.cbp import CbpDriver
from agent_centric.cbp.driver import ADMITTED_BACKENDS, BackendNotAdmitted


class TestBackendAdmission:
    def test_only_inproc_is_admitted(self) -> None:
        assert frozenset({"inproc"}) == ADMITTED_BACKENDS

    def test_default_and_explicit_inproc_are_accepted(self) -> None:
        with CbpDriver() as driver:
            assert driver is not None
        with CbpDriver(backend="inproc") as driver:
            assert driver is not None

    @pytest.mark.parametrize("backend", ["subprocess", "native", "remote", ""])
    def test_other_backends_fail_closed(self, backend: str) -> None:
        with pytest.raises(BackendNotAdmitted):
            CbpDriver(backend=backend)
