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
import queue
import threading
import urllib.request
import webbrowser
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from .chatstore import ChatHistoryStore
from .driver import FbpDriver

# The default loopback bind host and port for the landing-server.
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790

# Upper bound on in-page chat history turns (a small, bounded transcript).
MAX_HISTORY_TURNS = 100

# OpenRouter chat-completions endpoint (the ``/api/v1/chat/completions`` form).
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
# The model served by the landing page's model agent when a real provider is
# wired. Overridable via ``OPENROUTER_MODEL``.
DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4-flash-0731"
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

    def __init__(
        self,
        *,
        host: str = DEFAULT_HOST,
        port: int = DEFAULT_PORT,
        history_path: str | os.PathLike[str] | None = None,
        networks_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        # One driver, in-process and reused. We register and run a small
        # deterministic demo tree so the page has something real to show.
        self._providers = _build_openrouter_providers()
        self._driver = self._build_driver()
        # Saved component networks (name -> to_dict payload). In-memory by
        # default; persisted to ``networks_path`` when granted (explicit grant).
        self._networks: dict[str, Any] = {}
        self._networks_path = str(networks_path) if networks_path is not None else None
        if self._networks_path:
            self._load_networks()
        # In-page chat history (bounded, per-session in-memory). When a durable
        # path is granted it is ALSO persisted (explicit grant: nothing is
        # written unless a caller opts in via ``history_path``).
        self._history: list[dict[str, Any]] = []
        self._history_lock = threading.Lock()
        self._history_store: ChatHistoryStore | None = None
        if history_path is not None:
            try:
                store = ChatHistoryStore(str(history_path))
                store.open()
                self._history = list(store.all())
                self._history_store = store
            except Exception as exc:  # noqa: BLE001 - fail closed on the UX path
                # A granted-but-unreadable history must not crash the page;
                # degrade to the in-memory transcript and say so on the page.
                self._history_store = None
                print(f"fbp-web: durable chat history unavailable: {exc}")

    # -- chat history (in-page, bounded, per-session) -----------------------

    def _append_history(self, entry: dict[str, Any]) -> None:
        """Append a turn to the in-page chat history, bound to MAX_HISTORY_TURNS.

        Entries are ``{"role": "user"|"assistant", "content", "model"?,
        "verified"?, "error"?}``. The transcript is in-memory only (per server
        session) unless a durable history path was granted at construction, in
        which case each turn is ALSO appended to the on-disk store so the log
        survives restarts.
        """
        with self._history_lock:
            role = entry.get("role") or "assistant"
            content = entry.get("content", "")
            if self._history_store is not None:
                try:
                    self._history_store.append(
                        role=role,
                        content=content,
                        model=(entry.get("model") or None),
                        verified=(True if entry.get("verified") is True else
                                  False if entry.get("verified") is False else None),
                        error=(entry.get("error") or None),
                    )
                except Exception as exc:  # noqa: BLE001 - never break the page
                    print(f"fbp-web: durable chat-history append failed: {exc}")
            self._history.append(entry)
            if len(self._history) > MAX_HISTORY_TURNS:
                del self._history[: len(self._history) - MAX_HISTORY_TURNS]

    def _clear_history(self) -> None:
        if self._history_store is not None:
            try:
                self._history_store.clear()
            except Exception as exc:  # noqa: BLE001 - never break the page
                print(f"fbp-web: durable chat-history clear failed: {exc}")
        with self._history_lock:
            self._history.clear()

    def _history_state(self) -> dict[str, Any]:
        with self._history_lock:
            return {"history": [dict(h) for h in self._history],
                    "durable": self._history_store is not None}

    # -- saved component networks (explicit-grant persistence) --------------

    def _load_networks(self) -> None:
        """Load saved networks from the granted path (fail-closed)."""
        if not self._networks_path:
            return
        import os

        if not os.path.exists(self._networks_path):
            return
        try:
            with open(self._networks_path, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                self._networks = dict(data)
        except (OSError, ValueError) as exc:
            print(f"fbp-web: could not load saved networks: {exc}")

    def _save_networks(self) -> None:
        """Persist saved networks to the granted path (atomic write)."""
        if not self._networks_path:
            return
        import os
        import tempfile

        directory = os.path.dirname(os.path.abspath(self._networks_path)) or "."
        os.makedirs(directory, exist_ok=True)
        (fd, tmp_path) = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self._networks, fh, sort_keys=True)
            os.replace(tmp_path, self._networks_path)
        except BaseException:
            from contextlib import suppress

            with suppress(OSError):
                os.unlink(tmp_path)
            raise

    def _network_save(self, body: str) -> dict[str, Any]:
        """Save a named component network (explicit grant; fail-closed).

        Body is ``{"name": str, "network": {...to_dict payload...}}``. The
        network is validated before it is stored (a malformed network is never
        persisted).
        """
        from .network import network_from_dict

        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"invalid payload: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "payload must be a JSON object"}
        name = data.get("name")
        network = data.get("network")
        if not isinstance(name, str) or not name.strip():
            return {"ok": False, "error": "save requires a 'name' string"}
        if not isinstance(network, dict):
            return {"ok": False, "error": "save requires a 'network' object"}
        try:
            network_from_dict(network).validate()
        except Exception as exc:  # noqa: BLE001 - fail closed
            return {"ok": False, "error": f"network invalid: {exc}"}
        self._networks[name.strip()] = network
        self._save_networks()
        return {"ok": True, "saved": name.strip()}

    def _network_list(self) -> dict[str, Any]:
        """List saved network names (read-only)."""
        return {"names": sorted(self._networks.keys())}

    def _network_load(self, name: str) -> dict[str, Any]:
        """Load a saved network by name (read-only; fail-closed if unknown)."""
        net = self._networks.get(name)
        if net is None:
            return {"ok": False, "error": f"no saved network named {name!r}"}
        return {"ok": True, "network": net}

    # -- driver setup (deterministic, offline) -----------------------------

    def _build_driver(self) -> FbpDriver:
        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.register("sum", lambda a, b: a + b, source_url="file:///tasks/sum")
        # No global verifier: the ``double`` demo passes ``even`` per-run, and
        # the model agent (string output) is not gated by a numeric verifier.
        driver.configure(tasks=("double", "even", "odd", "sum"), verifiers=("even", "odd"))
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
                    # ``{"prompt": str, "model": str}`` envelope. This is the
                    # audited path (goes through the driver's correctness spine).
                    body = self._read_body()
                    prompt, model = _parse_model_body(body)
                    result = server._run_model(prompt, model)
                    self._send_json(result)
                elif self.path == "/model/stream":
                    # Streaming preview: SSE over POST so the model's tokens
                    # appear as they arrive (real-time UX). This bypasses the
                    # driver round-trip and reports the result honestly as an
                    # unverified preview; the verified path remains /model.
                    body = self._read_body()
                    prompt, model = _parse_model_body(body)
                    self._serve_sse_stream(server, prompt, model)
                elif self.path == "/orchestrate":
                    # The chat-window → FBP seam: a JSON artifact (task or
                    # steps, or a typed intent) is validated/canonicalized, then
                    # run as an FBP plan through the driver's verified spine.
                    body = self._read_body()
                    result = server._run_artifact(body)
                    self._send_json(result)
                elif self.path == "/orchestrate/schema":
                    # The typed-intent schema registry (schema-driven
                    # orchestration): the UI renders a typed form from it.
                    from .orchestrate import SCHEMAS

                    self._send_json({"schemas": SCHEMAS})
                elif self.path == "/network":
                    # Component Networks: a visual-programming graph payload
                    # (components + edges) compiles to an ordered FBP plan and
                    # runs through the verified spine.
                    body = self._read_body()
                    result = server._run_network(body)
                    self._send_json(result)
                elif self.path == "/network/save":
                    body = self._read_body()
                    self._send_json(server._network_save(body))
                elif self.path == "/network/list":
                    self._send_json(server._network_list())
                elif self.path.startswith("/network/load/"):
                    name = self.path[len("/network/load/"):]
                    self._send_json(server._network_load(name))
                elif self.path == "/history":
                    # In-page chat history (per-session, bounded).
                    self._send_json(server._history_state())
                elif self.path == "/history/clear":
                    server._clear_history()
                    self._send_json({"ok": True})
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

            def _serve_sse_stream(
                self,
                srver: FbpLandingServer,
                prompt: str,
                model: str,
            ) -> None:
                """Stream a model response to the browser as Server-Sent Events.

                Writes ``data: <json>\n\n`` events: a ``meta`` event, live
                ``chunk`` events, and a final ``done`` (or ``error``). The whole
                stream is written on the handler thread; the model runs in a
                background thread, so tokens can be emitted as they arrive.
                """
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "keep-alive")
                self.end_headers()
                full_text = ""
                result_model = model
                errored = False
                try:
                    for event in srver._stream_model(prompt, model):
                        if event.get("type") == "done":
                            full_text = event.get("text", "")
                            result_model = event.get("model", result_model)
                        elif event.get("type") == "error":
                            errored = True
                        self.wfile.write(
                            ("data: " + json.dumps(event) + "\n\n").encode("utf-8")
                        )
                        self.wfile.flush()
                except (BrokenPipeError, ConnectionAbortedError):
                    # Client disconnected mid-stream; stop without crashing the
                    # poll loop. The streaming result (if any) is not recorded.
                    return
                if not errored:
                    srver._record_model_turn(
                        prompt,
                        {
                            "ok": True,
                            "text": full_text,
                            "verified": False,
                            "model": result_model,
                        },
                    )
                else:
                    srver._record_model_turn(
                        prompt,
                        {"ok": False, "error": "stream failed", "verified": False},
                    )

        return _Handler

    def _run_artifact(self, body: str) -> dict[str, Any]:
        """Run a chat artifact through the post-return determinism pipeline t
   o an FBP plan.

        Body is a JSON artifact (``{"task": ...}`` or ``{"steps": [...]}``).
        It is schema-validated and canonicalized (fail-closed), then mapped to
        an ordered ``run`` plan executed through the driver's spine (every step
        is a normal directive: ledgered, parent re-verified, replayable).
        """
        from .chat_pipeline import canonicalize, parse_json_object
        from .orchestrate import run_artifact_plan

        try:
            artifact = parse_json_object(body)
            # Canonical form before it may drive state (deterministic order).
            artifact = canonicalize(artifact)
        except ValueError as exc:
            return {
                "ok": False,
                "results": [],
                "completed": 0,
                "error": f"artifact rejected: {exc}",
            }
        return run_artifact_plan(self._driver, artifact)

    def _run_network(self, body: str) -> dict[str, Any]:
        """Run a component network (visual-programming payload) as an FBP plan.

        Body is a JSON ``to_dict()``-shaped network (``components`` + ``edges``).
        It is reconstructed, validated (DAG, known refs), compiled to an ordered
        FBP ``run`` plan, and executed through the driver's verified spine.
        Fail-closed: a malformed or cyclic network returns ``ok=False``.
        """
        from .network import network_from_dict, run_network

        try:
            import json as _json

            data = _json.loads(body)
            if not isinstance(data, dict):
                return {"ok": False, "results": [], "completed": 0,
                        "error": "network payload must be a JSON object"}
            network = network_from_dict(data)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "results": [], "completed": 0,
                    "error": f"network rejected: {exc}"}
        return run_network(self._driver, network)

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

    def _build_chat_context(self, max_turns: int = 8) -> str:
        """A deterministic, bounded transcript prefix for the next model prompt.

        Turns are the (possibly durable) in-page history, oldest first. Injecting
        them as context lets the model answer with continuity (chat-context), but
        the prefix is ordinary text built from recorded turns — deterministic and
        auditable. Only ``max_turns`` of the most recent turns are included so the
        prompt stays bounded.
        """
        with self._history_lock:
            recent = self._history[-max_turns:] if max_turns else []
        parts: list[str] = []
        for t in recent:
            role = "you" if t.get("role") == "user" else "model"
            content = (t.get("content") or "").strip()
            if content:
                parts.append(f"{role}: {content}")
        return "\n".join(parts)

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

        If the in-page transcript has prior turns, they are folded in as
        deterministic chat context (oldest-first, bounded) so the model answers
        with continuity.
        """
        try:
            if model and model in self._providers:
                self._driver.configure_provider("model", self._providers[model], model_id=model)
            full_prompt = prompt
            context = self._build_chat_context()
            if context:
                # Deterministic transcript prefix -> continuity for the model.
                full_prompt = f"{context}\n--\n{full_prompt}"
            resp = self._driver.run("model", {"prompt": full_prompt}, child="model")
            if resp.verified:
                result = {
                    "ok": True,
                    "text": resp.value,
                    "verified": True,
                    "model": (resp.sources[0]["id"] if resp.sources else None),
                }
            else:
                result = {
                    "ok": False,
                    "error": resp.error or "model run not verified",
                    "verified": False,
                }
            self._record_model_turn(prompt, result)
            return result
        except Exception as exc:  # noqa: BLE001 - surfaced to the page
            result = {"ok": False, "error": str(exc), "verified": False}
            self._record_model_turn(prompt, result)
            return result

    def _record_model_turn(self, prompt: str, result: dict[str, Any]) -> None:
        """Record a completed model turn into the in-page chat history.

        Always records the user turn; records the assistant answer (or the
        error) so the user sees the outcome in the transcript.
        """
        self._append_history({"role": "user", "content": prompt, "model": result.get("model")})
        if result.get("ok"):
            self._append_history({
                "role": "assistant",
                "content": result.get("text", ""),
                "model": result.get("model"),
                "verified": result.get("verified"),
            })
        else:
            self._append_history({
                "role": "assistant",
                "content": "",
                "model": result.get("model"),
                "verified": False,
                "error": result.get("error") or "unknown error",
            })

    def _stream_model(self, prompt: str, model: str) -> Iterator[dict[str, Any]]:
        """Run a model request with progressive token delivery.

        This is a *streaming preview* surface for the landing page: it runs the
        model directly (in a background thread) and emits real token chunks to
        the SSE handler via a queue as they arrive. It does **not** go through
        the driver's directive/response round-trip, so its output is reported
        honestly as an unverified preview (the audited, verified path remains
        the synchronous ``/model`` route). When no ``OPENROUTER_API_KEY`` is set
        it fails closed to the deterministic stub.

        A generator of stream events: ``{type: meta, model}``, then
        ``{type: chunk, text}`` events, then ``{type: done, text, model,
        verified: False}`` (or ``{type: error, error}`` on failure). The caller
        (SSE handler) serialises them for the browser and, on completion,
        records the turn in chat history. Prior transcript turns are folded in
        as deterministic chat context (same as the audited ``/model`` path).
        """
        resolved_model = model or (next(iter(self._providers), None) or "stub-model")
        working_prompt = prompt
        context = self._build_chat_context()
        if context:
            working_prompt = f"{context}\n--\n{working_prompt}"

        api_key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
        if not api_key or resolved_model == "stub-model":
            # Deterministic stub (offline, CI-safe) — emit progressively.
            text = _stub_model_text(working_prompt)
            yield {"type": "meta", "model": "stub-model"}
            for chunk in _stream_chunks(text):
                yield {"type": "chunk", "text": chunk}
            yield {"type": "done", "text": text, "model": "stub-model", "verified": False}
            return

        out: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)
        chunks: list[str] = []

        def _worker() -> None:
            try:
                client = _openrouter_http_client_stream(
                    resolved_model,
                    on_chunk=lambda c: out.put(("chunk", c)),
                )
                full = client(
                    OPENROUTER_ENDPOINT,
                    {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
                    working_prompt,
                )
                chunks.append(full)
                out.put(("done", full))
            except Exception as exc:  # noqa: BLE001 - surfaced as an SSE error
                out.put(("error", f"{exc}"))

        threading.Thread(target=_worker, daemon=True).start()
        yield {"type": "meta", "model": resolved_model}
        while True:
            tag, value = out.get()
            if tag == "error":
                yield {"type": "error", "error": value}
                return
            if tag == "done":
                full = value
                if not chunks:  # no live chunks (e.g. empty reply); fall back to full
                    yield {"type": "chunk", "text": full}
                yield {"type": "done", "text": full, "model": resolved_model, "verified": False}
                return
            # tag == "chunk": a real token chunk
            yield {"type": "chunk", "text": value}
            chunks.append(value)


def _stub_model_text(prompt: str) -> str:
    """The deterministic offline stub response (mirrors the model agent stub)."""
    return f"stub response to: {prompt[:80]}"


def _stream_chunks(text: str, size: int = 32) -> list[str]:
    """Split ``text`` into progressive word/prefix-sized chunks for the SSE UI.

    A simple, deterministic progressive renderer: emits the text in words so
    the browser shows the answer appearing incrementally, until the final
    ``done`` carries the full text.
    """
    if not text:
        return []
    words = text.split(" ")
    out: list[str] = []
    buffer = ""
    for w in words:
        cand = f"{buffer} {w}" if buffer else w
        if len(cand) >= size or w == words[-1]:
            out.append(cand)
            buffer = ""
        else:
            buffer = cand
    if buffer:
        out.append(buffer)
    return out


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


def _openrouter_http_client_stream(model: str, on_chunk: Any) -> Any:
    """A stdlib ``urllib`` transport that streams OpenRouter tokens incrementally.

    It posts a chat-completions request with ``"stream": True``, reads the
    response line-by-line (OpenRouter sends Server-Sent Events: ``data: {...}``
    with ``choices[0].delta.content``), and calls ``on_chunk(text)`` for each
    content delta. Returns the concatenated full text.

    ``on_chunk`` receives ``str`` chunks as they arrive. The final ``data:
    [DONE]`` line ends the loop. An OpenRouter error line (``data:
    {"error": ...}``) raises ``RuntimeError``. Raises on HTTP/transport/parse
    errors; the caller surfaces them (a streaming failure is an explicit,
    visible error, never a silent gap).
    """

    def client(endpoint: str, headers: dict[str, str], prompt: str) -> str:
        payload = json.dumps({
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": True,
        })
        request = urllib.request.Request(
            endpoint, data=payload.encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as resp:
                full: list[str] = []
                for raw in resp:
                    line = raw.decode("utf-8", errors="replace").strip()
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError as exc:
                        raise RuntimeError(f"unexpected SSE line: {line[:200]}") from exc
                    if isinstance(obj, dict) and obj.get("error"):
                        err = obj["error"]
                        raise RuntimeError(f"OpenRouter error: {err}")
                    try:
                        delta = obj["choices"][0]["delta"].get("content")
                    except (KeyError, IndexError, TypeError):
                        continue
                    if delta:
                        on_chunk(str(delta))
                        full.append(str(delta))
                return "".join(full)
        except urllib.error.URLError as exc:
            raise RuntimeError(f"OpenRouter stream request failed: {exc}") from exc

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
    ".spinner { display:inline-block; width:1rem; height:1rem; border:2px solid #ccc;",
    "  border-top-color:#0057ff; border-radius:50%; animation:spin .6s linear infinite;",
    "  vertical-align:middle; margin-left:.5rem; }",
    "@keyframes spin { to { transform: rotate(360deg); } }",
    "#model-result { white-space:pre-wrap; }",
    ".chat-history { border:1px solid #ddd; border-radius:6px; padding:.5rem .8rem;",
    "  margin:.5rem 0 1rem; max-height:18rem; overflow:auto; }",
    ".chat-turn { padding:.35rem 0; border-bottom:1px solid #f0f0f0; }",
    ".chat-turn:last-child { border-bottom:none; }",
    ".net-node { display:inline-block; background:#e8f0fe; border:1px solid #b6cdf5;",
    "  border-radius:6px; padding:.3rem .7rem; margin:.25rem; font-family:monospace; }",
    ".net-edge { color:#555; font-family:monospace; font-size:.85rem;",
    "  margin:.15rem 0 .15rem 1rem; }",
    ".net-panel { border:1px solid #ddd; border-radius:6px; padding:.6rem;",
    "  margin:.5rem 0; }",
    ".net-canvas { position:relative; border:1px dashed #bbb; border-radius:6px;",
    "  background:#fafbfc; margin:.4rem 0; overflow:hidden; }",
    ".net-node-el { position:absolute; background:#e8f0fe; border:1px solid #5b8def;",
    "  border-radius:6px; padding:.3rem .5rem; font-family:monospace; font-size:.85rem;",
    "  cursor:grab; user-select:none; min-width:90px; }",
    ".net-node-el .net-port { display:inline-block; width:10px; height:10px;",
    "  border-radius:50%; background:#5b8def; margin-right:.3rem; cursor:crosshair;",
    "  vertical-align:middle; }",
    ".net-node-el .net-port.in { background:#22a06b; }",
    ".net-svg { display:block; }",
    ".mode-switch { display:flex; gap:.5rem; margin:1.2rem 0 .8rem; }",
    ".mode-btn { flex:1; padding:.6rem; border:1px solid #ccc; border-radius:8px;",
    "  background:#f5f6f8; cursor:pointer; font-size:1rem; }",
    ".mode-btn.active { background:#0057ff; color:#fff; border-color:#0057ff; }",
    ".card { background:#fff; border:1px solid #e2e4e8; border-radius:12px;",
    "  box-shadow:0 1px 3px rgba(0,0,0,.06); padding:1rem 1.2rem; margin:1rem 0; }",
    ".card h2 { margin-top:0; }",
    ".card-grid { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }",
    "@media (max-width:760px){ .card-grid { grid-template-columns:1fr; } }",
    ".card .note { margin-top:.2rem; }",
    ".schema-field { margin:.4rem 0; }",
    ".schema-field label { display:block; font-size:.85rem; color:#333; margin-bottom:.15rem; }",
    ".schema-field input { width:60%; padding:.35rem .5rem; border:1px solid #ccc;",
    "  border-radius:6px; font-family:monospace; }",
])

# The model text-box client script (kept out of the f-string so its JS object
# braces are not mistaken for f-string interpolations).
_MODEL_JS = r"""\
<script>
  const $modelOut = () => document.getElementById('model-result');
  const $modelSpin = () => document.getElementById('model-spinner');
  const $modelBtn = () => document.getElementById('model-ask');

  async function loadHistory() {
    try {
      const r = await fetch('/history');
      const data = await r.json();
      renderHistory(data.history || []);
    } catch (e) { /* history is best-effort */ }
  }

  function renderHistory(history) {
    const box = document.getElementById('chat-history');
    if (!box) return;
    box.innerHTML = '';
    for (const turn of history) {
      const div = document.createElement('div');
      div.className = 'chat-turn';
      if (turn.role === 'user') {
        div.innerHTML = '<b>You:</b> ' + esc(turn.content);
      } else if (turn.error) {
        div.innerHTML = '<b>Model:</b> <span class=\'error\'>' + esc(turn.error) + '</span>';
      } else {
        const badge = turn.verified ? '[verified]' : '[unverified]';
        const src = turn.model ? ' source=' + turn.model : '';
        div.innerHTML = '<b>Model (' + esc(badge + src) + '):</b> ' + esc(turn.content);
      }
      box.appendChild(div);
    }
  }

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  document.getElementById('model-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    const prompt = document.getElementById('model-prompt').value;
    const sel = document.getElementById('model-select');
    const model = sel ? sel.value : '';
    const out = $modelOut();
    const spin = $modelSpin();
    const btn = $modelBtn();
    out.textContent = ''; out.className = 'note';
    if (spin) spin.style.display = 'inline-block';
    if (btn) btn.disabled = true;
    let full = ''; let resultModel = model; let failed = '';
    try {
      const r = await fetch('/model/stream', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({prompt: prompt, model: model})
      });
      if (!r.ok) {
        out.textContent = 'request failed: HTTP ' + r.status;
        out.className='error'; failed='HTTP '+r.status;
      }
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      while (true) {
        const {done, value} = await reader.read();
        if (done) break;
        buf += dec.decode(value, {stream: true});
        let idx;
        while ((idx = buf.indexOf('\n\n')) !== -1) {
          const block = buf.slice(0, idx); buf = buf.slice(idx + 2);
          for (const line of block.split('\n')) {
            if (!line.startsWith('data: ')) continue;
            const evt = JSON.parse(line.slice(6));
            if (evt.type === 'chunk') {
              full += evt.text; out.textContent = full;
            }
            else if (evt.type === 'meta') { resultModel = evt.model || model; }
            else if (evt.type === 'done') {
              resultModel = evt.model || resultModel;
              out.textContent = full || evt.text;
            }
            else if (evt.type === 'error') {
              failed = evt.error || 'stream error';
              out.textContent = 'error: ' + failed; out.className='error';
            }
          }
        }
      }
    } catch (err) {
      failed = String(err); out.textContent = 'request failed: ' + err; out.className='error';
    } finally {
      if (spin) spin.style.display = 'none';
      if (btn) btn.disabled = false;
      loadHistory();
    }
  });

  document.getElementById('model-prompt').addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      document.getElementById('model-form').requestSubmit();
    }
  });

  document.getElementById('history-clear').addEventListener('click', async () => {
    await fetch('/history/clear', {method: 'POST'});
    loadHistory();
    const out = $modelOut(); out.textContent=''; out.className='note';
  });

  loadHistory();
