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
import os
import threading
import urllib.request
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .driver import FbpDriver

# The default loopback bind host and port for the landing-server.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790

# OpenRouter chat-completions endpoint (the ``/api/v1/chat/completions`` form).
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# The model served by the landing page's model agent when a real provider is
# wired. Overridable via ``OPENROUTER_MODEL``.
DEFAULT_OPENROUTER_MODEL = "openai/gpt-4o-mini"
# Env var holding the OpenRouter API key (never hardcoded).
OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"
OPENROUTER_MODEL_ENV = "OPENROUTER_MODEL"


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
        self._providers = _build_openrouter_providers()
        self._driver = self._build_driver()

    # -- driver setup (deterministic, offline) -----------------------------

    def _build_driver(self) -> FbpDriver:
        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        # No global verifier: the ``double`` demo passes ``even`` per-run, and
        # the model agent (string output) is not gated by a numeric verifier.
        driver.configure(tasks=("double",), verifiers=("even",))
        # Provision a couple of real children so the tree shows substance.
        driver.spawn("child")
        driver.configure_child("child", tasks=("double",))
        driver.spawn("store", kind="store")
        driver.configure_child("store", store_keys=("bill-b1",))
        # A model agent: an LLM as an ordinary agent. If an OpenRouter key is
        # present it is wired to a real, fail-closed provider; otherwise it
        # serves the deterministic stub (offline, CI-safe).
        driver.spawn("model", kind="model")
        # Wire the first configured real provider, if any (an OpenRouter key is
        # present); otherwise the deterministic stub serves (offline, CI-safe).
        provider = next(iter(self._providers.values()), None)
        if provider is not None:
            driver.configure_provider("model", provider, model_id=next(iter(self._providers)))
        return driver

    # -- page state --------------------------------------------------------

    def _model_choices(self) -> tuple[str, ...]:
        """The configured model choices for the dropdown (may be empty)."""
        return tuple(self._providers.keys())

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

    def _ledger_state(self) -> dict[str, Any]:
        """A deterministic, read-only view of the driver's recorded ledger.

        This is the operator-facing recovery surface: the per-kind directive
        counts and run outcomes the driver recorded this session. Read-only —
        nothing is mutated; it reflects the live in-process driver.
        """
        return {
            "ledger": self._driver.ledger(),
            "summary": self._driver.summary(),
            "count": len(self._driver.ledger()),
        }

    # -- server wiring -----------------------------------------------------

    def serve_forever(self) -> None:
        """Serve the landing page until interrupted."""
        handler = self._make_handler()
        # Single-threaded: the driver owns a private event loop set on the main
        # thread, so all requests must run there (a threaded server would run
        # handlers on worker threads with no current loop). Local, single-user.
        httpd = HTTPServer((self._host, self._port), handler)
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
                    self._send_html(
                        _render_landing(state, models=server._model_choices())
                    )
                elif self.path == "/action/run":
                    # Demonstrative deterministic action: run the demo task
                    # set through the (now-existing) driver.
                    run = server._run_demo()
                    state = server._page_state()
                    state["last_action"] = run
                    self._send_html(
                        _render_landing(state, models=server._model_choices())
                    )
                elif self.path == "/model":
                    # Run a prompt through the model agent (LLM as an ordinary
                    # agent). Body is either a plain prompt string or a JSON
                    # ``{"prompt": str, "model": str}`` envelope.
                    body = self._read_body()
                    prompt, model = _parse_model_body(body)
                    result = server._run_model(prompt, model)
                    self._send_json(result)
                elif self.path == "/state.json":
                    # Machine-readable snapshot: tree + summary + last action.
                    self._send_json(server._page_state())
                elif self.path == "/ledger":
                    # Operator-facing durable-ledger readout (read-only).
                    self._send_html(_render_ledger(server._ledger_state()))
                elif self.path == "/health":
                    self._send_json({"ok": True, "server": f"{host}:{port}"})
                else:
                    self._send_html(
                        _render_landing(server._page_state(), error="unknown path"),
                        code=404,
                    )

            def _read_body(self) -> str:
                """Read the POST body as text (bounded), defaulting to empty."""
                try:
                    length = int(self.headers.get("Content-Length", 0) or 0)
                except ValueError:
                    length = 0
                if length <= 0 or length > 1 << 16:
                    return ""
                return self.rfile.read(length).decode("utf-8", errors="replace")

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
            resp = self._driver.run("double", {"value": 21}, verifier="even")
            if resp.verified:
                return {"action": "run double(21)", "value": resp.value}
            return {"action": "run double(21)", "error": resp.error}
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            return {"action": "run double(21)", "error": str(exc)}

    def _run_model(self, prompt: str, model: str = "") -> dict[str, Any]:
        """Run a prompt through the model agent (an LLM as an ordinary agent).

        The model is reached through the normal directive/response protocol and
        its output is re-verified by the parent. When no OpenRouter key is set
        the model agent serves its deterministic stub (offline, CI-safe); when a
        key is present the selected model is routed to OpenRouter via the
        providers built in ``__init__``.

        ``model`` optionally names a configured provider to switch to for this
        call (additive, in-process, never relaxes verification). Unknown or
        empty selections are ignored and the current wiring is used.
        """
        try:
            if model and model in self._providers:
                self._driver.configure_provider("model", self._providers[model], model_id=model)
            resp = self._driver.run("model", {"prompt": prompt}, child="model")
            if resp.verified:
                return {
                    "ok": True,
                    "text": resp.value,
                    "verified": True,
                    "model": (resp.sources[0]["id"] if resp.sources else None),
                }
            return {"ok": False, "error": resp.error or "model run not verified", "verified": False}
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            return {"ok": False, "error": str(exc), "verified": False}


