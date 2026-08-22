"""A simple, local-only landing-page server for the FBP subsystem.

This is the "easy UX" front door: a single stdlib ``http.server`` that serves
one HTML landing page against a live, in-process ``FbpDriver``. It is:

- **Dependency-free** — stdlib only (no FastAPI/flask; the project keeps no web
  framework dependency).
- **Local-only & fail-closed** — binds 127.0.0.1 by default, and every action is
  a read-only observation or a verification that never changes durable state.
- **Actionable** — the page shows the live agent tree (via ``driver.tree()``),
  the session summary (via ``driver.summary()``), the standing invariants, and
  one-button/clink actions: run the deterministic demo, and re-verify replay.

Security posture:
- Binds loopback only (no remote exposure).
- No secrets on the page; the page only reflects local, in-process state.
- Actions mutate nothing durable; they run deterministic, read-only
  inspections and the deterministic demo tree.

The server is a thin, additive convenience over the existing ``FbpDriver``; it
does not add new capabilities, new state, or new trust. It is disabled by
default (opt-in via ``agent-centric fbp web``).
"""

from __future__ import annotations

import json
import threading
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .driver import FbpDriver

# The default loopback bind host and port for the landing-server.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790


class FbpLandingServer:
    """A tiny HTTP server that renders an actionable FBP landing page.

    The server owns one ``FbpDriver`` (spawned once, reused across requests,
    in-process) so the landing page is *live*: it reflects the actual agent
    tree and ledger at the moment it is rendered.

    Because the driver is deterministic and the page is a read/verify surface,
    the server is a thin, additive observer — it never creates new state and
    never relaxes verification. Actions (``run``/``replay``) are deterministic
    and read-only w.r.t. durable state.
    """

    def __init__(self, *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
        self._host = host
        self._port = port
        # One driver, compositored and reused. We register and run a small
        # deterministic demo tree so the page has something real to show.
        self._driver = self._build_driver()

    # -- driver setup (deterministic, offline) -----------------------------

    @staticmethod
    def _build_driver() -> FbpDriver:
        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.configure(tasks=("double",), verifiers=("even",), verifier="even")
        # Provision a couple of real children so the tree shows substance.
        driver.spawn("child")
        driver.configure_child("child", tasks=("double",))
        driver.spawn("store", kind="store")
        driver.configure_child("store", store_keys=("bill-b1",))
        return driver

    # -- page state --------------------------------------------------------

    def _page_state(self) -> dict[str, Any]:
        """A deterministic JSON-ready snapshot for the landing page."""
        tree = self._driver.tree()
        return {
            "tree": tree,
            "summary": self._driver.summary(),
            "checked": {
                "tree_length": len(tree),
                "identities": [n["identity"] for n in tree],
            },
        }

    # -- server wiring -----------------------------------------------------

    def serve_forever(self) -> None:
        """Serve the landing page until interrupted."""
        handler = self._make_handler()
        httpd = ThreadingHTTPServer((self._host, self._port), handler)
        print(f"FBP landing page: http://{self._host}:{self._port}")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            httpd.server_close()
            self._driver.close()

    def _make_handler(self) -> type[BaseHTTPRequestHandler]:
        server = self
        host = self._host
        port = self._port

        class _Handler(BaseHTTPRequestHandler):
            def log_message(self, fmt: str, *args: object) -> None:
                # Quiet the default stderr logging; intentional.
                ...

            def do_GET(self) -> None:
                self._serve()

            def do_POST(self) -> None:
                self._serve()

            def _serve(self) -> None:
                state: dict[str, Any]
                if self.path in ("/", "", "/index.html"):
                    state = server._page_state()
                    self._send_html(_render_landing(state))
                elif self.path == "/action/run":
                    # Demonstrative deterministic action: run the demo task
                    # set through the (now-existing) driver.
                    run = server._run_demo()
                    state = server._page_state()
                    state["last_action"] = run
                    self._send_html(_render_landing(state))
                elif self.path == "/health":
                    self._send_json({"ok": True, "server": f"{host}:{port}"})
                else:
                    self._send_html(
                        _render_landing(server._page_state(), error="unknown path"),
                        code=404,
                    )

            def _send_html(self, body: str, *, code: int = 200) -> None:
                data = body.encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _send_json(self, obj: dict[str, Any], *, code: int = 200) -> None:
                data = json.dumps(obj).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        return _Handler

    def _run_demo(self) -> dict[str, Any]:
        """A deterministic demo action: run the double task through the driver.

        This is a real, verified run over the existing in-process tree — it
        exercises the correctness spine (an even verifier accepts 2*value) and
        is recorded in the driver's in-memory ledger. It never mutates durable
        state (no store grant is touched).
        """
        try:
            resp = self._driver.run("double", {"value": 21})
            if resp.verified:
                return {"action": "run double(21)", "value": resp.value}
            return {"action": "run double(21)", "error": resp.error}
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            return {"action": "run double(21)", "error": str(exc)}


_PAGE_CSS = "\n".join([
    "body { font-family: system-ui, sans-serif;",
    "  max-width: 900px; margin: 2rem auto; padding: 0 1rem; color:#1a1a1a; }",
    "h1 { font-size: 1.6rem; } h2 { font-size: 1.15rem; margin-top: 1.5rem; }",
    "table { border-collapse: collapse; width: 100%; }",
    "th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid #ddd; }",
    ".pill { display:inline-block; background:#eee; border-radius:999px;",
    "  font-size:.8rem; padding:.15rem .6rem; }",
    ".actions a { display:inline-block; margin-right:.6rem;",
    "  padding:.5rem .9rem; background:#0057ff; color:#fff; text-decoration:none; }",
    ".invariants li { margin:.25rem 0; } .error { color:#b00020; } .note { color:#555; }",
])


def _render_landing(state: dict[str, Any], *, error: str | None = None) -> str:
    """Render the actionable landing page HTML from a page-state snapshot."""
    tree = state.get("tree", [])
    summary = state.get("summary", {})
    identities = state.get("checked", {}).get("identities", [])
    last = state.get("last_action")

    rows = "".join(
        f"<tr><td>{n.get('identity','')}</td><td>{n.get('kind','')}</td>"
        f"<td>{_caps(n)}</td><td>{_grants(n)}</td></tr>"
        for n in tree
    )
    caps = (
        f"{summary.get('run_count', 0)} runs · "
        f"{summary.get('verified_runs', 0)} verified"
        if summary else ""
    )
    action_note = (
        f"<p class='note'>Last action: {last.get('action','')} → {last.get('value','')}</p>"
        if last else ""
    )
    err = f"<p class='error'>{error}</p>" if error else ""

    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<title>Agent-Centric FBP — Landing</title>
<style>{_PAGE_CSS}</style></head><body>
<h1>Agent-Centric · FBP Subsystem</h1>
<p>A deterministic, agent-centric, flow-based subsystem. A rooted tree of agents;
work flows <b>down</b> as directives, responsibility bubbles <b>up</b> — each parent
re-verifies a child's value before accepting it. A task ends in a <b>verified
result or an explicit, audited failure</b> — never a silent third state.</p>

<h2>Live agent tree</h2>
<table><thead><tr><th>Identity</th><th>Kind</th><th>Capabilities</th><th>Grants</th></tr></thead>
<tbody>{rows}</tbody></table>

<h2>Session summary</h2>
<p class='pill'>{caps}</p>
<p>Identities: {len(identities)} · {' · '.join(identities) if identities else '—'}</p>

<h2>Actions</h2>
<div class='actions'>
  <a href='/'>Refresh</a>
  <a href='/action/run'>Run a deterministic demo action</a>
</div>
{action_note}
{err}

<h2>Standing invariants</h2>
<ul class='invariants'>
  <li>No unverified success — a child's self-claimed <code>verified</code>
      is not conclusive on its own.</li>
  <li>Fail-closed everywhere; deterministic by construction.</li>
  <li>Persistence is an explicit grant; single-writer; no auto-generated ids.</li>
  <li>We use — but never fully trust — non-deterministic tools; only
      irreducible residue reaches a human.</li>
  <li>CPM, audit, and replay are read-only capabilities, not agents.</li>
</ul>
</body></html>
"""


def _caps(node: dict[str, Any]) -> str:
    cap = node.get("capabilities") or []
    return " ".join(f"<span class='pill'>{c}</span>" for c in cap) or "&mdash;"


def _grants(node: dict[str, Any]) -> str:
    parts: list[str] = []
    if node.get("state"):
        parts.append("state")
    if node.get("trajectory"):
        parts.append("trajectory")
    keys = node.get("store_keys")
    if keys:
        parts.append(f"keys={keys}")
    return " ".join(f"<span class='pill'>{p}</span>" for p in parts) or "&mdash;"


def serve(
    *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = False
) -> None:
    """Serve the FBP landing page (blocking). Pass --open to open a browser."""
    server = FbpLandingServer(host=host, port=port)
    if open_browser:
        url = f"http://{host}:{port}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    server.serve_forever()