</script>
"""

# The orchestrate box client script (kept out of the f-string so its JS object
# braces are not mistaken for f-string interpolations).
_ORCHESTRATE_JS = r"""\
<script>
  document.getElementById('orch-run').addEventListener('click', async () => {
    const ta = document.getElementById('orch-prompt');
    const out = document.getElementById('orch-result');
    const spin = document.getElementById('orch-spinner');
    const btn = document.getElementById('orch-run');
    out.textContent = ''; out.className = 'note';
    if (spin) spin.style.display = 'inline-block';
    if (btn) btn.disabled = true;
    try {
      const r = await fetch('/orchestrate', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: ta.value
      });
      const data = await r.json();
      if (!r.ok) {
        out.textContent = 'HTTP ' + r.status + ': ' + JSON.stringify(data);
        out.className='error'; return;
      }
      if (data.ok) {
        let lines = data.results.map((s) =>
          (s.verified ? '✔' : '✘') + ' step ' + s.step + ' ' + s.task +
          (s.value !== undefined ? ' = ' + JSON.stringify(s.value) : '') +
          (s.error ? ' error=' + s.error : '')
        ).join('\n');
        out.textContent = 'FBP plan ok (' + data.completed + ' step(s)):\n' + lines;
      } else {
        out.textContent = 'FBP plan FAILED: ' + (data.error || 'unverified step') +
          ' (completed ' + (data.completed || 0) + ')';
        out.className = 'error';
      }
    } catch (err) {
      out.textContent = 'request failed: ' + err; out.className='error';
    } finally {
      if (spin) spin.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  });
