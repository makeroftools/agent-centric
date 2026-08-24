"""Focused coverage for the shared worker-thread driver host.

``FbpDriverHost`` hosts ``FbpDriver`` on a dedicated worker thread so the
driver's private asyncio loop stays isolated from the ACP/MCP async loops. Its
lifecycle and fail-closed contract are mission-critical:

- ``start`` idempotently builds the driver and waits until it is ready;
- ``submit`` runs a command on the worker and returns its result; a command
  that raises is **re-raised on the caller** (fail-closed, never swallowed);
- verified arithmetic is reachable through the host;
- ``close`` tears down the driver and worker thread cleanly.

All tests are deterministic and offline (networking is never used).
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.driverhost import FbpDriverHost


class TestDriverHost:
    def test_submit_runs_verified_command(self) -> None:
        host = FbpDriverHost(store_keys="acp-*")
        try:
            value = host.submit(lambda driver: driver.run("double", {"value": 21}))
            assert value.verified is True
            assert value.value == 42
        finally:
            host.close()

    def test_submit_surfaces_raised_error(self) -> None:
        host = FbpDriverHost()
        try:

            def _boom(driver: object) -> None:
                raise ValueError("boom")

            with pytest.raises(ValueError, match="boom"):
                host.submit(_boom)
        finally:
            host.close()

    def test_start_is_idempotent_and_close_is_clean(self) -> None:
        host = FbpDriverHost()
        try:
            host.start()
            host.start()  # second start is a no-op
            value = host.submit(lambda driver: driver.run("double", {"value": 10}))
            assert value.value == 20
        finally:
            host.close()
        # close is idempotent and completes without error.
        host.close()