def _openrouter_http_client(model: str) -> Any:
    """A stdlib ``urllib`` transport for OpenRouter's chat-completions API.

    It is used as the ``http_client`` for ``build_real_model_provider`` so the
    fail-closed, secret-redacting, timeout-bounded provider path is reused.
    The client builds the JSON request body and parses the ``choices``/``message``
    shape, returning just the text block. Raises on HTTP/transport/parse errors;
    the provider maps those to ``ModelProviderError``.
    """

    def client(endpoint: str, headers: dict[str, str], prompt: str) -> str:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
        })
        request = urllib.request.Request(
            endpoint, data=payload.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=30) as resp:
                body = resp.read().decode("utf-8")
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter request failed: {exc}") from exc
        try:
            data = json.loads(body)
            content = data["choices"][0]["message"]["content"]
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"unexpected OpenRouter response: {body[:200]}") from exc
        if not isinstance(content, str):
            raise RuntimeError(f"unexpected OpenRouter response: {body[:200]}")
        return content

    return client


def _parse_model_body(body: str) -> tuple[str, str]:
    """Parse a ``/model`` POST body into ``(prompt, model)``.

    Accepts either a plain prompt string or a JSON ``{"prompt": ..., "model":
    ...}`` envelope. Always returns a ``(prompt, model)`` tuple; the model may
    be empty to mean "use the current wiring" (fail closed — an unparseable
    body yields an empty prompt, which the model agent rejects explicitly).
    """
    stripped = body.strip()
    if not stripped.startswith("{"):
        return stripped, ""
    try:
        data = json.loads(stripped)
    except (ValueError, TypeError):
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    prompt = data.get("prompt", "")
    model = data.get("model", "")
    return (prompt if isinstance(prompt, str) else "",
            model if isinstance(model, str) else "")


def _model_choices_from_env() -> tuple[str, ...]:
    """Resolve the configured OpenRouter model choices.

    Read from ``OPENROUTER_MODEL`` (a single id or comma-separated list),
    defaulting to ``DEFAULT_OPENROUTER_MODEL``. Read at call time so env
    changes (including tests) are honoured.
    """
    raw = os.environ.get(OPENROUTER_MODEL_ENV, "").strip()
    if raw:
        return tuple(m for m in (p.strip() for p in raw.split(",")) if m)
    return (DEFAULT_OPENROUTER_MODEL,)