</script>
"""

# The schema-driven orchestration client script: a typed form rendered from the
# intent schema registry (`/orchestrate/schema`), submitting a typed artifact.
_SCHEMA_JS = r"""\
<script>
  let schemaData = {};

  async function schemaLoad() {
    try {
      const r = await fetch('/orchestrate/schema');
      const data = await r.json();
      schemaData = (data && data.schemas) || {};
    } catch (e) { schemaData = {}; }
    schemaRenderIntents();
  }

  function schemaRenderIntents() {
    const sel = document.getElementById('schema-intent');
    if (!sel) return;
    sel.innerHTML = '';
    for (const name of Object.keys(schemaData)) {
      const o = document.createElement('option'); o.value = name; o.textContent = name;
      sel.appendChild(o);
    }
    if (sel.options.length) schemaRenderFields();
  }

  function schemaTypeLabel(type) {
    return type === 'int' ? 'integer' : (type === 'float' ? 'decimal' : type);
  }

  function schemaRenderFields() {
    const sel = document.getElementById('schema-intent');
    const box = document.getElementById('schema-fields');
    if (!sel || !box) return;
    const schema = schemaData[sel.value] || {};
    const fields = schema.fields || {};
    box.innerHTML = '';
    for (const [name, spec] of Object.entries(fields)) {
      if (name === 'intent') continue;
      const req = spec.required ? ' (required)' : '';
      const wrap = document.createElement('div');
      wrap.className = 'schema-field';
      wrap.innerHTML = '<label>' + esc(name + ' (' +
        schemaTypeLabel(spec.type || 'str') + ')' + req + ')</label>';
      const input = document.createElement('input');
      input.type = 'text';
      input.id = 'schema-f-' + name;
      if (spec.default !== undefined) input.value = spec.default;
      input.placeholder = spec.type || 'str';
      wrap.appendChild(input);
      box.appendChild(wrap);
    }
  }

  async function schemaRun() {
    const sel = document.getElementById('schema-intent');
    const out = document.getElementById('schema-result');
    const spin = document.getElementById('schema-spinner');
    const btn = document.getElementById('schema-run');
    out.textContent = ''; out.className = 'note';
    if (spin) spin.style.display = 'inline-block';
    if (btn) btn.disabled = true;
    const schema = (schemaData[sel.value] || {}).fields || {};
    const artifact = {intent: sel.value};
    for (const name of Object.keys(schema)) {
      if (name === 'intent') continue;
      artifact[name] = document.getElementById('schema-f-' + name).value;
    }
    try {
      const r = await fetch('/orchestrate', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(artifact)
      });
      const data = await r.json();
      if (data.ok) {
        let lines = data.results.map((s) =>
          (s.verified ? '✔' : '✘') + ' step ' + s.step + ' ' + s.task +
          (s.value !== undefined ? ' = ' + JSON.stringify(s.value) : '') +
          (s.error ? ' error=' + s.error : '')
        ).join('\n');
        out.textContent = 'FBP plan ok (' + data.completed + ' step(s)):\n' + lines;
      } else {
        out.textContent = 'FBP plan FAILED: ' + (data.error || 'unverified step');
        out.className = 'error';
      }
    } catch (err) {
      out.textContent = 'request failed: ' + err; out.className='error';
    } finally {
      if (spin) spin.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  }

  document.getElementById('schema-intent').addEventListener('change', schemaRenderFields);
  document.getElementById('schema-run').addEventListener('click', schemaRun);
  schemaLoad();
</script>
"""

# The Component Network editor client script (kept out of the f-string so its
# JS object braces are not mistaken for f-string interpolations).
_NETWORK_JS = r"""\
<script>
  // A dependency-free, drag-and-drop node-and-wire canvas for Component
  // Networks. Nodes are absolutely-positioned divs; edges are SVG paths. The
  // editor serializes to the exact ComponentNetwork JSON the verified spine
  // runs — no third-party library, no CDN, fully client-side.
  const netState = {components: [], edges: []};
  let netSeq = 1;
  let netPending = null;   // {source, source_field} awaiting a target click
  let netDrag = null;      // {id, dx, dy} while dragging
  const $netOut = () => document.getElementById('net-result');
  const $netSpin = () => document.getElementById('net-spinner');
  const $netBtn = () => document.getElementById('net-run');
  const $netSvg = () => document.getElementById('net-svg');
  const $netCanvas = () => document.getElementById('net-canvas');

  function netNodeEl(id) { return document.getElementById('net-n-' + id); }

  function netAddComponent(task) {
    const id = 'c' + netSeq++;
    netState.components.push({id: id, task: task, args: {}});
    netRender();
  }

  function netRemoveComponent(id) {
    netState.components = netState.components.filter(c => c.id !== id);
    netState.edges = netState.edges.filter(e => e.source !== id && e.target !== id);
    if (netPending && netPending.source === id) netPending = null;
    netRender();
  }

  function netPortPos(id, which) {
    const el = netNodeEl(id);
    if (!el) return {x: 0, y: 0};
    const r = el.getBoundingClientRect();
    const cr = $netCanvas().getBoundingClientRect();
    const x = r.left - cr.left + (which === 'in' ? 0 : r.width);
    const y = r.top - cr.top + r.height / 2;
    return {x: x, y: y};
  }

  function netRender() {
    const canvas = $netCanvas();
    const svg = $netSvg();
    // Clear node divs (keep svg, we rebuild its content).
    canvas.querySelectorAll('.net-node-el').forEach(n => n.remove());
    // Place nodes.
    netState.components.forEach((c, i) => {
      const el = document.createElement('div');
      el.className = 'net-node-el';
      el.id = 'net-n-' + c.id;
      el.style.left = (40 + (i % 4) * 150) + 'px';
      el.style.top = (30 + Math.floor(i / 4) * 70) + 'px';
      el.innerHTML = '<span class="net-port out" title="out"></span>' +
        '<span class="net-node-label">' + c.id + ' : ' + c.task + '</span>' +
        '<span class="net-port in" title="in"></span>';
      // Drag to move.
      el.addEventListener('mousedown', (ev) => {
        if (ev.target.classList.contains('net-port')) return;
        netDrag = {id: c.id, dx: ev.clientX - el.offsetLeft, dy: ev.clientY - el.offsetTop};
        ev.preventDefault();
      });
      // Double-click to remove.
      el.addEventListener('dblclick', () => netRemoveComponent(c.id));
      // Ports: out starts a pending edge, in completes it.
      el.querySelector('.net-port.out').addEventListener('click', (ev) => {
        ev.stopPropagation();
        netPending = {source: c.id, source_field: 'value'};
        el.style.borderColor = '#e09b00';
      });
      el.querySelector('.net-port.in').addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (!netPending) return;
        if (netPending.source === c.id) { netPending = null; el.style.borderColor=''; return; }
        netState.edges.push({source: netPending.source, source_field: netPending.source_field,
          target: c.id, target_arg: 'value'});
        netPending = null;
        netRender();
      });
      canvas.appendChild(el);
    });
    // Draw edges as SVG paths.
    let paths = '';
    netState.edges.forEach((e) => {
      const s = netPortPos(e.source, 'out');
      const t = netPortPos(e.target, 'in');
      const mx = (s.x + t.x) / 2;
      paths += '<path d="M' + s.x + ',' + s.y + ' C' + mx + ',' + s.y + ' ' +
        mx + ',' + t.y + ' ' + t.x + ',' + t.y + '" fill="none" stroke="#5b8def" ' +
        'stroke-width="2"/>';
    });
    svg.innerHTML = paths;
    document.getElementById('net-json').value = JSON.stringify(netState, null, 2);
  }

  // Drag handling on the canvas.
  document.addEventListener('mousemove', (ev) => {
    if (!netDrag) return;
    const el = netNodeEl(netDrag.id);
    if (!el) return;
    el.style.left = (ev.clientX - netDrag.dx) + 'px';
    el.style.top = (ev.clientY - netDrag.dy) + 'px';
    netRenderEdgesOnly();
  });
  document.addEventListener('mouseup', () => { netDrag = null; });

  function netRenderEdgesOnly() {
    const svg = $netSvg();
    let paths = '';
    netState.edges.forEach((e) => {
      const s = netPortPos(e.source, 'out');
      const t = netPortPos(e.target, 'in');
      const mx = (s.x + t.x) / 2;
      paths += '<path d="M' + s.x + ',' + s.y + ' C' + mx + ',' + s.y + ' ' +
        mx + ',' + t.y + ' ' + t.x + ',' + t.y + '" fill="none" stroke="#5b8def" ' +
        'stroke-width="2"/>';
    });
    svg.innerHTML = paths;
  }

  async function netRun() {
    const out = $netOut(); const spin = $netSpin(); const btn = $netBtn();
    out.textContent = ''; out.className = 'note';
    if (spin) spin.style.display = 'inline-block';
    if (btn) btn.disabled = true;
    try {
      const r = await fetch('/network', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(netState)
      });
      const data = await r.json();
      if (!r.ok) {
        out.textContent = 'HTTP ' + r.status + ': ' + JSON.stringify(data);
        out.className='error'; return;
      }
      if (data.ok) {
        let lines = data.results.map((s) =>
          (s.verified ? '✔' : '✘') + ' step ' + s.step + ' ' + s.task +
          (s.value !== undefined ? ' = ' + JSON.stringify(s.value) : '') +
          (s.error ? ' error=' + s.error : '')
        ).join('\n');
        out.textContent = 'Network ok (' + data.completed + ' step(s)):\n' + lines;
      } else {
        out.textContent = 'Network FAILED: ' + (data.error || 'unverified step');
        out.className = 'error';
      }
    } catch (err) {
      out.textContent = 'request failed: ' + err; out.className='error';
    } finally {
      if (spin) spin.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  }

  async function netSave() {
    const name = document.getElementById('net-name').value.trim();
    if (!name) { alert('enter a network name to save'); return; }
    const r = await fetch('/network/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, network: netState})
    });
    const data = await r.json();
    if (data.ok) { netRefreshList(); alert('saved ' + name); }
    else { alert('save failed: ' + (data.error || 'unknown')); }
  }

  async function netRefreshList() {
    try {
      const r = await fetch('/network/list');
      const data = await r.json();
      const sel = document.getElementById('net-load-sel');
      sel.innerHTML = '';
      (data.names || []).forEach((n) => {
        const o = document.createElement('option'); o.value = n; o.textContent = n;
        sel.appendChild(o);
      });
    } catch (e) { /* best-effort */ }
  }

  async function netLoad() {
    const sel = document.getElementById('net-load-sel');
    const name = sel.value;
    if (!name) { alert('select a saved network to load'); return; }
    const r = await fetch('/network/load/' + encodeURIComponent(name));
    const data = await r.json();
    if (data.ok) {
      netState.components = (data.network.components || []).map(c => ({...c}));
      netState.edges = (data.network.edges || []).map(e => ({...e}));
      netRender();
    } else { alert('load failed: ' + (data.error || 'unknown')); }
  }

  document.getElementById('net-add-c').addEventListener('click', () => netAddComponent('double'));
  document.getElementById('net-add-e').addEventListener('click', () => netAddComponent('even'));
  document.getElementById('net-add-s').addEventListener('click', () => netAddComponent('sum'));
  document.getElementById('net-clear').addEventListener('click', () => {
    netState.components = []; netState.edges = []; netPending = null;
    netRender();
  });
  document.getElementById('net-run').addEventListener('click', netRun);
  document.getElementById('net-save').addEventListener('click', netSave);
  document.getElementById('net-load').addEventListener('click', netLoad);
  netRefreshList();
  netRender();
