"""Tests for the CURVE transport-encryption primitives (``fbp/curve.py``).

CURVE is ZeroMQ libzmq's native encryption — the dependency-free way to close
the "no encryption on the wire" production gap. These tests are deterministic
and offline: they exercise key/socket-option/cert generation and, over a real
loopback ``tcp://`` link, prove that an authorized client completes an encrypted
handshake while an unauthorized client fails closed.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import zmq

from agent_centric.fbp.curve import (
    CurveAuth,
    CurveConfig,
    apply_options,
    client_options,
    generate_curve_config,
    server_options,
    write_public_cert,
)


class TestCurveConfig:
    def test_generates_distinct_keypairs(self) -> None:
        cfg = generate_curve_config()
        assert isinstance(cfg, CurveConfig)
        # Server and client keypairs are distinct; all are 40-char z85.
        for v in (
            cfg.server_public,
            cfg.server_secret,
            cfg.client_public,
            cfg.client_secret,
        ):
            assert isinstance(v, bytes) and len(v) == 40
        assert cfg.server_public != cfg.client_public
        assert cfg.server_secret != cfg.client_secret

    def test_options_are_internal_disjoint(self) -> None:
        cfg = generate_curve_config()
        so = server_options(cfg)
        co = client_options(cfg)
        # Server marks itself a server; client marks not; both carry keys.
        assert so["curve_server"] is True
        assert co["curve_server"] is False
        assert so["curve_publickey"] == cfg.server_public
        assert co["curve_serverkey"] == cfg.server_public

    def test_client_options_require_server_key(self) -> None:
        cfg = generate_curve_config()
        opts = client_options(cfg)  # server public always present in a full config
        assert opts["curve_serverkey"] == cfg.server_public


class TestCurveAuth:
    def test_write_cert_and_apply_options(self, tmp_path: Path) -> None:
        cfg = generate_curve_config()
        cert = tmp_path / "peer.key"
        write_public_cert(str(cert), cfg.client_public)
        content = cert.read_text()
        assert "curve" in content
        assert cfg.client_public.decode("ascii") in content

        ctx = zmq.Context()
        sock = ctx.socket(zmq.ROUTER)
        try:
            apply_options(sock, server_options(cfg))
            assert sock.getsockopt(zmq.CURVE_SERVER) is True
            assert sock.getsockopt(zmq.CURVE_PUBLICKEY) == cfg.server_public
        finally:
            sock.close(0)
            ctx.term()

    def test_auth_stop_is_idempotent(self) -> None:
        ctx = zmq.Context()
        auth = CurveAuth(ctx)
        auth.start(allowed=[generate_curve_config().client_public])
        auth.stop()
        auth.stop()  # no error


class TestWire:
    """Live loopback CURVE round-trip + fail-closed rejection."""

    def _bound_port(self, router: zmq.Socket[Any]) -> str:
        raw = router.getsockopt(zmq.LAST_ENDPOINT)
        # LAST_ENDPOINT is bytes on a bound tcp socket; guard the union.
        endpoint = raw if isinstance(raw, bytes) else str(raw).encode()
        return endpoint.decode("ascii").split(":")[-1]

    def test_authorized_client_round_trips(self) -> None:
        cfg = generate_curve_config()
        ctx = zmq.Context()
        auth = CurveAuth(ctx)
        auth.start(allowed=[cfg.client_public])
        try:
            router = ctx.socket(zmq.ROUTER)
            apply_options(router, server_options(cfg))
            router.bind("tcp://127.0.0.1:0")
            port = self._bound_port(router)

            dealer = ctx.socket(zmq.DEALER)
            dealer.identity = b"child"
            apply_options(dealer, client_options(cfg))
            dealer.connect(f"tcp://127.0.0.1:{port}")
            import time

            time.sleep(0.5)
            dealer.send_multipart([b"hello"])
            got = None
            if router.poll(timeout=2000):
                got = router.recv_multipart()
            router.close(0)
            dealer.close(0)
            assert got is not None and got[-1] == b"hello"
        finally:
            auth.stop()
            ctx.term()


class TestCurveDriver:
    """End-to-end CURVE wiring through the real FbpDriver over tcp."""

    def test_curve_driver_run_and_delegate(self) -> None:
        from agent_centric.fbp import FbpDriver

        def _double(value: int) -> int:
            return value * 2

        driver = FbpDriver(
            transport="tcp", endpoint="127.0.0.1:0", curve=True, identity="root"
        )
        try:
            driver.register("double", _double, source_url="file:///tasks/double")
            driver.configure(tasks=("double",))
            r = driver.run("double", {"value": 21})
            assert r.verified and r.value == 42
            # Spawn + delegate over the CURVE-encrypted child link.
            driver.spawn("child")
            driver.configure_child("child", tasks=("double",))
            r2 = driver.run("double", {"value": 10}, child="child")
            assert r2.verified and r2.value == 20
        finally:
            driver.close()

    def test_curve_rejects_inproc(self) -> None:
        from agent_centric.fbp import FbpDriver
        from agent_centric.fbp.transport import TransportSecurityError

        with pytest.raises(TransportSecurityError):
            FbpDriver(transport="inproc", curve=True)

    def test_curve_rogue_client_rejected(self) -> None:
        import time

        from agent_centric.fbp import FbpDriver

        def _double(value: int) -> int:
            return value * 2

        # Fixed port so the rogue can target the CURVE server deterministically.
        driver = FbpDriver(
            transport="tcp", endpoint="127.0.0.1:5630", curve=True, identity="root"
        )
        ctx = zmq.Context()
        rogue: Any = ctx.socket(zmq.DEALER)
        try:
            driver.register("double", _double, source_url="file:///tasks/double")
            driver.configure(tasks=("double",))
            # A rogue client that guesses the wrong server key cannot complete a
            # CURVE handshake; its send is dropped and it gets nothing back.
            rogue.identity = b"rogue"
            rp, rs = zmq.curve_keypair()
            rogue.curve_publickey = rp
            rogue.curve_secretkey = rs
            rogue.curve_serverkey = b"0" * 40
            rogue.connect("tcp://127.0.0.1:5630")
            time.sleep(0.5)
            rogue.send_multipart([b"hello"])
            rogue.setsockopt(zmq.RCVTIMEO, 400)
            got: Any = None
            try:
                got = rogue.recv_multipart()
            except Exception:
                got = "closed"
            assert got is None or got == "closed"
        finally:
            rogue.close(0)
            ctx.term()
            driver.close()