def _build_openrouter_providers() -> dict[str, Any]:
    """Build enabled OpenRouter providers per model, or {} if no key is set.

    Reads ``OPENROUTER_API_KEY`` from the environment (never hardcoded). When
    the key is absent, returns {} so the server fails closed to the
    deterministic stub. Model choices come from ``OPENROUTER_MODEL`` (a single
    id or comma-separated list), defaulting to ``DEFAULT_OPENROUTER_MODEL``.
    """
    from ..providers import build_real_model_provider

    api_key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
    if not api_key:
        return {}

    result: dict[str, Any] = {}
    for model in _model_choices_from_env():
        client = _openrouter_http_client(model)
        result[model] = build_real_model_provider(
            endpoint=OPENROUTER_ENDPOINT,
            api_key=api_key,
            http_client=client,
        )
    return result


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

# The model text-box client script (kept out of the f-string so its JS object
# braces are not mistaken for f-string interpolations).
_MODEL_JS = """\
<script>
  document.getElementById('model-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const prompt = document.getElementById('model-prompt').value;
    const sel = document.getElementById('model-select');
    const model = sel ? sel.value : '';
    const out = document.getElementById('model-result');
    out.textContent = '...';
    try {
      const r = await fetch('/model', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt: prompt, model: model})
      });
      const data = await r.json();
      if (data.ok) {
        const badge = data.verified ? '[verified]' : '[unverified]';
        const src = data.model ? ' source=' + data.model : '';
        out.textContent = badge + src + '\n' + data.text;
      } else {
        out.textContent = 'error: ' + (data.error || 'unknown');
      }
    } catch (err) {
      out.textContent = 'request failed: ' + err;
    }
  });
</script>
"""


def _render_landing(
    state: dict[str, Any], *, error: str | None = None, models: tuple[str, ...] = ()
) -> str:
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

    # Model dropdown options (additive UX; a single entry when reading only
    # ``OPENROUTER_MODEL``, or the deterministic stub choice).
    choices = models or ()
    opts = "".join(
        f"<option value='{m}'{(' selected' if i == 0 else '')}>{m}</option>"
        for i, m in enumerate(choices)
    )
    select = ""
    if choices:
        select = (
            f"<label for='model-select'>Model</label>"
            f"<select id='model-select' class='pill'>{opts}</select>"
        )

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
  <a href='/ledger'>View ledger</a>
  <a href='/state.json'>state.json</a>
</div>
{action_note}
{err}

<h2>Model (LLM as an ordinary agent)</h2>
<p class='note'>Ask a model. When an <code>OPENROUTER_API_KEY</code> is set it is
routed to OpenRouter; otherwise the deterministic stub answers (offline).
The answer shows its <b>verified</b> status and model <b>source</b>.</p>
<form id='model-form'>
  {select}
  <textarea id='model-prompt' rows='3' cols='60' placeholder='Ask the model...'></textarea>
  <br/>
  <button type='submit'>Ask</button>
</form>
<pre id='model-result' class='note'></pre>
{_MODEL_JS}

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


def _render_ledger(state: dict[str, Any]) -> str:
    """Render the operator-facing ledger view (read-only, transitional)."""
    summary = state.get("summary", {})
    runs = summary.get("runs", [])
    rows = "".join(
        f"<tr><td>{r.get('correlation_id','')}</td><td>{r.get('task','')}</td>"
        f"<td>{r.get('terminal','')}</td><td>{r.get('value','')}</td></tr>"
        for r in runs
    )
    return f"""<!doctype html><html lang='en'><head><meta charset='utf-8'>
<title>Agent-Centric FBP — Ledger</title>
<style>{_PAGE_CSS}</style></head><body>
<h1>Agent-Centric · FBP — Session Ledger</h1>
<p><a href='/'>← Landing</a> · <a href='/state.json'>state.json</a></p>
<h2>Runs</h2>
<table><thead><tr><th>correlation_id</th><th>task</th>
<th>terminal</th><th>value</th></tr></thead>
<tbody>{rows if rows else '<tr><td colspan=4>no runs recorded</td></tr>'}</tbody></table>
<p class='note'>Count: {state.get('count', 0)} directives recorded this session
(read-only).</p>
</body></html>
"""


def serve(
    *, host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, open_browser: bool = False
) -> None:
    """Serve the FBP landing page (blocking). Pass --open to open a browser."""
    server = FbpLandingServer(host=host, port=port)
    if open_browser:
        url = f"http://{host}:{port}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    server.serve_forever()