</script>
"""

# The Chat/Designer mode-switch script.
_MODE_JS = r"""\
<script>
  function modeShow(which) {
    const chat = document.getElementById('pane-chat');
    const des = document.getElementById('pane-designer');
    const bc = document.getElementById('mode-chat');
    const bd = document.getElementById('mode-designer');
    if (!chat || !des) return;
    chat.style.display = (which === 'chat') ? 'block' : 'none';
    des.style.display = (which === 'designer') ? 'block' : 'none';
    bc.classList.toggle('active', which === 'chat');
    bd.classList.toggle('active', which === 'designer');
  }
  document.getElementById('mode-chat').addEventListener('click', () => modeShow('chat'));
  document.getElementById('mode-designer').addEventListener('click', () => modeShow('designer'));
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

<div class='mode-switch'>
  <button id='mode-chat' class='mode-btn active' type='button'>Chat</button>
  <button id='mode-designer' class='mode-btn' type='button'>Designer</button>
</div>

<div id='pane-chat' class='mode-pane'>

<div class='card'>
<h2>Model (LLM as an ordinary agent)</h2>
<p class='note'>Ask a model. When an <code>OPENROUTER_API_KEY</code> is set it is
routed to OpenRouter; otherwise the deterministic stub answers (offline).
Answers stream in as they arrive; the transcript shows each turn's status.</p>
<form id='model-form'>
  {select}
  <textarea id='model-prompt' rows='3' cols='60' placeholder='Ask the model...'></textarea>
  <br/>
  <button id='model-ask' type='submit'>Ask</button>
  <span id='model-spinner' class='spinner' style='display:none'></span>
