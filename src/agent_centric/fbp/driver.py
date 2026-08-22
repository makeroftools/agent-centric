"""A high-level, synchronous driver over the agent-centric FBP protocol.

This is the *easy UX* layer. The raw ``Agent`` speaks the directive/response
protocol over ZeroMQ frames; ``FbpDriver`` wraps that in a plain, synchronous
API so a caller can build and drive a tree of agents without touching sockets,
frames, or an event loop.

The driver owns a root ``Agent`` (the shell of the tree) and exposes the
directive kinds as methods:

- ``register`` / ``resolve`` — the passive registry-as-agent catalog.
- ``configure`` / ``configure_child`` — parent provides context (rules,
  verifiers, task allowlist).
- ``run`` — execute a task, optionally delegating to a named child.
- ``spawn`` — provision a real child agent.
- ``ping`` / ``kill`` — liveness and teardown.

Every method returns a ``Response`` (``.value`` / ``.verified`` / ``.node`` /
``.error`` / ``.source``). A response that failed verification is an explicit,
audited failure — never a verified success. The driver is deterministic and
offline-testable over ``inproc://``.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import zmq
import zmq.asyncio

from . import ledger as _ledger
from .agent import Agent, _resolve_entry, register_callable
from .audit import AuditChain
from .config import AgentConfig
from .message import (
    DIRECTIVE_AUDIT,
    DIRECTIVE_CONFIGURE,
    DIRECTIVE_KILL,
    DIRECTIVE_PING,
    DIRECTIVE_RESOLVE,
    DIRECTIVE_RUN,
    DIRECTIVE_SPAWN,
    DIRECTIVE_STATE_GET,
    DIRECTIVE_STATE_SET,
    MESSAGE_DIRECTIVE,
    Response,
)

# A task is a registered callable; a verifier is a pure predicate.
Task = Callable[..., Any]
Verifier = Callable[[Any], bool]


class FbpDriver:
    """A synchronous driver over a root agent and its tree.

    The driver binds a ROUTER at the root endpoint, creates a root ``Agent``
    that connects a DEALER to it, and sends directives on the caller's behalf.
    Each method blocks until the matching response arrives (or the agent fails
    closed), so the caller never manages the poll loop.

    Args:
        endpoint: The root channel name (default ``root``).
        transport: The transport to use (``inproc``, ``tcp``, ``ipc``).
        identity: The root agent's identity (default ``root``).
    """

    def __init__(
        self,
        *,
        endpoint: str = "root",
        transport: str = "inproc",
        identity: str = "root",
        replay_state_isolate: bool = False,
        ledger_path: str | None = None,
    ) -> None:
        self._transport = transport
        # When replaying a full session, on-disk state grants are redirected to
        # fresh temp paths so the replayed tree never reads or writes the
        # original (live) store files. This makes stateful trees (e.g. bills)
        # replay cleanly and keeps replay side-effect-free on real data.
        self._replay_state_isolate = replay_state_isolate
        self._replay_tmpdir: tempfile.TemporaryDirectory[str] | None = None
        self._replay_paths: dict[str, str] = {}
        # An optional durable directive ledger (an explicit grant — a path the
        # caller chooses). When given, every directive is persisted so a later
        # process can re-open the ledger and replay (re-verify) the session
        # after the fact. When None, the ledger is in-memory only (as before).
        self._ledger_store = (
            _ledger.DirectiveLedger(ledger_path) if ledger_path is not None else None
        )
        if self._ledger_store is not None:
            self._ledger_store.open()
        self._endpoint = f"{transport}://{endpoint}"
        # The driver owns a private, dedicated event loop. It is set as the
        # current loop so ZeroMQ's async sockets resolve the right loop (in
        # Python 3.13 ``get_event_loop`` raises if none is set, which otherwise
        # fails spuriously when other tests have torn down loop state).
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        self._context = zmq.asyncio.Context()
        self._root_socket = self._context.socket(zmq.ROUTER)
        self._root_socket.bind(self._endpoint)
        self._root = Agent(
            AgentConfig(
                identity=identity,
                parent_endpoint=self._endpoint,
                transport=transport,
                context=self._context,
            )
        )
        self._root.init()
        self._seq = 0
        self._child_base = 0
        self._ledger: dict[str, dict[str, Any]] = {}
        # Over ``tcp``/``ipc`` the DEALER link connects asynchronously; retry a
        # bounded number of times with a short settle before giving up.
        self._settle_attempts = 5
        self._settle_delay = 0.05
        self._poll_timeout = 0.1
        if transport != "inproc":
            # Give the async transport link a beat to establish before the
            # first directive is sent.
            self._loop.run_until_complete(asyncio.sleep(0.2))

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        """Teardown the root agent, its sockets, and the event loop."""
        self._root.kill()
        self._root_socket.close(0)
        self._context.term()
        self._loop.close()
        if self._ledger_store is not None:
            self._ledger_store.close()
            self._ledger_store = None
        if self._replay_tmpdir is not None:
            self._replay_tmpdir.cleanup()
            self._replay_tmpdir = None

    def __enter__(self) -> FbpDriver:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- low-level round-trip ----------------------------------------------

    def _correlation(self, prefix: str) -> str:
        self._seq += 1
        return f"{prefix}-{self._seq}"

    def _roundtrip(self, kind: str, payload: dict[str, Any], prefix: str) -> Response:
        """Send a directive to the root and step the poll loop until its response.

        A delegated directive is routed down to a child agent, so the whole
        tree must be polled (root and every descendant child) until the
        matching response bubbles back up.

        Over ``tcp``/``ipc`` the DEALER link is established asynchronously, so a
        message sent before the peer's registration can be dropped by the
        transport. Per the protocol, we retry until the response arrives — the
        re-send is idempotency-safe (the same directive fingerprint returns the
        cached result rather than re-executing).
        """
        correlation_id = self._correlation(prefix)
        # Record the directive in the ledger so it can be replayed later
        # (deterministic re-verification after the fact). This is also persisted
        # to the durable ledger store when one is granted.
        self._ledger[correlation_id] = {"kind": kind, "payload": payload}
        if self._ledger_store is not None:
            self._ledger_store.append(
                correlation_id=correlation_id, kind=kind, payload=payload
            )
        directive_frames = [
            self._root.identity.encode(),
            correlation_id.encode(),
            MESSAGE_DIRECTIVE.encode(),
            kind.encode(),
            json.dumps(payload).encode(),
        ]
        # Bounded retry for transport asynchrony; a settled link answers on the
        # first attempt, so this loop normally exits immediately.
        attempts = self._settle_attempts
        for attempt in range(1, attempts + 1):
            self._root_socket.send_multipart(directive_frames)
            responses = self._poll_tree_bounded()
            for response in responses:
                if response.correlation_id == correlation_id:
                    # Record the terminal outcome for deterministic replay.
                    outcome = {
                        "terminal": response.kind,
                        "terminal_value": response.value,
                        "terminal_error": response.error,
                    }
                    self._ledger[correlation_id]["response"] = outcome
                    if self._ledger_store is not None:
                        self._ledger_store.set_outcome(
                            correlation_id=correlation_id, outcome=outcome
                        )
                    return response
            if attempt < attempts:
                time.sleep(self._settle_delay)
        raise RuntimeError(
            f"no response from root for {prefix!r} after {attempts} attempts"
        )

    def _poll_tree_bounded(self) -> list[Response]:
        """Poll the tree for one window; return the root's responses.

        Children are polled first so a child's response is available to be
        relayed by the root in the same step. A poll window that exhausts with
        no event simply returns an empty list — the caller retries.
        """
        self._poll_children(self._root)
        return self._loop.run_until_complete(
            self._root.poll(timeout=self._poll_timeout)
        )

    def _poll_children(self, agent: Agent) -> None:
        """Poll ``agent``'s children depth-first, discarding their responses."""
        for child in agent.children.values():
            self._poll_children(child)
            self._loop.run_until_complete(child.poll(timeout=self._poll_timeout))

    # -- registry-as-agent -------------------------------------------------

    def register(
        self, name: str, fn: Task, *, source_url: str = ""
    ) -> None:
        """Register a callable so directives can reference it (chain-audited).

        The callable is registered in the module-level catalog (so directives
        can resolve it by name) and immediately made available to the root
        agent's own registry, so ``resolve`` works without a prior ``configure``.
        """
        register_callable(name, fn, source_url=source_url)
        self._root._registry.register_entry(_resolve_entry(name))
        if self._ledger_store is not None:
            entry = _resolve_entry(name)
            self._ledger_store.record_callable(
                name=name,
                source_url=source_url,
                module=entry.module,
                qualname=entry.qualname,
            )

    def resolve(self, name: str) -> Response:
        """Return the passive-catalog location for a named capability."""
        return self._roundtrip(
            DIRECTIVE_RESOLVE, {"name": name}, prefix="resolve"
        )

    def state_set(self, key: str, value: Any) -> Response:
        """Idempotently persist ``value`` at ``key`` in the root's state store.

        A replayed directive (same key/value/fingerprint) is a no-op; a
        distinct directive is a real update. Requires a state store granted via
        ``configure(state=...)``.
        """
        return self._roundtrip(
            DIRECTIVE_STATE_SET,
            {"key": key, "value": value},
            prefix="state-set",
        )

    def state_get(self, key: str) -> Response:
        """Return the value at ``key`` from the root's durable state store."""
        return self._roundtrip(DIRECTIVE_STATE_GET, {"key": key}, prefix="state-get")

    def audit(self) -> Response:
        """Return the root agent's local audit record (the chain's local start)."""
        return self._roundtrip(DIRECTIVE_AUDIT, {}, prefix="audit")

    def reconstruct_audit(self) -> list[dict[str, Any]]:
        """Reconstruct the full audit chain per correlation id across the tree.

        Gathers every descendant agent's trajectory store and reconstructs the
        causal chains (audit as proof). Read-only and deterministic.
        """
        from .audit import reconstruct_chains

        stores: dict[str, Any] = {}

        def _collect(agent: Any) -> None:
            if agent._trajectory_store is not None:
                stores[agent.identity] = agent._trajectory_store
            for child in agent.children.values():
                _collect(child)

        _collect(self._root)
        chains = reconstruct_chains(stores)
        return [c.to_dict() for c in chains]

    def ledger(self) -> dict[str, dict[str, Any]]:
        """Return a copy of the recorded directive ledger.

        Each logged directive is ``{correlation_id: {"kind": str, "payload": dict}}``
        as issued this session — the inputs to deterministic replay.
        """
        return dict(self._ledger)

    def summary(self) -> dict[str, Any]:
        """A deterministic, operator-facing summary of this session's lane.

        Aggregates per-kind directive counts and run outcomes (verified/error).
        Read-only and deterministic; same shape as ``summarise_ledger``.
        """
        return _summarise_entries(self._ledger)

    def reconstruct_audit_chains(self) -> tuple[AuditChain, ...]:
        """Return the reconstructed chains (dataclasses) for the current tree."""
        from .audit import reconstruct_chains

        stores: dict[str, Any] = {}

        def _collect(agent: Any) -> None:
            if agent._trajectory_store is not None:
                stores[agent.identity] = agent._trajectory_store
            for child in agent.children.values():
                _collect(child)

        _collect(self._root)
        return reconstruct_chains(stores)

    def replay(self, target: str | None = None) -> dict[str, Any]:
        """Re-run a recorded ``run`` directive (or the latest) and compare.

        Deterministic re-verification after the fact: re-issues the recorded
        ``run`` directive against a fresh, storeless driver (so no write-once
        audit collision) and compares the fresh response to the recorded
        terminal outcome. This is sound because ``run`` tasks are registered
        callables in the module-level catalog, so a fresh driver resolves and
        executes the same deterministic task.

        Args:
            target: A correlation id to replay, or None for the latest ``run``.

        Returns:
            ``{"correlation_id", "recorded", "replayed", "passed",
            "diff"}``.
        """
        runs = [
            (cid, d) for cid, d in self._ledger.items() if d["kind"] == DIRECTIVE_RUN
        ]
        if not runs:
            return {
                "passed": False,
                "diff": "no run directive recorded",
                "correlation_id": None,
                "recorded": None,
                "replayed": None,
            }
        if target is not None:
            chosen = [(cid, d) for cid, d in runs if cid == target]
            if not chosen:
                return {
                    "passed": False,
                    "diff": "unknown correlation id",
                    "correlation_id": target,
                    "recorded": None,
                    "replayed": None,
                }
        else:
            chosen = [runs[-1]]
        cid, directive = chosen[0]
        payload = dict(directive["payload"])

        recorded = directive.get("response")
        fresh = self._replay_run(payload)
        passed = recorded is not None and fresh is not None and _same_outcome(
            recorded, fresh
        )
        diff = None if passed else _outcome_diff(recorded, fresh)
        return {
            "correlation_id": cid,
            "passed": passed,
            "recorded": recorded,
            "replayed": fresh,
            "diff": diff,
        }

    def _replay_run(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Re-run a run directive's task against a fresh driver and return the
        fresh terminal response dict, or None on failure.

        The fresh root is configured with the original task allowlist and
        verifier so it can resolve the same deterministic task. The callable is
        the module-registered one (already in the catalog via ``register``).
        """
        task = payload.get("task")
        if not isinstance(task, str):
            return None
        try:
            entry = _resolve_entry(task)
        except KeyError:
            return None
        fresh_verifier = self._root._verifier
        # The run directive may carry its own per-run verifier (``payload["verifier"]``)
        # distinct from the root default. Both must be resolvable on the fresh
        # root, or a verified original would diverge into a spurious failure.
        per_run_verifier = payload.get("verifier")
        verifier_names = {
            v for v in (fresh_verifier, per_run_verifier) if isinstance(v, str)
        }
        with self.__class__() as fresh:
            try:
                fresh.register(task, entry.callable if entry.callable else _noop)
                for vname in verifier_names:
                    try:
                        ventry = _resolve_entry(vname)
                    except KeyError:
                        continue
                    fresh.register(
                        vname, ventry.callable if ventry.callable else _noop
                    )
                cfg_payload: dict[str, Any] = {"tasks": [task]}
                if fresh_verifier is not None:
                    cfg_payload["verifier"] = fresh_verifier
                fresh._roundtrip(DIRECTIVE_CONFIGURE, cfg_payload, prefix="replay-cfg")
                resp = fresh._roundtrip(
                    DIRECTIVE_RUN,
                    payload,
                    prefix="replay",
                )
            except Exception:  # noqa: BLE001 - fail closed, never silent
                return None
            return {
                "terminal": resp.kind,
                "terminal_value": resp.value,
                "terminal_error": resp.error,
            }

    def replay_session(
        self, entries: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, Any]:
        """Replay the full recorded directive sequence and verify every run.

        This is the general form of replay: it re-issues every recorded
        directive in issue order on a fresh driver, rebuilding the same tree
        topology (spawn / configure / setup) as it goes, and compares each
        ``run`` outcome to its recorded response. It therefore also covers
        delegated ``run`` directives, which a single-record replay cannot
        (they need the child topology in place).

        Args:
            entries: Optional entries to replay (from a reopened durable
                ledger). When None, replays this session's own in-memory
                ledger. The keys are correlation ids; values are the recorded
                ``{kind, payload, response, _child}`` dicts.

        Returns:
            ``{"total": int, "runs": int, "passed": int, "failed":
            [{correlation_id, recorded, replayed, diff}], "ok": bool}``.
        """
        ledger_entries = self._ledger if entries is None else entries

        # Issue order: correlation ids are ``{prefix}-{seq}``; sort by seq.
        def _seq_of(cid: str) -> int:
            try:
                return int(cid.rsplit("-", 1)[1])
            except (ValueError, IndexError):
                return 0

        ordered = sorted(ledger_entries.items(), key=lambda kv: _seq_of(kv[0]))
        run_directives = [
            (cid, d) for cid, d in ordered if d["kind"] == DIRECTIVE_RUN
        ]
        failed: list[dict[str, Any]] = []

        with self.__class__(replay_state_isolate=True) as fresh:
            for cid, directive in ordered:
                payload = directive["payload"]
                if directive.get("_child") is True:
                    # A recorded child-configure: reconstitute the child on the
                    # fresh tree via configure_child. The fresh driver's state
                    # isolation remaps the granted store path, so the replayed
                    # child never touches the original on-disk store.
                    fresh.configure_child(
                        payload["identity"], **payload["extra"]
                    )
                    continue
                if directive["kind"] in (DIRECTIVE_CONFIGURE, DIRECTIVE_SPAWN):
                    # Re-issue on the fresh driver to rebuild the tree. A root
                    # configure's state grant is remapped to a fresh temp path
                    # (isolation), so the replayed root never touches the
                    # original on-disk store.
                    if directive["kind"] == DIRECTIVE_CONFIGURE:
                        payload = fresh._remap_state_paths(payload)
                    fresh._roundtrip(directive["kind"], payload, prefix="replay")
                    continue
                if directive["kind"] != DIRECTIVE_RUN:
                    continue
                # A run outcome we will verify (delegated or local). A run's
                # state grant (e.g. ``bills_setup``'s ``args.state``) is also
                # remapped so the replayed tree reads a fresh, isolated store.
                payload = fresh._remap_state_paths(payload)
                recorded = directive.get("response")
                try:
                    resp = fresh._roundtrip(
                        DIRECTIVE_RUN, payload, prefix="replay-run"
                    )
                except Exception:  # noqa: BLE001 - fail closed, never silent
                    failed.append(
                        {"run_id": cid, "diff": "replay raised",
                         "recorded": recorded, "replayed": None}
                    )
                    continue
                replayed = {
                    "terminal": resp.kind,
                    "terminal_value": resp.value,
                    "terminal_error": resp.error,
                }
                if recorded is not None and _same_outcome(recorded, replayed):
                    continue
                failed.append(
                    {
                        "run_id": cid,
                        "recorded": recorded,
                        "replayed": replayed,
                        "diff": _outcome_diff(recorded, replayed),
                    }
                )

        return {
            "total": len(ordered),
            "runs": len(run_directives),
            "passed": len(run_directives) - len(failed),
            "failed": failed,
            "ok": not failed,
        }

    # -- configuration -----------------------------------------------------

    def configure(
        self,
        *,
        tasks: tuple[str, ...] = (),
        verifiers: tuple[str, ...] = (),
        rules: tuple[str, ...] = (),
        verifier: str | None = None,
        clear_verifier: bool = False,
        state: str | None = None,
        state_read_only: bool = False,
        trajectory: str | None = None,
    ) -> Response:
        """Configure the root agent's rules, task allowlist, verifier, and
        optional durable stores.

        Args:
            tasks/verifiers/rules/verifier: The task allowlist, verifier list,
                hard rules, and default verifier for the root agent.
            clear_verifier: If true, clear the root's default verifier (so
                delegated non-numeric values are not demoted on relay). This is
                recorded in the ledger and replayed faithfully.
            state: Optional durable state file path grant (a single-writer
                key/value store the agent owns).
            state_read_only: If true, open the state store read-only (read-only
                grant; writes fail closed).
            trajectory: Optional durable trajectory file path grant (an
                append-only local audit — the start of chain audit).
        """
        payload: dict[str, Any] = {
            "tasks": list(tasks),
            "verifiers": list(verifiers),
            "rules": list(rules),
        }
        if verifier is not None:
            payload["verifier"] = verifier
        if clear_verifier:
            payload["_clear_verifier"] = True
        if state is not None:
            payload["state"] = self._isolate_state_path(state)
            payload["state_read_only"] = state_read_only
        if trajectory is not None:
            payload["trajectory"] = self._isolate_state_path(trajectory)
        return self._roundtrip(DIRECTIVE_CONFIGURE, payload, prefix="configure")

    def configure_child(
        self,
        identity: str,
        *,
        tasks: tuple[str, ...] = (),
        verifiers: tuple[str, ...] = (),
        rules: tuple[str, ...] = (),
        verifier: str | None = None,
        state: str | None = None,
        state_read_only: bool = False,
        trajectory: str | None = None,
        store_keys: tuple[str, ...] = (),
    ) -> Response:
        """Configure a spawned child (the parent provides the child's context).

        Records a synthetic configure directive in the ledger (keyed under a
        child-specific correlation id) so ``replay_session`` can rebuild child
        configuration for delegated runs.
        """
        # Record the child-configure so replay_session can reconstitute the child.
        self._seq += 1
        cid = f"configure-child-{self._seq}"
        payload: dict[str, Any] = {"identity": identity, "extra": {
            "tasks": list(tasks),
            "verifiers": list(verifiers),
            "rules": list(rules),
            "store_keys": list(store_keys),
        }}
        if state is not None:
            payload["extra"]["state"] = self._isolate_state_path(state)
            payload["extra"]["state_read_only"] = state_read_only
        if trajectory is not None:
            payload["extra"]["trajectory"] = self._isolate_state_path(trajectory)
        if verifier is not None:
            payload["extra"]["verifier"] = verifier
        self._ledger[cid] = {"kind": "configure", "payload": payload, "_child": True}
        if self._ledger_store is not None:
            self._ledger_store.append(
                correlation_id=cid, kind="configure", payload=payload, child=True
            )

        return self._root.configure_child(
            identity,
            tasks=tasks,
            verifiers=verifiers,
            rules=rules,
            verifier=verifier,
            state=self._isolate_state_path(state) if state is not None else None,
            state_read_only=state_read_only,
            trajectory=self._isolate_state_path(trajectory)
            if trajectory is not None
            else None,
            store_keys=store_keys,
        )

    # -- execution ---------------------------------------------------------

    def run(
        self,
        task: str,
        args: dict[str, Any] | None = None,
        *,
        verifier: str | None = None,
        child: str | None = None,
        sources: list[dict[str, Any]] | None = None,
    ) -> Response:
        """Run a task, optionally delegating to a named child.

        Args:
            sources: Optional source references to attach to the response
                (e.g. the model id / documents that informed a non-deterministic
                producer's output). They are carried on the response and the
                audit; they are metadata, not task inputs.

        Returns:
            A ``Response``. If ``child`` is given and is a spawned child, the
            directive is routed down and the child's verified response is
            relayed up (re-verified by the parent). Otherwise the root resolves
            the task locally.
        """
        payload: dict[str, Any] = {"task": task, "args": args or {}}
        if verifier is not None:
            payload["verifier"] = verifier
        if child is not None:
            payload["child"] = child
        if sources is not None:
            payload["sources"] = sources
        return self._roundtrip(DIRECTIVE_RUN, payload, prefix="run")

    def run_plan(
        self,
        steps: list[dict[str, Any]],
        *,
        on_step: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Run a deterministic sequence of ``run`` steps; fail closed on the
        first unverified one.

        Each step is ``{"task": str, "args": dict, "verifier": str?,
        "child": str?}``. Steps execute strictly in order; each is a normal
        ``run`` directive (so it is recorded in the ledger and replayable). If
        any step is unverified or raises, execution stops and the plan returns
        ``ok=False`` with the failing step — never a partial silent success.

        Args:
            steps: The ordered run steps to execute.
            on_step: An optional per-step observer ``callable(result_dict)``
                invoked as each step completes, so a long-running plan streams
                progress to an operator before the final result is returned.

        Returns:
            ``{"ok": bool, "results": [{"step", "task", "verified",
            "value", "error"}], "completed": int, "failed": dict?}``.
        """
        if not isinstance(steps, list) or not steps:
            raise ValueError("run_plan requires a non-empty 'steps' list")
        results: list[dict[str, Any]] = []
        for idx, step in enumerate(steps):
            task = step.get("task")
            if not isinstance(task, str):
                raise ValueError(f"plan step {idx} requires a 'task' string")
            resp = self.run(
                task,
                step.get("args") or {},
                verifier=step.get("verifier"),
                child=step.get("child"),
            )
            result = {
                "step": idx,
                "task": task,
                "verified": resp.verified,
                "value": resp.value,
                "error": resp.error,
            }
            results.append(result)
            if on_step is not None:
                on_step(result)
            if not resp.verified:
                return {
                    "ok": False,
                    "results": results,
                    "completed": idx,
                    "failed": results[-1],
                }
        return {"ok": True, "results": results, "completed": len(results), "failed": None}

    def spawn(
        self,
        identity: str,
        endpoint: str | None = None,
        *,
        kind: str | None = None,
    ) -> Response:
        """Provision a real child agent (mediated spawn).

        Args:
            identity: The child agent's identity.
            endpoint: The child's endpoint. If omitted (or a bare name), it is
                resolved against the driver's transport.
            kind: An optional domain child kind (e.g. ``store`` to spawn a
                ``StoreAgent``). Defaults to the base ``Agent``.
        """
        if endpoint is None or "://" not in endpoint:
            endpoint = self._child_endpoint(identity)
        payload: dict[str, Any] = {"identity": identity, "endpoint": endpoint}
        if kind is not None:
            payload["kind"] = kind
        return self._roundtrip(DIRECTIVE_SPAWN, payload, prefix="spawn")

    def _child_endpoint(self, identity: str) -> str:
        """Return a transport-appropriate child endpoint for ``identity``."""
        if self._transport == "tcp":
            # Each child binds its own ROUTER at a distinct local port.
            self._child_base += 1
            port = 5600 + self._child_base * 2
            return f"tcp://127.0.0.1:{port}"
        name = f"children-{identity}"
        return f"{self._transport}://{name}"

    def _isolate_state_path(self, path: str) -> str:
        """Redirect an on-disk state grant to a fresh temp path during replay.

        When ``replay_state_isolate`` is set (full-tree replay), every state
        path granted to the replayed tree is rewritten into a private temp
        directory. The replayed tree therefore never reads or writes the
        original (live) store files, so stateful trees (e.g. bills) replay
        cleanly and replay stays side-effect-free on real data.

        The mapping is deterministic per original path: the same original path
        always maps to the same temp path within a single replay session, so
        the replayed tree's topology (which shares one store file across
        agents) is preserved exactly.
        """
        if not self._replay_state_isolate:
            return path
        if self._replay_tmpdir is None:
            self._replay_tmpdir = tempfile.TemporaryDirectory(
                prefix="agent-centric-fbp-replay-"
            )
        if path not in self._replay_paths:
            self._replay_paths[path] = os.path.join(
                self._replay_tmpdir.name, f"store-{len(self._replay_paths)}"
            )
        return self._replay_paths[path]

    def _remap_state_paths(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of ``payload`` with every on-disk store grant remapped.

        Store paths can arrive in a ``configure`` payload (``payload["state"]``,
        ``payload["trajectory"]``) or inside a ``run`` payload's ``args``
        (e.g. ``bills_setup`` grants the registry path via ``args["state"]``).
        During an isolated replay, both are rewritten to fresh temp paths so the
        replayed tree never touches the original store files. The mapping is
        deterministic per original path, so a store shared across agents maps to
        one temp path.
        """
        out = dict(payload)
        for key in ("state", "trajectory"):
            if isinstance(out.get(key), str):
                out[key] = self._isolate_state_path(out[key])
        args = out.get("args")
        if isinstance(args, dict):
            args = dict(args)
            for key in ("state", "trajectory"):
                if isinstance(args.get(key), str):
                    args[key] = self._isolate_state_path(args[key])
            out["args"] = args
        return out

    # -- liveness / teardown ------------------------------------------------

    def ping(self) -> Response:
        """Return an ``ok`` response if the root agent is alive."""
        return self._roundtrip(DIRECTIVE_PING, {}, prefix="ping")

    def kill(self) -> Response:
        """Kill the root agent (teardown)."""
        return self._roundtrip(DIRECTIVE_KILL, {}, prefix="kill")


def _same_outcome(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True if two outcome dicts are equivalent (terminal kind, value, error)."""
    return (
        a.get("terminal") == b.get("terminal")
        and a.get("terminal_value") == b.get("terminal_value")
        and a.get("terminal_error") == b.get("terminal_error")
    )


def _outcome_diff(a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    """A human-readable description of how two outcomes differ."""
    if a is None or b is None:
        return {"recorded": a, "replayed": b, "reason": "missing outcome"}
    diffs = {
        field: (av, bv)
        for field, av, bv in (
            ("terminal", a.get("terminal"), b.get("terminal")),
            ("terminal_value", a.get("terminal_value"), b.get("terminal_value")),
            ("terminal_error", a.get("terminal_error"), b.get("terminal_error")),
        )
        if av != bv
    }
    return {"recorded": a, "replayed": b, "differences": diffs}


def _noop(*_args: Any, **_kwargs: Any) -> Any:
    """A safe fallback callable (should never run in practice)."""
    return None


def _summarise_entries(
    entries: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """A deterministic, operator-facing summary of a directive ledger/entries.

    Aggregates per-kind directive counts, run outcomes (verified/error), and a
    list of run results. Deterministic: orders by correlation id. Read-only —
    never mutates the entries.
    """
    kinds: dict[str, int] = {}
    runs: list[dict[str, Any]] = []
    for cid in sorted(entries):
        d = entries[cid]
        kind = d["kind"]
        kinds[kind] = kinds.get(kind, 0) + 1
        resp = d.get("response")
        if kind == DIRECTIVE_RUN:
            runs.append(
                {
                    "correlation_id": cid,
                    "task": d["payload"].get("task"),
                    "terminal": resp.get("terminal") if resp else None,
                    "verified": bool(
                        resp and resp.get("terminal") in ("result", "ok")
                    ),
                    "value": resp.get("terminal_value") if resp else None,
                    "error": resp.get("terminal_error") if resp else None,
                    "child": d["payload"].get("child"),
                }
            )
    verified_runs = sum(1 for r in runs if r["verified"])
    return {
        "kinds": dict(sorted(kinds.items())),
        "runs": runs,
        "run_count": len(runs),
        "verified_runs": verified_runs,
        "error_runs": len(runs) - verified_runs,
        "ok": verified_runs == len(runs),
    }


def summarise_ledger(
    ledger_path: str | os.PathLike[str],
) -> dict[str, Any]:
    """Summarise a durable directive ledger (operator-facing, read-only).

    Returns the same shape as ``FbpDriver.summary`` for a recorded session.
    Fails closed (raises ``FileNotFoundError``) if the ledger does not exist.
    """
    entries = load_ledger(ledger_path)
    return _summarise_entries(entries)


def _seed_entry_from_source(name: str, info: dict[str, str]) -> bool:
    """Register ``name`` by importing the recorded module.qualname.

    Returns True if the callable was imported and registered in the module-level
    catalog (so the replayed tree can resolve it). Returns False if the manifest
    has no importable source or the import fails (the caller can seed manually).
    """
    module = info.get("module", "")
    qualname = info.get("qualname", "")
    if not module or not qualname:
        return False
    try:
        mod = importlib.import_module(module)
    except (ImportError, ModuleNotFoundError):
        return False
    obj: Any = mod
    try:
        for part in qualname.split("."):
            obj = getattr(obj, part)
    except AttributeError:
        return False
    if not callable(obj):
        return False
    register_callable(name, obj, source_url=info.get("source_url", ""))
    return True


def load_ledger(ledger_path: str | os.PathLike[str]) -> dict[str, dict[str, Any]]:
    """Load a durable directive ledger into the in-memory replay shape.

    Returns a ``{correlation_id: {kind, payload, response, _child}}`` mapping
    (the inputs to ``FbpDriver.replay_session``). Read-only — the ledger is not
    modified. Fails closed (raises) if the ledger cannot be read.
    """
    store = _ledger.DirectiveLedger(Path(ledger_path))
    if not Path(ledger_path).is_file():
        raise FileNotFoundError(f"no ledger file at {ledger_path}")
    store.open()
    try:
        out: dict[str, dict[str, Any]] = {}
        for row in store.all():
            entry: dict[str, Any] = {
                "kind": row["kind"],
                "payload": row["payload"],
            }
            if row["response"] is not None:
                entry["response"] = row["response"]
            if row.get("_child"):
                entry["_child"] = True
            out[row["correlation_id"]] = entry
        return out
    finally:
        store.close()


def ledger_callables(
    ledger_path: str | os.PathLike[str],
) -> dict[str, dict[str, str]]:
    """Return the registry manifest recorded in a durable ledger.

    Returns ``{name: {source_url, module, qualname}}`` — the callables the
    recording session registered, so a fresh process knows *what* to seed (and
    how to import them) to re-resolve directives.
    """
    store = _ledger.DirectiveLedger(Path(ledger_path))
    store.open()
    try:
        return store.callables()
    finally:
        store.close()


def replay_ledger(
    ledger_path: str | os.PathLike[str],
    transport: str = "inproc",
    endpoint: str = "root",
) -> dict[str, Any]:
    """Re-open a durable directive ledger and replay (re-verify) it.

    This is the crash-safe recovery path: a session recorded to a durable
    ledger (via ``FbpDriver(ledger_path=...)``) can be re-verified after the
    process is gone by re-issuing every directive on a fresh, state-isolated
    tree. Returns the same shape as ``FbpDriver.replay_session``.

    The original callables cannot cross the wire, so ``replay_ledger`` re-seeds
    them from the ledger's registry manifest: for each recorded callable it
    imports the recorded ``module.qualname`` and registers it by name. Callables
    without an importable source (e.g. REPL closures) cannot be auto-restored;
    the manifest still records their name and source URL for a caller to seed
    manually.
    """
    entries = load_ledger(ledger_path)
    manifest = ledger_callables(ledger_path)
    seeded: list[str] = []
    missing: list[str] = []
    for name, info in manifest.items():
        ok = _seed_entry_from_source(name, info)
        (seeded if ok else missing).append(name)
    with FbpDriver(transport=transport, endpoint=endpoint) as fresh:
        result = fresh.replay_session(entries=entries)
        result["seeded_callables"] = seeded
        result["missing_callables"] = missing
        return result