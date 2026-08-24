"""Shared worker-thread host for ``FbpDriver`` (used by both ACP and MCP).

The ``FbpDriver`` binds a private asyncio event loop and drives it with
``run_until_complete``. That loop must never be the ACP/MCP loop (which is
already running asyncio), so the driver is hosted on a **dedicated worker thread**:
the driver is constructed on that thread, its private loop is bound there, and every
command is submitted to the worker and awaited by the caller. A command is a plain
callable ``fn(driver) -> result``.

Both edge transports (`agent_centric/acp.py`, `agent_centric/mcp.py`) reuse this
host so the deterministic spine is reached identically from either. A raised
exception is surfaced to the caller (fail-closed: never swallowed).
"""

from __future__ import annotations

import contextlib
import os
import queue
import tempfile
import threading
from collections.abc import Callable
from typing import Any

from agent_centric.fbp import FbpDriver


def _openrouter_http_client(model: str) -> Any:
    """A stdlib ``urllib`` transport for OpenRouter's chat-completions API.

    Mirrors the landing-page client so the fail-closed, secret-redacting,
    timeout-bounded provider path is reused. Builds the JSON request body and
    parses the ``choices``/``message`` shape, returning just the text block.
    Raises on HTTP/transport/parse errors; the provider maps those to
    ``ModelProviderError``.
    """

    def client(endpoint: str, headers: dict[str, str], prompt: str) -> str:
        import json
        import urllib.error
        import urllib.request

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


class FbpDriverHost:
    """Host ``FbpDriver`` on a dedicated worker thread.

    The driver binds a private asyncio event loop and drives it with
    ``run_until_complete``. That loop must not be the ACP/MCP loop (which is
    already running asyncio), so the driver is constructed and driven here on this
    worker thread. A command is a plain callable ``fn(driver) -> result``; it is
    submitted to the worker and awaited by the caller. Fail-closed: a command that
    raises is surfaced to the caller, never swallowed.

    Args:
        store_keys: The store-key allowlist granted to the store child (e.g.
            ``acp-*`` for ACP or ``mcp-*`` for MCP). Keys outside fail closed.
        thread_name: Name for the worker thread (to tell transports apart).
    """

    def __init__(self, *, store_keys: str = "acp-*", thread_name: str = "fbp-driver-host") -> None:
        self._store_keys = store_keys
        self._thread_name = thread_name
        self._driver: FbpDriver | None = None
        self._tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._ready = threading.Event()
        self._commands: queue.Queue[Any] = queue.Queue()
        self._thread: threading.Thread | None = None

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Start the worker thread and wait until the driver is built."""
        if self._thread is not None:
            self._ready.wait(timeout=5.0)
            if self._driver is None:
                raise RuntimeError("FBP driver failed to start on the worker thread")
            return
        self._thread = threading.Thread(
            target=self._run, name=self._thread_name, daemon=True
        )
        self._thread.start()
        self._ready.wait(timeout=5.0)
        if self._driver is None:
            raise RuntimeError("FBP driver failed to start on the worker thread")

    def _run(self) -> None:
        """Worker entry: build the driver, then serve commands until stopped."""
        try:
            driver = self._build_driver()
        except BaseException:  # noqa: BLE001 - surfaced to the caller
            self._driver = None
            self._ready.set()
            raise
        self._driver = driver
        self._ready.set()
        while True:
            item = self._commands.get()
            if item is None:  # stop sentinel
                break
            fn, result_q = item
            try:
                result_q.put((fn(driver), None))
            except BaseException as exc:  # noqa: BLE001 - surfaced to the caller
                result_q.put((None, exc))

    def _build_driver(self) -> FbpDriver:
        """Build a driver with a deterministic demo tree."""
        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.register("square", lambda value: value * value, source_url="file:///tasks/square")
        driver.register("negate", lambda value: -value, source_url="file:///tasks/negate")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.register("positive", lambda value: value > 0)
        driver.register("sum", lambda a, b: a + b, source_url="file:///tasks/sum")
        driver.configure(
            tasks=("double", "square", "negate", "even", "odd", "positive", "sum"),
            verifiers=("even", "odd", "positive"),
        )
        self._store_prefix = self._store_keys
        self._tmpdir = tempfile.TemporaryDirectory(prefix="agent-centric-driver-")
        driver.spawn("store", kind="store")
        driver.configure_child(
            "store",
            state=os.path.join(self._tmpdir.name, "store.db"),
            store_keys=(self._store_keys,),
        )
        driver.spawn("model", kind="model")
        self._wire_real_model(driver)
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {
                "state": os.path.join(self._tmpdir.name, "bills.db"),
                "store_keys": ["bill-*"],
            },
            child="bills",
        )
        return driver

    def _wire_real_model(self, driver: FbpDriver) -> None:
        """Wire an OpenRouter provider to the model child when a key is set.

        Reads ``OPENROUTER_API_KEY`` (never hardcoded). When absent, the model
        agent serves its deterministic stub (offline, CI-safe). The correctness
        chain is untouched — the parent still re-verifies the model's output.
        """
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return
        model = os.environ.get("OPENROUTER_MODEL", "").strip() or "deepseek/deepseek-v4-flash-0731"
        try:
            from agent_centric.providers import build_real_model_provider

            provider = build_real_model_provider(
                endpoint="https://openrouter.ai/api/v1/chat/completions",
                api_key=api_key,
                http_client=_openrouter_http_client(model),
            )
            driver.configure_provider("model", provider, model_id=model)
        except Exception:  # noqa: BLE001 - a real provider is opt-in; fail closed to the stub
            return

    # -- submission ---------------------------------------------------------

    def submit(self, fn: Callable[[FbpDriver], Any]) -> Any:
        """Run ``fn(driver)`` on the worker thread and return its result.

        The driver's loop is bound to the worker thread, so the synchronous
        driver call runs on the correct loop. A raised exception is re-raised on the
        caller's thread (fail-closed: never swallowed).
        """
        self.start()
        result_q: queue.Queue[tuple[Any, BaseException | None]] = queue.Queue(maxsize=1)
        self._commands.put((fn, result_q))
        result, error = result_q.get()
        if error is not None:
            raise error
        return result

    def close(self) -> None:
        """Teardown the driver and stop the worker thread."""
        if self._thread is not None:
            self._commands.put(None)  # stop sentinel
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._driver is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort teardown
                self._driver.close()
            self._driver = None
        if self._tmpdir is not None:
            with contextlib.suppress(Exception):  # noqa: BLE001 - best-effort cleanup
                self._tmpdir.cleanup()
            self._tmpdir = None