</form>
<pre id='model-result' class='note'></pre>
<h3>Chat history</h3>
<div id='chat-history' class='chat-history'></div>
<button id='history-clear' type='button'>Clear history</button>
{_MODEL_JS}
</div>

<div class='card'>
<h2>Orchestrate → FBP</h2>
<p class='note'>Take a validated, canonical JSON artifact and run it as an
FBP plan through the verified spine. A single task (<code>{{ "task": ... }}</code>)
or an ordered <code>{{ "steps": [...] }}</code> list; each step is a normal
<code>run</code> directive that is parent re-verified, ledgered, and
replayable. Invalid artifacts fail closed.</p>
<textarea id='orch-prompt' rows='5' cols='72'
  placeholder='{{"task": "double", "args": {{"value": 21}}}}'></textarea>
<br/>
<button id='orch-run' type='button'>Run as FBP plan</button>
<span id='orch-spinner' class='spinner' style='display:none'></span>
<pre id='orch-result' class='note'></pre>
{_ORCHESTRATE_JS}
</div>

<div class='card'>
<h2>Schema-driven orchestration</h2>
<p class='note'>Pick a typed intent and fill its fields. The artifact is validated
against the intent's schema (fail-closed), then run as a verified FBP plan —
the same verified spine, but with a typed, schema-constrained form instead of
free-form JSON.</p>
<label for='schema-intent'>Intent</label>
<select id='schema-intent' class='pill'></select>
<div id='schema-fields'></div>
<br/>
<button id='schema-run' type='button'>Run typed intent</button>
<span id='schema-spinner' class='spinner' style='display:none'></span>
<pre id='schema-result' class='note'></pre>
{_SCHEMA_JS}
</div>
</div>

