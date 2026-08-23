# FBP Transport Trust Boundary

**Status:** largely enforced in code. §5.1 (trust-boundary switch), §5.3
(owner-only IPC sockets), and — new this session — §5.2 (mutual-TLS credential
config), §5.4 (per-peer authorization), and §5.5 (traffic integrity) are now
implemented in `fbp/transport.py` + `fbp/security.py` and wired into
`FbpDriver`/`Agent._spawn`, with tests in `tests/test_fbp_transport.py` and
`tests/test_fbp_security.py`. The `security.py` primitives are **opt-in and
default-off**; an operator who wants TLS/authn/intrinsity calls the explicit
driver arguments. See `fbp/security.py`.
**Scope:** the `agent_centric.fbp` directive/response protocol over its three
transports (`inproc://`, `tcp://`, `ipc://`).

This document defines what the FBP transport guarantees today, where its trust
boundary sits, and what hardening is required before it should be used across
a *real* trust boundary (i.e. anything other than localhost / a single
operator's machine).

---

## 1. The one-line posture

> The FBP protocol assumes **one local operator on one machine** (loopback). It
> is **not** a security boundary: there is no TLS, no authentication, and no
> authorization on the wire. Using `tcp://` or `ipc://` across a trust boundary
> (different machines, different users, an untrusted network) is **out of
> scope** until the hardening below is done.

Because the platform is fail-closed *by construction* (no verified success
without a deterministic check), a passive adversary can do little to corrupt a
result's *verified* status. But an **active** adversary who can reach the
transport can inject directives, read responses, or observe traffic. That is
the trust boundary: **the transport is trusted; everything on the other side
of it is untrusted.**

---

## 2. Transport matrix

| Transport | Trust model | Intended use | Notes |
|-----------|-------------|--------------|-------|
| `inproc://` | In-process only | Deterministic tests, embedded driver | No network; zero exposure. Always safe. |
| `ipc://` | Same host, same user (local filesystem sockets) | Local inter-process | Safe only if the local user is trusted and the socket files are not world-writable. |
| `tcp://` | Loopback `127.0.0.1` by default | Demo / local distribution | **Not** safe across a NIC / to a remote host without authn/z + encryption. |

The driver binds loopback by default and the landing server binds
`127.0.0.1` too — but that is a *default*, not a boundary.

---

## 3. Honest gaps (what is NOT enforced)

1. **No transport authentication.** The protocol does not require a client to
   prove who it is before sending directives.
2. **No authorization.** Every directive is processed the same regardless of
   caller identity; there are no per-peer grants on the wire.
3. **No encryption (TLS)** on `tcp://` or `ipc://` (IPC over Unix sockets is
   local, but not encrypted).
4. **Loopback is configured, not enforced.** Code can pass any host/port.

These are **by design for a local, single-operator dev/demo surface** — not a
gap that was accidentally left. They are the explicit cost of staying
dependency-free and local-first.

---

## 4. What this guarantees TODAY (the safe part)

- **In-process and loopback traffic only** — the default bindings never expose
  the protocol to a remote network.
- **Fail-closed application layer** — even if traffic were intercepted, the
  protocol validates every directive/response (version, frames, schema) and
  rejects malformed input explicitly. A tampered message becomes an audited
  error, not a silent verified success.
- **Deterministic replay** — a recorded session can be re-verified; the ledger
  is the ground truth, not the wire.

So: the *data integrity* of a result does not depend on transport secrecy; it
depends on the deterministic correctness spine. The transport boundary is
about **who may drive the tree** and **whether traffic is readable**.

---

## 5. Hardening needed before 1.0 / cross-host use

To move to a real trust boundary (recommended TOTI, in order of value):

1. **Document + enforce a trust-boundary switch.** ✅ **BUILT.** The driver
   (`FbpDriver(security=...)`) and `Agent._spawn` refuse a non-loopback `tcp://`
   bind unless the caller opts in to a security profile (`security="local"` or
   `"tls"`). The default profile `loopback` fails closed. See `fbp/transport.py`.
2. **Mutual TLS (or TLS + client auth) on `tcp://`.** ✅ **BUILT (config).**
   `TlsCreds`/`configure_tls` in `fbp/security.py` validate mutually-TLS material
   (cert + key + optional CA) and fail closed on incomplete material; the driver
   refuses a `security="tls"` bind without ready creds. A live cross-host
   deployment still supplies the actual PEM affs + a TLS-capable transport.
3. **IPC socket permissions** — ✅ **BUILT.** An `ipc://` socket is created
   with mode `0o600` (owner-only) after bind, and existing sockets are validated
   as owner-only before use. See `fbp/transport.py`.
4. **Per-peer authorization map.** ✅ **BUILT.** `PeerAuthz`/`PeerPolicy` in
   `fbp/security.py` map an authenticated peer to allowed directive kinds /
   subtree, enforced at the driver boundary via `FbpDriver(peer_autz=...)`
   (fail-closed for an unknown/unpermitted peer).
5. **Traffic integrity.** ✅ **BUILT.** `sign_payload`/`verify_payload`/
   `integrity_headers` in `fbp/security.py` produce and check an HMAC-SHA256
   over the canonical message so a frame cannot be replayed/ altered without the
   shared secret. Layered under real TLS this is handled by TLS itself.

All of §5.2/§5.4/§5.5 are implemented as **pure, offline-tested, opt-in**
primitives plus driver hooks. What crosses a real trust boundary still requires
the operator to supply actual certs/secrets and a TLS-capable transport; the
code no longer pretends these are unbuilt.

---

## 6. Standing rule for future work

- Do **not** claim the protocol is "secure for the network" anywhere in docs or
  code until TLS + authn are actually implemented and tested.
- Keep the FBP default loopback. Treat any non-loopback bind as an **explicit,
  audited opt-in** that prints a warning.
- A future agent should find this doc before reaching for a "transport
  security" change, and should implement the steps in §5, testing each.

---

*This document is authoritative for the FBP transport boundary. Keep it
current whenever the transport posture changes.*