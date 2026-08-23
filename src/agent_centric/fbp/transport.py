"""Transport trust-boundary enforcement for the agent-centric FBP tree.

This module implements the hardening steps of ``docs/transport_trust_boundary.md``
(§5) that are self-contained and enforceable without adding dependencies:

- **§5.1 — a trust-boundary switch.** A non-loopback ``tcp://`` bind is refused
  unless the caller has explicitly opted in to a security profile
  (``security="local"`` or ``security="tls"``). The default is fail-closed: a
  caller who asks for a remote bind without opting in gets an explicit error,
  never a silent exposure.
- **§5.3 — owner-only IPC sockets.** An ``ipc://`` endpoint is created with
  mode ``0o600`` (owner-only) so other local users cannot connect to the
  socket. The mode is applied to the socket file after bind.

The functions here are pure and deterministic: given an endpoint string and an
optional security profile, they return either ``None`` (the bind is permitted)
or a human-readable reason it must fail closed. They never touch the network
themselves, so they are fully offline-testable.

The transport is trusted; everything on the other side of it is untrusted. This
module is where that boundary is *enforced*, not just documented.
"""

from __future__ import annotations

import os
import re
from typing import Final

# The security profiles a caller may opt in to. ``loopback`` is the fail-closed
# default: only loopback binds are permitted. ``local`` means "I accept the
# risk of a non-loopback bind on a trusted local network"; ``tls`` is reserved
# for a future mutual-TLS profile (not yet implemented — it is accepted here so
# the switch is forward-compatible, but the transport does not yet encrypt).
SECURITY_LOOPBACK: Final = "loopback"
SECURITY_LOCAL: Final = "local"
SECURITY_TLS: Final = "tls"
SECURITY_PROFILES: Final = frozenset({SECURITY_LOOPBACK, SECURITY_LOCAL, SECURITY_TLS})

#: The default, fail-closed profile. A non-loopback bind without an explicit
#: opt-in is refused.
SECURITY_DEFAULT: Final = SECURITY_LOOPBACK

#: The default owner-only mode for new IPC socket files.
IPC_SOCKET_MODE: Final = 0o600

#: A tcp endpoint is ``host:port``. The host may be a hostname or an IP literal.
_TCP_RE: Final = re.compile(r"^(?P<host>[^:]+):(?P<port>\d+)$")

#: Loopback host names / addresses that are always safe to bind.
_LOOPBACK_HOSTS: Final = frozenset(
    {
        "127.0.0.1",
        "127.0.0.0",
        "::1",
        "localhost",
        "127.0.0.0.1",
    }
)


class TransportSecurityError(ValueError):
    """A transport bind was refused by the trust-boundary policy."""


def _is_loopback_host(host: str) -> bool:
    """True if ``host`` is a loopback address (127/8, ::1, or localhost)."""
    if host in _LOOPBACK_HOSTS:
        return True
    # The whole 127.0.0.0/8 range is loopback; accept any 127.x.y.z.
    if host.startswith("127."):
        parts = host.split(".")
        return len(parts) == 4 and all(p.isdigit() for p in parts)
    return False


def _is_loopback_tcp_endpoint(endpoint: str) -> bool:
    """True if a ``tcp://host:port`` endpoint binds a loopback host."""
    match = _TCP_RE.match(endpoint)
    if match is None:
        # A bare name (no port) is not a valid tcp bind; treat as non-loopback
        # so it fails closed rather than silently binding an odd address.
        return False
    return _is_loopback_host(match.group("host"))


def check_tcp_bind(endpoint: str, security: str = SECURITY_DEFAULT) -> str | None:
    """Return ``None`` if a ``tcp`` bind is permitted, else a fail-closed reason.

    Args:
        endpoint: The endpoint *without* the ``tcp://`` scheme (e.g.
            ``127.0.0.1:5599`` or ``0.0.0.0:5599``).
        security: The caller's opt-in security profile. The default
            ``local`` permits loopback binds only; a non-loopback bind requires
            an explicit ``local`` or ``tls`` opt-in.

    Returns:
        ``None`` when the bind is permitted, or a human-readable reason the
        bind must fail closed.
    """
    if security not in SECURITY_PROFILES:
        expected = ", ".join(sorted(SECURITY_PROFILES))
        return f"unknown security profile {security!r} (expected one of {expected})"
    if _is_loopback_tcp_endpoint(endpoint):
        return None
    # A non-loopback bind is permitted only under an explicit opt-in profile
    # (``local`` or ``tls``). The default ``loopback`` profile fails closed
    # rather than silently exposing the tree to the network.
    if security == SECURITY_LOOPBACK:
        return (
            f"refusing non-loopback tcp bind {endpoint!r}: the FBP transport is "
            "trusted only on loopback; pass security='local' (or 'tls') to opt in "
            "explicitly"
        )
    return None


def _ipc_path(endpoint: str) -> str | None:
    """Extract the filesystem path from an ``ipc://`` endpoint, or None."""
    if endpoint.startswith("ipc://"):
        return endpoint[len("ipc://") :]
    return None


def _set_ipc_socket_mode(path: str) -> None:
    """Apply the owner-only mode to a bound IPC socket file.

    The mode is applied only if the path exists (the socket was just bound).
    A failure to apply the mode is surfaced as an explicit error rather than
    silently leaving a world-reachable socket.
    """
    try:
        os.chmod(path, IPC_SOCKET_MODE)
    except OSError as exc:
        raise TransportSecurityError(
            f"could not set owner-only mode on ipc socket {path!r}: {exc}"
        ) from exc


def enforce_ipc_socket_mode(endpoint: str) -> str | None:
    """Apply and validate the owner-only mode on an ``ipc://`` socket.

    Args:
        endpoint: The full endpoint (e.g. ``ipc:///tmp/agent-centric-fbp-root``).

    Returns:
        ``None`` on success, or a fail-closed reason if the mode could not be
        applied.
    """
    path = _ipc_path(endpoint)
    if path is None:
        return f"not an ipc endpoint: {endpoint!r}"
    if not os.path.exists(path):
        # The socket file does not exist yet (bind has not happened). This is
        # not an error by itself; the caller applies the mode after binding.
        return None
    try:
        _set_ipc_socket_mode(path)
    except TransportSecurityError as exc:
        return str(exc)
    return None


def validate_ipc_socket_mode(endpoint: str) -> str | None:
    """Validate that an existing IPC socket has the owner-only mode.

    Args:
        endpoint: The full endpoint (e.g. ``ipc:///tmp/agent-panel-fbp-root``).

    Returns:
        ``None`` if the socket is owner-only (or absent), else a fail-closed
        reason.
    """
    path = _ipc_path(endpoint)
    if path is None:
        return f"not an ipc endpoint: {endpoint!r}"
    if not os.path.exists(path):
        return None
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError as exc:
        return f"cannot stat ipc socket {path!r}: {exc}"
    if mode & 0o077:
        return (
            f"ipc socket {path!r} is not owner-only (mode {oct(mode)}); "
            "refusing to trust it"
        )
    return None


def resolve_endpoint(endpoint: str, transport: str) -> str:
    """Resolve a channel endpoint against the configured transport.

    Mirrors ``Agent._endpoint``: a bare name is resolved against the transport,
    and an already-schemed endpoint is returned unchanged.
    """
    if "://" in endpoint:
        return endpoint
    return f"{transport}://{endpoint}"