<div id='pane-designer' class='mode-pane' style='display:none'>

<div class='card'>
<h2>Component Network (visual programming)</h2>
<p class='note'>Wire components into a directed data-flow graph. Each component is an FBP
<code>task</code>; each edge feeds a target's input from a source's output. The
network compiles to an ordered plan and runs through the verified spine — cycles
and unknown references fail closed.</p>
<div class='net-panel'>
  <b>Component palette</b>
  <button id='net-add-c' type='button'>+ double</button>
  <button id='net-add-e' type='button'>+ even</button>
  <button id='net-add-s' type='button'>+ sum</button>
  <button id='net-clear' type='button'>Clear</button>
  <button id='net-run' type='button'>Run network</button>
  <span id='net-spinner' class='spinner' style='display:none'></span>
  <br/>
  <input id='net-name' placeholder='network name'/>
  <button id='net-save' type='button'>Save</button>
  <button id='net-load' type='button'>Load</button>
  <select id='net-load-sel'></select>
  <p class='note'>Drag nodes on the canvas. Click a node's <b>out</b> port, then a
  target's <b>in</b> port, to wire an edge. Double-click a node to remove it.</p>
  <div id='net-canvas' class='net-canvas'>
    <svg id='net-svg' width='100%' height='360'></svg>
  </div>
  <textarea id='net-json' rows='5' cols='72' class='note'></textarea>
  <pre id='net-result' class='note'></pre>
{_NETWORK_JS}
</div>
</div>

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
{_MODE_JS}
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
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    open_browser: bool = False,
    history_path: str | os.PathLike[str] | None = None,
    networks_path: str | os.PathLike[str] | None = None,
) -> None:
    """Serve the FBP landing page (blocking). Pass --open to open a browser.

    ``history_path`` optionally grants a durable, cross-restart chat-history
    store (an explicit opt-in; without it the transcript is in-memory only).
    ``networks_path`` optionally grants durable, cross-restart storage for
    saved component networks (an explicit opt-in; without it they are
    in-memory only).
    """
    server = FbpLandingServer(
        host=host, port=port, history_path=history_path, networks_path=networks_path
    )
    if open_browser:
        url = f"http://{host}:{port}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    server.serve_forever()