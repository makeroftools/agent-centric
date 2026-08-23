"""Tests for the FBP transport trust-boundary enforcement.

These prove the hardening steps of ``docs/transport_trust_boundary.md`` (§5)
that are self-contained and offline-testable:

- **§5.1 — a trust-boundary switch.** A non-loopback ``tcp://`` bind is refused
  unless the caller opts in explicitly (``security="local"`` or ``"tls"``);
  loopback binds are always permitted; the default fails closed.
- **§5.3 — owner-only IPC sockets.** An ``ipc://`` socket is created with mode
  ``0o600`` so other local users cannot connect.

The pure helpers are tested directly, and the driver is tested end-to-end to
prove the enforcement is wired into real binds (fail-closed, not just
documented).
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from agent_centric.fbp import FbpDriver
from agent_centric.fbp.transport import (
    IPC_SOCKET_MODE,
    SECURITY_LOCAL,
    SECURITY_TLS,
    TransportSecurityError,
    check_tcp_bind,
    enforce_ipc_socket_mode,
    validate_ipc_socket_mode,
)


def _double(value: int) -> int:
    return value * 2


def _even(value: Any) -> bool:
    return isinstance(value, int) and value % 2 == 0


class TestCheckTcpBind:
    """§5.1 — the trust-boundary switch for non-loopback tcp binds."""

    def test_loopback_bind_is_permitted_by_default(self) -> None:
        assert check_tcp_bind("127.0.0.1:5599") is None
        assert check_tcp_bind("localhost:5599") is None
        assert check_tcp_bind("127.0.0.0:5599") is None

    def test_any_127_prefix_is_loopback(self) -> None:
        assert check_tcp_bind("127.8.9.10:5599") is None

    def test_non_loopback_bind_fails_closed_by_default(self) -> None:
        reason = check_tcp_bind("0.0.0.0:5599")
        assert reason is not None
        assert "non-loopback" in reason

    def test_non_loopback_bind_fails_closed_for_private_ip(self) -> None:
        reason = check_tcp_bind("192.168.1.10:5599")
        assert reason is not None
        assert "non-loopback" in reason

    def test_explicit_local_opt_in_permits_non_loopback(self) -> None:
        assert check_tcp_bind("0.0.0.0:5599", security=SECURITY_LOCAL) is None
        assert check_tcp_bind("192.168.1.10:5599", security=SECURITY_LOCAL) is None

    def test_explicit_tls_opt_in_permits_non_loopback(self) -> None:
        assert check_tcp_bind("0.0.0.0:5599", security=SECURITY_TLS) is None

    def test_unknown_security_profile_fails_closed(self) -> None:
        reason = check_tcp_bind("127.0.0.1:5599", security="bogus")
        assert reason is not None
        assert "unknown security profile" in reason

    def test_malformed_tcp_endpoint_fails_closed(self) -> None:
        # A bare name (no port) is not a valid tcp bind; fail closed.
        reason = check_tcp_bind("root")
        assert reason is not None


class TestDriverTrustBoundary:
    """The driver enforces the boundary at real binds (fail-closed)."""

    def test_driver_refuses_non_loopback_tcp_by_default(self) -> None:
        with pytest.raises(TransportSecurityError, match="non-loopback"):
            FbpDriver(transport="tcp", endpoint="0.0.0.0:5599")

    def test_driver_accepts_non_loopback_tcp_with_explicit_opt_in(self) -> None:
        with FbpDriver(
            transport="tcp", endpoint="0.0.0.0:5599", security=SECURITY_LOCAL
        ) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42

    def test_driver_loopback_tcp_is_permitted_by_default(self) -> None:
        with FbpDriver(transport="tcp", endpoint="127.0.0.1:5599") as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42

    def test_driver_refuses_nonloopback_child_spawn_by_default(self) -> None:
        with FbpDriver() as driver:
            resp = driver.spawn("child", endpoint="tcp://0.0.0.0:5600")
            assert resp.verified is False
            assert "non-loopback" in (resp.error or "")

    def test_driver_permits_nonloopback_child_spawn_with_opt_in(self) -> None:
        with FbpDriver(security=SECURITY_LOCAL) as driver:
            resp = driver.spawn("child", endpoint="tcp://0.0.0.0:5600")
            assert resp.verified is True


class TestIpcSocketMode:
    """§5.3 — owner-only IPC sockets."""

    def test_enforce_sets_owner_only_mode(self, tmp_path: Path) -> None:
        sock = tmp_path / "fbp.ipc"
        sock.write_bytes(b"")  # simulate a bound socket file
        endpoint = f"ipc://{sock}"
        assert enforce_ipc_socket_mode(endpoint) is None
        assert os.stat(sock).st_mode & 0o777 == IPC_SOCKET_MODE

    def test_validate_accepts_owner_only(self, tmp_path: Path) -> None:
        sock = tmp_path / "fbp.ipc"
        sock.write_bytes(b"")
        os.chmod(sock, IPC_SOCKET_MODE)
        assert validate_ipc_socket_mode(f"ipc://{sock}") is None

    def test_validate_rejects_world_readable(self, tmp_path: Path) -> None:
        sock = tmp_path / "fbp.ipc"
        sock.write_bytes(b"")
        os.chmod(sock, 0o755)
        reason = validate_ipc_socket_mode(f"ipc://{sock}")
        assert reason is not None
        assert "not owner-only" in reason

    def test_validate_absent_socket_is_ok(self, tmp_path: Path) -> None:
        assert validate_ipc_socket_mode(f"ipc://{tmp_path / 'missing.ipc'}") is None

    def test_driver_ipc_bind_creates_owner_only_socket(self, tmp_path: Path) -> None:
        sock = tmp_path / "driver.ipc"
        with FbpDriver(transport="ipc", endpoint=str(sock)) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42
            # The bound socket file must be owner-only.
            assert os.stat(sock).st_mode & 0o777 == IPC_SOCKET_MODE


class TestSecurityWiring:
    """The hardening of §5.2 (TLS) and §5.4 (per-peer authz) is wired into the
    driver boundary, not just exposed as primitives."""

    def test_tls_profile_without_creds_fails_closed(self) -> None:
        from agent_centric.fbp.transport import SECURITY_TLS

        with pytest.raises(TransportSecurityError, match="TLS profile"):
            FbpDriver(transport="tcp", endpoint="127.0.0.1:5599", security=SECURITY_TLS)

    def test_tls_profile_with_complete_creds_is_accepted(self, tmp_path: Path) -> None:
        from agent_centric.fbp.security import TlsCreds
        from agent_centric.fbp.transport import SECURITY_TLS

        cert = tmp_path / "cert.pem"
        key = tmp_path / "key.pem"
        cert.write_bytes(b"cert")
        key.write_bytes(b"key")
        creds = TlsCreds(public_cert_path=str(cert), private_key_path=str(key))
        with FbpDriver(
            transport="tcp",
            endpoint="127.0.0.1:5600",
            security=SECURITY_TLS,
            tls_creds=creds,
        ) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42

    def test_peer_authz_blocks_disallowed_kind(self) -> None:
        from agent_centric.fbp.security import PeerAuthz, PeerPolicy

        authz = PeerAuthz()
        authz.add(PeerPolicy(peer="root", roles=("configure", "register")))
        with FbpDriver(peer_autz=authz) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            with pytest.raises(RuntimeError, match="per-peer authorization refused"):
                driver.run("double", {"value": 21})

    def test_peer_authz_permits_authorised_kind(self) -> None:
        from agent_centric.fbp.security import PeerAuthz, PeerPolicy

        authz = PeerAuthz()
        authz.add(PeerPolicy(peer="root", roles=("run", "configure", "register")))
        with FbpDriver(peer_autz=authz) as driver:
            driver.register("double", _double)
            driver.configure(tasks=("double",))
            resp = driver.run("double", {"value": 21})
            assert resp.verified is True
            assert resp.value == 42