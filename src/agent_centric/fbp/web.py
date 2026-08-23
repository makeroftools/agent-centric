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
from typing import TYPE_CHECKING, Any

from .activity import ActivityFeed
from .chatstore import ChatHistoryStore
from .driver import FbpDriver

if TYPE_CHECKING:
    from .domainrepo import ArtifactVault, DomainRegistry

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
        bills_path: str | os.PathLike[str] | None = None,
        registry_path: str | os.PathLike[str] | None = None,
        activity_path: str | os.PathLike[str] | None = None,
    ) -> None:
        self._host = host
        self._port = port
        # The bills registry store. In-memory (temp) by default; a caller grants
        # a durable path via ``--bills <path>`` (explicit grant).
        if bills_path is not None:
            self._bills_state_path = str(bills_path)
        else:
            import tempfile

            self._bills_tmp = tempfile.TemporaryDirectory(prefix="agent-centric-fbp-bills-")
            self._bills_state_path = os.path.join(self._bills_tmp.name, "registry.db")
        # The Domain Registry + artifact vault (observability +
        # provenance layer). In-memory by default; durable via ``registry_path``
        # (explicit grant). Set up after the driver so we can observe its tree.
        self._registry_path = str(registry_path) if registry_path is not None else None
        self._domain_registry: DomainRegistry | None = None
        self._artifact_vault: ArtifactVault | None = None
        # One driver, in-process and reused. We register and run a small
        # deterministic demo tree so the page has something real to show.
        self._providers = _build_openrouter_providers()
        self._driver = self._build_driver()
        # Build the Domain Registry + artifact vault from the
        # live tree (read-only observations of how each domain is served).
        self._build_domain_repo()
        # A granted path (--registry) persists the write-once artifact evidence
        # across restarts (explicit grant; load any prior evidence now).
        if self._registry_path and self._artifact_vault is not None:
            self._load_artifacts()
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
        # Operator activity feed (bounded, append-only, optionally-durable
        # audit of operator actions). In-memory by default; durable via
        # ``activity_path`` (explicit grant).
        self._activity = ActivityFeed(
            path=str(activity_path) if activity_path is not None else None
        )

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
        driver.register("triple", lambda value: value * 3, source_url="file:///tasks/triple")
        driver.register("square", lambda value: value * value, source_url="file:///tasks/square")
        driver.register("negate", lambda value: -value, source_url="file:///tasks/negate")
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.register("positive", lambda value: value > 0)
        driver.register("sum", lambda a, b: a + b, source_url="file:///tasks/sum")
        driver.register("product", lambda a, b: a * b, source_url="file:///tasks/product")
        driver.register("concat", lambda a, b: f"{a}{b}", source_url="file:///tasks/concat")
        # No global verifier: the ``double`` demo passes ``even`` per-run, and
        # the model agent (string output) is not gated by a numeric verifier.
        driver.configure(
            tasks=(
                "double", "triple", "square", "negate",
                "even", "odd", "positive", "sum", "product", "concat",
            ),
            verifiers=("even", "odd", "positive"),
        )
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
        # A bills loop: intake -> human-gated accept -> durable registry ->
        # calendar. The store is granted a ``bill-*`` prefix so the landing page
        # can accept arbitrary bill ids under the namespace (still fail-closed
        # on anything outside it). The registry is in-memory (temp) by default;
        # a caller grants a durable path via ``--bills <path>``.
        driver.spawn("bills", kind="bills")
        driver.run(
            "bills_setup",
            {
                "state": self._bills_state_path,
                "store_keys": ["bill-*"],
            },
            child="bills",
        )
        return driver

    # -- Domain Registry + artifact vault --------------------

    def _build_domain_repo(self) -> None:
        """Observe the live tree into the DomainRegistry + ArtifactVault.

        Each registered capability on a node is observed as a domain (with its
        expert selection determined deterministically), and a representative
        verified artifact is recorded per domain. Read-only — this only mirrors
        what the tree already serves; it never grants authority.
        """
        from .domainrepo import Artifact, ArtifactVault, DomainRegistry
        from .experts import Domain

        tenant = "local"
        registry = DomainRegistry()
        repo = ArtifactVault()
        tree = self._driver.tree()
        verifiers: set[str] = set()
        for node in tree:
            verifiers.update(node.get("verifiers") or [])
        for node in tree:
            identity = node.get("identity", "")
            for cap in sorted(node.get("capabilities") or []):
                verifiable = cap in verifiers
                domain = Domain(
                    id=f"{identity}::{cap}",
                    name=f"{identity} domain ({cap})",
                    output_fields=("value",) if verifiable else (),
                    determinism=0.8 if verifiable else 0.2,
                    residue=0.1 if verifiable else 0.9,
                )
                rec = registry.observe(domain, tenant=tenant, provenance=f"node:{identity}")
                repo.record(Artifact(
                    tenant=tenant, domain=domain.id, run="seed",
                    value={"served_by": rec.selection.kind},
                    kind=rec.selection.kind,
                    residue=domain.residue or 0.0, cost=1,
                ))
        self._domain_registry = registry
        self._artifact_vault = repo

    def _record_run_artifact(self, task: str, result: dict[str, Any]) -> None:
        """Record a verified run's artifact into the Artifact Vault.

        Called by the run paths when a step verifies, so the vault reflects real
        runs (not just the startup seeds). Fail-closed: an unverified result or a
        missing vault is a no-op (evidence is only ever written for verified
        outcomes).
        """
        if self._artifact_vault is None or not result.get("verified"):
            return
        from .domainrepo import Artifact

        try:
            self._artifact_vault.record(Artifact(
                tenant="local",
                domain=task,
                run=f"run-{self._artifact_vault.count()}",
                value=result.get("value"),
                kind="deterministic",
                residue=0.0,
                cost=1,
            ))
        except Exception:  # noqa: BLE001 - evidence recording must never break a run
            return

    def _domain_readout(self) -> dict[str, Any]:

        """A read-only view of the Domain Registry."""
        if self._domain_registry is None:
            return {"ok": False, "domains": []}
        return {
            "ok": True,
            "domains": [r.to_dict() for r in self._domain_registry.all()],
            "count": self._domain_registry.count(),
            "durable": self._registry_path is not None,
        }

    def _artifact_readout(self) -> dict[str, Any]:
        """A read-only view of the artifact vault (write-once evidence)."""
        if self._artifact_vault is None:
            return {"ok": False, "artifacts": []}
        return {
            "ok": True,
            "artifacts": [a.to_dict() for a in self._artifact_vault.all()],
            "count": self._artifact_vault.count(),
            "total_cost": self._artifact_vault.total_cost(),
            "durable": self._registry_path is not None,
        }

    def _slm_readout(self) -> dict[str, Any]:
        """A read-only view of which domains warrant a learned (SLM) expert.

        For each observed domain it reports whether ``select_expert`` warrants a
        per-domain SLM (the ``learned`` tier). Read-only — it never trains; it
        only shows the deterministic recommendation.
        """
        if self._domain_registry is None:
            return {"ok": False, "warranted": []}
        from .experts import select_expert

        warranted: list[dict[str, Any]] = []
        for rec in self._domain_registry.all():
            sel = select_expert(rec.domain)
            warranted.append({
                "id": rec.domain.id,
                "name": rec.domain.name,
                "kind": sel.kind,
                "warranted": sel.warranted,
                "reason": sel.reason,
            })
        return {"ok": True, "warranted": warranted}

    def _catalog_readout(self) -> dict[str, Any]:
        """Read-only view of the recommended base-model catalog.

        Serves the 2026 reference catalog (categorical by deployment class) so
        the provisioning card can offer a model picker and show each base's
        recommended hardware tier. Deterministic and read-only.
        """
        from .model_catalog import catalog

        return {"ok": True, **catalog()}

    def _provision_domains(self) -> list[dict[str, Any]]:
        """The live domains a user may provision (read-only).

        Mirrors the tree-observed domains used by the expert/deterministic
        readouts, so the provisioning card lists the same verifiable domains the
        rest of the page serves.
        """
        from .experts import Domain, select_expert

        tree = self._driver.tree()
        verifiers: set[str] = set()
        for node in tree:
            verifiers.update(node.get("verifiers") or [])
        domains: list[dict[str, Any]] = []
        seen: set[str] = set()
        for node in tree:
            identity = node.get("identity", "")
            for cap in sorted(node.get("capabilities") or []):
                did = f"{identity}::{cap}"
                if did in seen:
                    continue
                seen.add(did)
                verifiable = cap in verifiers
                domain = Domain(
                    id=did,
                    name=f"{identity} domain ({cap})",
                    output_fields=("value",) if verifiable else (),
                    determinism=0.8 if verifiable else 0.2,
                    residue=0.1 if verifiable else 0.9,
                )
                sel = select_expert(domain)
                domains.append({
                    "id": did,
                    "name": domain.name,
                    "kind": sel.kind,
                    "warranted": sel.warranted,
                    "verifiable": verifiable,
                })
        default = "bill-extract::extract"
        alias = "bill-extract::extract.v1"
        if default not in seen and alias not in seen:
            domain = Domain(
                id="bill-extract",
                name="bill extraction",
                output_fields=("vendor", "amount_cents", "due_date"),
                determinism=0.5,
                residue=0.8,
            )
            sel = select_expert(domain)
            domains.append({
                "id": "bill-extract",
                "name": domain.name,
                "kind": sel.kind,
                "warranted": sel.warranted,
                "verifiable": True,
            })
        return domains

    def _run_provision(self, body: str) -> dict[str, Any]:
        """Run one deterministic provisioning pass (offline, fail-closed).

        Parses a ``{domain, provider, budget}`` envelope and calls
        ``provision_expert`` through the default (stub) provider — the real
        Modal adapter is opt-in and never reached here. The produced expert's
        artifact is recorded into the Artifact Vault as write-once evidence.
        Recording is best-effort and can never break the run.
        """
        from .experts import Domain
        from .provision import ProvisionError, provision_expert
        from .settlement import SettlementGrant

        try:
            data = json.loads(body or "{}") if body else {}
        except ValueError:
            return {"ok": False, "error": "invalid JSON body"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "body must be a JSON object"}
        did = str(data.get("domain") or "bill-extract")
        provider = str(data.get("provider") or "stub")
        budget = data.get("budget")
        base_model = str(data.get("base_model") or "llama-3.2-3b")

        known = {d["id"] for d in self._provision_domains()}
        if did not in known:
            known_sorted = ", ".join(sorted(known)) or "(none observed)"
            return {
                "ok": False,
                "error": f"domain {did!r} is not a known domain; expected one of {known_sorted}",
            }
        domain_map = {
            "bill-extract": Domain(
                id="bill-extract",
                name="bill extraction",
                output_fields=("vendor", "amount_cents", "due_date"),
                determinism=0.5,
                residue=0.8,
            )
        }
        domain = domain_map.get(did)
        if domain is None:
            return {"ok": False, "error": f"no contract mapped for domain {did!r}"}

        grant = SettlementGrant(domain=did, max_amount=max(budget or 0, 1))
        try:
            result = provision_expert(
                domain,
                [f"vendor GasCo amount {i}" for i in range(50)],
                provider=provider,
                grant=grant,
                budget=budget,
                base_model=base_model,
            )
        except ProvisionError as exc:
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # noqa: BLE001 - surfaced to the operator
            return {"ok": False, "error": f"provision failed: {exc}"}
        self._record_provision_artifact(result)
        self._record_activity(
            "provision", "Provision domain expert",
            detail=result.to_dict(), verified=True,
        )
        return {"ok": True, "result": result.to_dict()}

    def _record_provision_artifact(self, result: Any) -> None:
        """Record a trained expert's artifact into the Artifact Vault.

        Best-effort and write-once: a missing vault, an unverifiable domain, or
        a vault failure is a no-op (evidence is only ever written for a produced
        learned expert). Never breaks the provisioning run.
        """
        if self._artifact_vault is None or result is None:
            return
        expert = getattr(result, "expert", None)
        if expert is None:
            return
        from .domainrepo import Artifact

        try:
            self._artifact_vault.record(Artifact(
                tenant="local",
                domain=result.domain,
                run=f"run-{self._artifact_vault.count()}",
                value={"artifact": expert.artifact, "method": expert.method},
                kind=result.selection,
                residue=result.account.residue,
                cost=result.account.cost,
            ))
        except Exception:  # noqa: BLE001 - evidence recording must never break a run
            return

    # -- persistence of the write-once artifact evidence (explicit grant) ----

    def _load_artifacts(self) -> None:
        """Load prior recorded artifacts from the granted path (fail-closed).

        The artifact vault is the append-only, write-once evidence that
        genuinely accumulates across runs (unlike the registry catalog, which is
        re-derived from the live tree). When ``--registry <path>`` is granted, we
        reload any previously-recorded evidence so it survives a restart.
        """
        import os as _os

        if not self._registry_path or self._artifact_vault is None:
            return
        if not _os.path.exists(self._registry_path):
            return
        try:
            with open(self._registry_path, encoding="utf-8") as fh:
                data = json.load(fh)
            artifacts = data.get("artifacts") if isinstance(data, dict) else None
            if isinstance(artifacts, list):
                from .domainrepo import Artifact as _A

                for a in artifacts:
                    if not isinstance(a, dict):
                        continue
                    try:
                        self._artifact_vault.record(_A(
                            tenant=str(a.get("tenant", "")),
                            domain=str(a.get("domain", "")),
                            run=str(a.get("run", "")),
                            value=a.get("value"),
                            kind=a.get("kind", "human"),
                            residue=float(a.get("residue", 0.0)),
                            cost=int(a.get("cost", 0)),
                        ))
                    except Exception:  # noqa: BLE001 - skip a corrupt entry
                        continue
        except (OSError, ValueError) as exc:
            print(f"fbp-web: could not load domain artifacts: {exc}")

    def _save_artifacts(self) -> None:
        """Atomically persist the artifact evidence to the granted path."""
        if not self._registry_path or self._artifact_vault is None:
            return
        import os
        import tempfile

        directory = os.path.dirname(os.path.abspath(self._registry_path)) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {
            "artifacts": [a.to_dict() for a in self._artifact_vault.all()]
        }
        (fd, tmp_path) = tempfile.mkstemp(dir=directory, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, sort_keys=True, default=str)
            os.replace(tmp_path, self._registry_path)
        except BaseException:
            from contextlib import suppress

            with suppress(OSError):
                os.unlink(tmp_path)
            raise

    # -- page state --------------------------------------------------------

    def _model_choices(self) -> tuple[str, ...]:
        """The configured model choices for the dropdown (may be empty)."""
        return tuple(self._providers.keys())

    def _activity_readout(self) -> dict[str, Any]:
        """A deterministic, read-only view of the operator activity feed.

        Bounded, append-only, and (when a path is granted) durable. Each
        entry records an action an operator took and whether its outcome
        passed the correctness spine. Read-only — nothing is mutated.
        """
        return self._activity.to_dict()

    def _record_activity(
        self, kind: str, action: str, *, detail: object = None,
        verified: bool = False, model: str | None = None,
    ) -> None:
        """Best-effort recording of an operator action into the feed.

        Never raises: the feed is an audit convenience, not the correctness
        spine, so a failed record must not break the action that triggered it.
        """
        try:
            self._activity.record(
                kind=kind, action=action, detail=detail,
                verified=verified, model=model,
            )
        except Exception:  # noqa: BLE001 - audit must never break an action
            return

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
            # Persist the write-once artifact evidence if a path was granted.
            self._save_artifacts()
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
                elif self.path == "/bills/registry":
                    # Read-only snapshot of the durable bills registry.
                    self._send_json(server._bills_registry())
                elif self.path == "/bills/intake":
                    # Intake a bill draft (unverified; requires accept).
                    body = self._read_body()
                    self._send_json(server._bills_intake(body))
                elif self.path == "/bills/accept":
                    # Human-gated accept: promote a draft to the registry.
                    body = self._read_body()
                    self._send_json(server._bills_accept(body))
                elif self.path == "/bills/calendar":
                    # Deterministic calendar projection from the registry.
                    body = self._read_body()
                    self._send_json(server._bills_calendar(body))
                elif self.path == "/experts":
                    # Read-only expert-selection + cost readout for the domains
                    # in the live tree (Network of Experts AI). Deterministic.
                    self._send_json(server._experts_readout())
                elif self.path == "/domains":
                    # Read-only Domain Registry (catalog, tenant-aware).
                    self._send_json(server._domain_readout())
                elif self.path == "/artifacts":
                    # Read-only artifact vault (write-once evidence).
                    self._send_json(server._artifact_readout())
                elif self.path == "/slm":
                    # Read-only view of which domains warrant a learned (SLM)
                    # expert (the deterministic select_expert recommendation).
                    self._send_json(server._slm_readout())
                elif self.path == "/provision":
                    # Run one deterministic expert-provisioning pass
                    # (plan -> train -> account -> settle), offline via the stub.
                    body = self._read_body()
                    self._send_json(server._run_provision(body))
                elif self.path == "/provision/domains":
                    # The live domains a user may provision (read-only).
                    self._send_json({"ok": True, "domains": server._provision_domains()})
                elif self.path == "/catalog":
                    # Read-only recommended base-model catalog (model picker).
                    self._send_json(server._catalog_readout())
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
                elif self.path == "/activity":
                    # Read-only operator activity feed (bounded audit).
                    self._send_json(server._activity_readout())
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
        plan_result = run_artifact_plan(self._driver, artifact)
        self._record_activity(
            "orchestrate", "run artifact",
            detail={"ok": plan_result.get("ok"),
                    "completed": plan_result.get("completed", 0)},
            verified=bool(plan_result.get("ok")),
        )
        for step in plan_result.get("results", []):
            if step.get("verified"):
                self._record_run_artifact(step.get("task", "task"), step)
        return plan_result

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
        net_result = run_network(self._driver, network)
        self._record_activity(
            "network", "Component network",
            detail={"ok": net_result.get("ok"),
                    "completed": net_result.get("completed", 0)},
            verified=bool(net_result.get("ok")),
        )
        for step in net_result.get("results", []):
            if step.get("verified"):
                self._record_run_artifact(step.get("task", "task"), step)
        return net_result

    # -- bills workflow (intake -> human-gated accept -> durable registry) ---

    def _bills_registry(self) -> dict[str, Any]:
        """A read-only snapshot of the durable bills registry.

        Delegates ``bills_registry`` to the bills child (mediated, grant-bound,
        verified). Returns ``{"ok", "registry", "count", "error"?}``.
        """
        resp = self._driver.run("bills_registry", {}, child="bills")
        if not resp.verified:
            return {"ok": False, "registry": {}, "count": 0,
                    "error": resp.error or "bills_registry not verified"}
        value = resp.value if isinstance(resp.value, dict) else {}
        return {
            "ok": True,
            "registry": value.get("registry", {}),
            "count": value.get("count", 0),
        }

    def _bills_intake(self, body: str) -> dict[str, Any]:
        """Intake a bill draft (unverified; still requires the accept gate).

        Body is ``{"draft": {...}}`` (id, vendor, amount_cents, due_date).
        Delegates ``bills_intake`` to the bills child; the produced draft is
        unverified and must be accepted before it enters the registry.
        """
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"invalid payload: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "payload must be a JSON object"}
        draft = data.get("draft")
        if not isinstance(draft, dict):
            return {"ok": False, "error": "intake requires a 'draft' object"}
        resp = self._driver.run("bills_intake", {"draft": draft}, child="bills")
        if not resp.verified:
            return {"ok": False, "error": resp.error or "bills_intake not verified"}
        self._record_activity(
            "bills", "Intake bill draft", detail=draft,
            verified=bool(resp.verified),
        )
        return {"ok": True, "draft": resp.value}

    def _bills_accept(self, body: str) -> dict[str, Any]:
        """Human-gated accept: promote a draft to the durable registry.

        Body is ``{"draft": {...}}`` (the draft from intake). Delegates
        ``bills_accept`` to the bills child — the only path that writes the
        registry. Fail-closed: a malformed draft or an unverified write returns
        ``ok=False``.
        """
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"invalid payload: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "payload must be a JSON object"}
        draft = data.get("draft")
        if not isinstance(draft, dict):
            return {"ok": False, "error": "accept requires a 'draft' object"}
        # Fail-closed: validate the draft shape (id/vendor/amount/due) before it
        # may enter the registry. ``draft_from_intake`` is the deterministic
        # validator intake uses, so a malformed draft can never be accepted.
        from .bills import draft_from_intake

        try:
            draft = draft_from_intake(draft)
        except Exception as exc:  # noqa: BLE001 - fail closed on the UX path
            return {"ok": False, "error": f"accept rejected: {exc}"}
        resp = self._driver.run("bills_accept", {"draft": draft}, child="bills")
        if not resp.verified:
            return {"ok": False, "error": resp.error or "bills_accept not verified"}
        self._record_activity(
            "bills", "Accept bill", detail=draft, verified=bool(resp.verified),
        )
        return {"ok": True, "id": resp.value}

    def _bills_calendar(self, body: str) -> dict[str, Any]:
        """Project a deterministic calendar from the durable registry.

        Body is ``{"from_date": str, "to_date": str}`` (ISO, inclusive).
        Delegates ``bills_calendar`` to the bills child (read-only, verified).
        """
        try:
            data = json.loads(body)
        except (ValueError, TypeError) as exc:
            return {"ok": False, "error": f"invalid payload: {exc}"}
        if not isinstance(data, dict):
            return {"ok": False, "error": "payload must be a JSON object"}
        from_date = data.get("from_date", "")
        to_date = data.get("to_date", "")
        resp = self._driver.run(
            "bills_calendar",
            {"from_date": from_date, "to_date": to_date},
            child="bills",
        )
        if not resp.verified:
            return {"ok": False, "error": resp.error or "bills_calendar not verified"}
        value = resp.value if isinstance(resp.value, dict) else {}
        return {"ok": True, "entries": value.get("entries", []),
                "total_cents": value.get("total_cents", 0)}

    def _experts_readout(self) -> dict[str, Any]:
        """A read-only expert-selection + cost readout for the live tree.

        For each domain (a registered capability on a node) it deterministically
        picks the expert kind (deterministic / learned / human) using the tree's
        configured verifiers to know which domains are verifiable, and records a
        nominal per-run cost so the readout shows an honest total. This is the
        "Network of Experts AI" observer: it shows how each part of the network
        would be served — read-only, never mutating state.
        """
        from .experts import CostAccount, CostLedger, Domain, select_expert

        tree = self._driver.tree()
        # The tree-wide configured verifier names mark verifiable domains.
        verifiers: set[str] = set()
        for node in tree:
            verifiers.update(node.get("verifiers") or [])
        entries: list[dict[str, Any]] = []
        cost_ledger = CostLedger()
        for node in tree:
            identity = node.get("identity", "")
            caps = node.get("capabilities") or []
            if not caps:
                continue
            for cap in sorted(caps):
                verifiable = cap in verifiers
                domain = Domain(
                    id=f"{identity}::{cap}",
                    name=f"{identity} domain ({cap})",
                    output_fields=("value",) if verifiable else (),
                    determinism=0.8 if verifiable else 0.2,
                    residue=0.1 if verifiable else 0.9,
                )
                sel = select_expert(domain)
                cost = 10 if verifiable else 100
                cost_ledger.record(
                    CostAccount(
                        domain=domain.id, kind=sel.kind, cost=cost,
                        residue=domain.residue or 0.0,
                    )
                )
                entries.append(
                    {
                        "id": domain.id,
                        "name": domain.name,
                        "kind": sel.kind,
                        "reason": sel.reason,
                        "warranted": sel.warranted,
                        "verifiable": verifiable,
                        "cost": cost,
                    }
                )
        return {
            "ok": True,
            "domains": entries,
            "total_cost": cost_ledger.total_cost(),
        }

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
                self._record_activity("action", "run demo double(21)", verified=True)
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
    # ---- base / layout ----
    "* { box-sizing: border-box; }",
    "body { margin:0; font-family: system-ui, -apple-system, Segoe UI, sans-serif;",
    "  background:#f5f6fa; color:#1a1a2e; }",
    ".app { display:flex; min-height:100vh; }",
    # ---- sidebar ----
    ".sidebar { width:240px; flex-shrink:0; background:#111827; color:#e5e7eb;",
    "  display:flex; flex-direction:column; position:sticky; top:0; height:100vh; }",
    ".sidebar .brand { padding:1.2rem 1.2rem .6rem; font-weight:700; font-size:1.02rem;",
    "  color:#fff; letter-spacing:.2px; }",
    ".sidebar .brand small { display:block; font-weight:400; color:#9ca3af;",
    "  font-size:.72rem; margin-top:.15rem; }",
    ".sidebar nav { flex:1; overflow-y:auto; padding:.5rem .6rem; }",
    ".side-section { font-size:.68rem; text-transform:uppercase; letter-spacing:.06em;",
    "  color:#6b7280; padding:.7rem .8rem .3rem; }",
    ".side-link { display:flex; align-items:center; gap:.55rem; width:100%;",
    "  padding:.55rem .8rem; margin:.05rem 0; border:0; background:transparent;",
    "  color:#d1d5db; border-radius:8px; cursor:pointer; font-size:.88rem; text-align:left; }",
    ".side-link:hover { background:#1f2937; color:#fff; }",
    ".side-link.active { background:#374151; color:#fff; font-weight:600; }",
    ".side-link .ico { width:1.1rem; text-align:center; }",
    ".sidebar .foot { padding:.8rem 1rem; font-size:.72rem; color:#6b7280;",
    "  border-top:1px solid #1f2937; }",
    # ---- main ----
    ".main { flex:1; min-width:0; padding:1.6rem 2rem 3rem; }",
    ".main header { margin-bottom:1.4rem; }",
    ".main header h1 { margin:0 0 .25rem; font-size:1.5rem; font-weight:700; }",
    ".main header p { margin:0; color:#6b7280; font-size:.92rem; }",
    # ---- stat cards (overview) ----
    ".stats { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr));",
    "  gap:1rem; margin-bottom:1.4rem; }",
    ".stat { background:#fff; border:1px solid #e5e7eb; border-radius:12px;",
    "  padding:1rem 1.1rem; box-shadow:0 1px 3px rgba(0,0,0,.04); }",
    ".stat .k { font-size:.75rem; text-transform:uppercase; letter-spacing:.04em;",
    "  color:#6b7280; }",
    ".stat .v { font-size:1.5rem; font-weight:700; margin-top:.2rem; }",
    ".stat .v.green { color:#16a34a; } .stat .v.blue { color:#2563eb; }",
    # ---- dashboard pane visibility (sidebar-driven) ----
    "#pane-dashboard, #pane-chat, #pane-provision, #pane-registry,",
    "#pane-designer, #pane-docs { display:none; }",
    "#pane-dashboard.active, #pane-chat.active, #pane-provision.active,",
    "#pane-registry.active, #pane-designer.active, #pane-docs.active",
    "{ display:block; }",
    ".docs { line-height:1.65; }",
    ".docs h2 { margin-top:1.4rem; }",
    ".docs code { background:#f3f4f6; padding:.15rem .35rem; border-radius:4px;",
    "  font-size:.88em; }",
    ".docs ul { padding-left:1.2rem; }",
    "# ---- shared existing styles (kept) ----",
    "h1 { font-size: 1.6rem; } h2 { font-size: 1.15rem; margin-top: 1.5rem; }",
    "table { border-collapse: collapse; width: 100%; }",
    "th,td { text-align:left; padding:.4rem .6rem; border-bottom:1px solid #ddd; }",
    ".pill { display:inline-block; background:#eee; border-radius:999px;",
    "  font-size:.8rem; padding:.15rem .6rem; }",
    ".actions a { display:inline-block; margin-right:.6rem;",
    "  padding:.5rem .9rem; background:#0057ff; color:#fff; text-decoration:none;",
    "  border-radius:6px; }",
    ".invariants li { margin:.25rem 0; } .error { color:#b00020; } .note { color:#555; }",
    ".spinner { display:inline-block; width:1rem; height:1rem; border:2px solid #ccc;",
    "  border-top-color:#0057ff; border-radius:50%; animation:spin .6s linear infinite;",
    "  vertical-align:middle; margin-left:.5rem; }",
    "@keyframes spin { to { transform: rotate(360deg); } }",
    "#model-result { white-space:pre-wrap; }",
    ".chat-history { border:1px solid #ddd; border-radius:6px; padding:.5rem .8rem;",
    "  margin:.5rem 0 1rem; max-height:18rem; overflow:auto; background:#fff; }",
    ".chat-turn { padding:.35rem 0; border-bottom:1px solid #f0f0f0; }",
    ".chat-turn:last-child { border-bottom:none; }",
    ".net-node { display:inline-block; background:#e8f0fe; border:1px solid #b6cdf5;",
    "  border-radius:6px; padding:.3rem .7rem; margin:.25rem; font-family:monospace; }",
    ".net-edge { color:#555; font-family:monospace; font-size:.85rem;",
    "  margin:.15rem 0 .15rem 1rem; }",
    ".net-panel { border:1px solid #ddd; border-radius:6px; padding:.6rem;",
    "  margin:.5rem 0; background:#fff; }",
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
    ".mode-switch { display:none; }",  # replaced by sidebar nav
    ".card { background:#fff; border:1px solid #e5e7eb; border-radius:12px;",
    "  box-shadow:0 1px 3px rgba(0,0,0,.04); padding:1rem 1.2rem; margin:1rem 0; }",
    ".card h2 { margin-top:0; }",
    ".card-grid { display:grid; grid-template-columns:1fr 1fr; gap:1rem; }",
    "@media (max-width:900px){ .card-grid { grid-template-columns:1fr; }",
    "  .app { flex-direction:column; } .sidebar { width:100%; height:auto;",
    "    position:static; } }",
    ".card .note { margin-top:.2rem; }",
    ".schema-field { margin:.4rem 0; }",
    ".schema-field label { display:block; font-size:.85rem; color:#333; margin-bottom:.15rem; }",
    ".schema-field input { width:60%; padding:.35rem .5rem; border:1px solid #ccc;",
    "  border-radius:6px; font-family:monospace; }",
    # ---- docs panel ----
    ".docs { line-height:1.6; }",
    ".docs h2 { margin-top:1.4rem; }",
    ".docs code { background:#f3f4f6; padding:.15rem .35rem; border-radius:4px;",
    "  font-size:.88em; }",
    ".docs ul { padding-left:1.2rem; }",
    # ---- designer / component network editor ----
    ".net-grid { display:grid; grid-template-columns:220px 1fr 240px; gap:1rem;",
    "  align-items:start; }",
    "@media (max-width:1000px){ .net-grid { grid-template-columns:1fr; } }",
    ".net-palette { background:#f8fafc; border:1px solid #e5e7eb; border-radius:12px;",
    "  padding:.8rem; }",
    ".net-palette h4 { margin:.3rem 0 .6rem; font-size:.8rem; text-transform:uppercase;",
    "  letter-spacing:.04em; color:#6b7280; }",
    ".net-pal-group { margin-bottom:.7rem; }",
    ".net-pal-group .plabel { font-size:.72rem; color:#9ca3af; margin:.3rem 0 .2rem; }",
    ".pal-item { display:block; width:100%; text-align:left; padding:.45rem .6rem;",
    "  margin:.15rem 0; border:1px solid #e2e4e8; border-radius:8px; background:#fff;",
    "  cursor:pointer; font-size:.85rem; }",
    ".pal-item:hover { border-color:#5b8def; background:#eef4ff; }",
    ".pal-item .ptask { font-weight:600; font-family:monospace; }",
    ".pal-item .pdesc { display:block; color:#6b7280; font-size:.75rem; }",
    ".net-stage { position:relative; }",
    ".net-toolbar { display:flex; flex-wrap:wrap; gap:.4rem; margin-bottom:.6rem;",
    "  align-items:center; }",
    ".net-toolbar button { background:#fff; border:1px solid #d1d5db; border-radius:8px;",
    "  padding:.4rem .7rem; cursor:pointer; font-size:.82rem; }",
    ".net-toolbar button:hover { background:#f3f4f6; }",
    ".net-toolbar .primary { background:#0057ff; color:#fff; border-color:#0057ff; }",
    ".net-canvas { position:relative; border:1px solid #e5e7eb; border-radius:12px;",
    "  background-image:radial-gradient(#e2e8f0 1px, transparent 1px);",
    "  background-size:22px 22px; height:460px; overflow:hidden; cursor:grab;",
    "  touch-action:none; }",
    ".net-canvas.panning { cursor:grabbing; }",
    ".net-node-el { position:absolute; background:#fff; border:1.5px solid #5b8def;",
    "  border-radius:10px; box-shadow:0 2px 6px rgba(0,0,0,.08); padding:.5rem .7rem;",
    "  font-family:monospace; font-size:.85rem; cursor:grab; user-select:none;",
    "  min-width:96px; transition:box-shadow .12s; }",
    ".net-node-el.sel { border-color:#111827; box-shadow:0 0 0 2px rgba(17,24,39,.15); }",
    ".net-node-el.err { border-color:#dc2626; }",
    ".net-node-el.ok { border-color:#16a34a; }",
    ".net-node-title { display:flex; align-items:center; gap:.4rem; font-weight:600;",
    "  font-size:.8rem; }",
    ".net-node-task { color:#6b7280; font-size:.72rem; }",
    ".net-node-val { margin-top:.3rem; font-size:.8rem; color:#111827;",
    "  background:#f3f4f6; border-radius:6px; padding:.15rem .4rem; white-space:nowrap;",
    "  overflow:hidden; text-overflow:ellipsis; max-width:150px; }",
    ".net-node-el .net-port { display:inline-block; width:12px; height:12px;",
    "  border-radius:50%; background:#5b8def; cursor:crosshair; vertical-align:middle; }",
    ".net-node-el .net-port.in { background:#22a06b; }",
    ".net-node-el .net-port:hover { transform:scale(1.3); }",
    ".net-svg { position:absolute; top:0; left:0; overflow:visible; pointer-events:none; }",
    ".net-edge-path { pointer-events:stroke; stroke:#94a3b8; stroke-width:2; fill:none;",
    "  cursor:pointer; }",
    ".net-wire-preview { stroke:#5b8def; stroke-width:2.5; stroke-dasharray:6 4;",
    "  pointer-events:none; }",
    ".net-edge-path.sel { stroke:#111827; stroke-width:3; }",
    ".net-inspector { background:#f8fafc; border:1px solid #e5e7eb; border-radius:12px;",
    "  padding:.9rem; }",
    ".net-inspector h4 { margin:0 0 .6rem; font-size:.8rem; text-transform:uppercase;",
    "  letter-spacing:.04em; color:#6b7280; }",
    ".insp-field { margin:.5rem 0; }",
    ".insp-field label { display:block; font-size:.78rem; color:#4b5563; }",
    ".insp-field input { width:100%; padding:.35rem .5rem; border:1px solid #d1d5db;",
    "  border-radius:6px; font-family:monospace; font-size:.8rem; }",
    ".insp-sec { border-top:1px solid #e5e7eb; margin-top:.7rem; padding-top:.6rem; }",
    ".net-status { font-family:monospace; font-size:.78rem; color:#6b7280; margin-top:.6rem;",
    "  white-space:pre-wrap; }",
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


# The Network-of-Experts readout client script: shows which kind of expert would
# serve each domain (deterministic / learned / human) and the running cost.
_EXPERTS_JS = r"""\
<script>
  async function expertsLoad() {
    const box = document.getElementById('experts-list');
    const total = document.getElementById('experts-total');
    if (!box) return;
    try {
      const r = await fetch('/experts');
      const data = await r.json();
      box.innerHTML = '';
      if (!data.ok || !data.domains) { box.textContent = 'unavailable.'; return; }
      const kinds = {deterministic: 'deterministic', learned: 'learned (SLM)', human: 'human'};
      for (const d of data.domains) {
        const div = document.createElement('div');
        div.className = 'chat-turn';
        const warrant = d.warranted ? ' <span class=\'error\'>[warrants SLM]</span>' : '';
        div.innerHTML = '<b>' + esc(d.name) + '</b> → ' +
          '<span class=\'pill\'>' + esc(kinds[d.kind] || d.kind) + '</span>' +
          ' · ' + esc(d.cost) + ' cost' + warrant;
        box.appendChild(div);
      }
      if (total) total.textContent = data.total_cost;
    } catch (e) { box.textContent = 'readout unavailable.'; }
  }
  expertsLoad();
</script>
"""

# The Domain Registry readout client script.
_DOMAINS_JS = r"""\
<script>
  async function domainsLoad() {
    const box = document.getElementById('domains-list');
    if (!box) return;
    try {
      const r = await fetch('/domains');
      const data = await r.json();
      box.innerHTML = '';
      if (!data.ok || !data.domains) { box.textContent = 'no domains observed.'; return; }
      const kinds = {deterministic: 'deterministic', learned: 'learned (SLM)', human: 'human'};
      for (const d of data.domains) {
        const div = document.createElement('div');
        div.className = 'chat-turn';
        const achievable = d.achievable ? '' : ' <span class=\'error\'>unverifiable</span>';
        div.innerHTML = '<b>' + esc(d.id) + '</b> <span class=\'pill\'>' +
          esc(kinds[d.expert_kind] || d.expert_kind) + '</span>' + achievable +
          ' · ' + esc(d.tenant);
        box.appendChild(div);
      }
    } catch (e) { box.textContent = 'registry unavailable.'; }
  }
  domainsLoad();
</script>
"""

# The Domain SLM (learned-expert recommendation) readout script.
_SLM_JS = r"""\
<script>
  async function slmLoad() {
    const box = document.getElementById('slm-list');
    if (!box) return;
    try {
      const r = await fetch('/slm');
      const data = await r.json();
      box.innerHTML = '';
      if (!data.ok || !data.warranted) { box.textContent = 'no domains observed.'; return; }
      for (const d of data.warranted) {
        const div = document.createElement('div');
        div.className = 'chat-turn';
        const badge = d.warranted ? ' <span class=\'error\'>[warrants SLM]</span>' : '';
        div.innerHTML = '<b>' + esc(d.id) + '</b> → <span class=\'pill\'>' +
          esc(d.kind) + '</span> ' + badge;
        box.appendChild(div);
      }
    } catch (e) { box.textContent = 'recommendation unavailable.'; }
  }
  slmLoad();
</script>
"""

# The artifact vault readout script (append-only, write-once evidence).
_ARTIFACTS_JS = r"""\
<script>
  async function artifactsLoad() {
    const box = document.getElementById('artifacts-list');
    const total = document.getElementById('artifacts-total');
    if (!box) return;
    try {
      const r = await fetch('/artifacts');
      const data = await r.json();
      box.innerHTML = '';
      if (!data.ok || !data.artifacts) { box.textContent = 'no artifacts recorded.'; return; }
      for (const a of data.artifacts) {
        const div = document.createElement('div');
        div.className = 'chat-turn';
        div.innerHTML = '<b>' + esc(a.domain) + '</b> run ' + esc(a.run) +
          ' · <span class=\'pill\'>' + esc(a.kind) + '</span> · ' +
          esc(a.cost) + ' cost · value ' + esc(JSON.stringify(a.value));
        box.appendChild(div);
      }
      if (total) total.textContent = data.total_cost;
    } catch (e) { box.textContent = 'repository unavailable.'; }
  }
  artifactsLoad();
</script>
"""

# The expert-provisioning client script: select the domain, pick a base model,
# a (stub) provider and a cost budget, then run one deterministic provisioning
# pass (plan -> train -> account -> settle), offline and fail-closed.
_PROVISION_JS = r"""\
<script>
  const $provOut = () => document.getElementById('prov-result');
  const $provSpin = () => document.getElementById('prov-spinner');
  const $provBtn = () => document.getElementById('prov-run');

  const provKinds = {
    'deterministic': 'deterministic',
    'learned': 'learned (SLM)',
    'human': 'human'
  };
  const provTierLabels = {
    'cpu': 'CPU / tiny',
    'single-gpu': 'Single GPU',
    'multi-gpu': 'Multi-GPU'
  };

  async function provLoadDomains() {
    const sel = document.getElementById('prov-domain');
    if (!sel) return;
    try {
      const r = await fetch('/provision/domains');
      const data = await r.json();
      sel.innerHTML = '';
      const domains = (data && data.domains) || [];
      if (!domains.length) {
        const o = document.createElement('option'); o.value = 'bill-extract';
        o.textContent = 'bill-extract (bill extraction)'; sel.appendChild(o);
        return;
      }
      for (const d of domains) {
        const o = document.createElement('option');
        o.value = d.id;
        o.textContent = d.id + ' (' + (provKinds[d.kind] || d.kind) + ') ' +
          (d.warranted ? '[SLM]' : '');
        sel.appendChild(o);
      }
    } catch (e) { /* best-effort; the default option remains */ }
  }

  let provCatalog = [];

  async function provLoadCatalog() {
    const sel = document.getElementById('prov-model');
    if (!sel) return;
    try {
      const r = await fetch('/catalog');
      const data = await r.json();
      provCatalog = (data && data.bases) || [];
      // Arc the bases by size class (smallest to largest).
      const order = ['edge', 'small', 'mid', 'large'];
      provCatalog.sort((a, b) =>
        order.indexOf(a.size_class) - order.indexOf(b.size_class) || a.params - b.params);
      sel.innerHTML = '';
      let selectedDefault = false;
      for (const b of provCatalog) {
        const o = document.createElement('option');
        o.value = b.id;
        const cls = b.size_class || 'mid';
        const label = b.id + ' (' + cls + ', ' + b.params + 'B)';
        o.textContent = label;
        o.title = (b.note || '') + ' min tier: ' + (b.min_tier || '');
        if (!selectedDefault && b.id === 'llama-3.2-3b') {
          o.selected = true; selectedDefault = true;
        }
        sel.appendChild(o);
      }
      provShowTier();
    } catch (e) { /* best-effort */ }
  }

  function provShowTier() {
    const sel = document.getElementById('prov-model');
    const el = document.getElementById('prov-tier');
    if (!sel || !el) return;
    const id = sel.value;
    const b = provCatalog.find(x => x.id === id);
    const tier = b ? (b.min_tier || '') : '';
    el.textContent = provTierLabels[tier] ? ('Expected tier: ' + provTierLabels[tier]) : '';
  }

  async function provRun() {
    const out = $provOut(); const spin = $provSpin(); const btn = $provBtn();
    out.textContent = ''; out.className = 'note';
    if (spin) spin.style.display = 'inline-block';
    if (btn) btn.disabled = true;
    const sel = document.getElementById('prov-domain');
    const model = document.getElementById('prov-model');
    const budgetEl = document.getElementById('prov-budget');
    const payload = {
      domain: (sel && sel.value) || 'bill-extract',
      provider: 'stub',
      base_model: (model && model.value) || 'llama-3.2-3b',
      budget: budgetEl && budgetEl.value !== '' ? Number(budgetEl.value) : null
    };
    try {
      const r = await fetch('/provision', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(payload)
      });
      const data = await r.json();
      if (data.ok) {
        const res = data.result;
        let lines = 'selection: ' + res.selection +
          '\nplan tier: ' + (res.plan || '—');
        if (res.expert) {
          lines += '\nexpert artifact: ' + res.expert.artifact +
            ' (' + res.expert.method + ' base ' + res.expert.base_model + ')' +
            ' dataset ' + res.expert.dataset_size;
        }
        lines += '\naccount cost: ' + res.account.cost;
        if (res.settlement) {
          lines += '\nsettlement: ' + res.settlement.status + ' ' +
            res.settlement.amount + ' (' + res.settlement.settlement_id + ')';
        } else {
          lines += '\nsettlement: — (no grant / no charge)';
        }
        out.textContent = 'Provisioning ok:\n' + lines;
      } else {
        out.textContent = 'Provisioning failed (fail-closed): ' +
          (data.error || 'unknown');
        out.className = 'error';
      }
    } catch (err) {
      out.textContent = 'request failed: ' + err; out.className = 'error';
    } finally {
      if (spin) spin.style.display = 'none';
      if (btn) btn.disabled = false;
    }
  }

  document.getElementById('prov-run').addEventListener('click', provRun);
  const modelSel = document.getElementById('prov-model');
  if (modelSel) modelSel.addEventListener('change', provShowTier);
  provLoadDomains();
  provLoadCatalog();
</script>
"""

# The Component Network editor client script (kept out of the f-string so its
# JS object braces are not mistaken for f-string interpolations).
_NETWORK_JS = r"""\
<script>
  // A full-featured, dependency-free component-network editor. Nodes are
  // absolutely-positioned divs; edges are SVG paths. The editor serializes to
  // the ComponentNetwork JSON the verified spine runs. No libs, no CDN.
  const netPalette = [
    {group: 'Arithmetic', items: [
      {task: 'double', desc: 'value * 2', args: {value: 1}},
      {task: 'square', desc: 'value * value', args: {value: 3}},
      {task: 'negate', desc: 'value * -1', args: {value: 5}},
    ]},
    {group: 'Derived', items: [
      {task: 'sum', desc: 'a + b', args: {a: 1, b: 1}},
      {task: 'product', desc: 'a * b', args: {a: 2, b: 3}},
      {task: 'concat', desc: 'a + b (str)', args: {a: 'x', b: 'y'}},
    ]},
    {group: 'Checks', items: [
      {task: 'even', desc: 'is value even?', args: {value: 0}},
      {task: 'odd', desc: 'is value odd?', args: {value: 1}},
      {task: 'positive', desc: 'is value > 0?', args: {value: 1}},
    ]},
  ];
  const verifierChoices = ['', 'even', 'odd', 'positive'];

  const netState = {components: [], edges: []};
  const netLayout = {};
  let netSeq = 1;
  let netPending = null;
  let netWire = null;   // in-progress drag-and-drop connector
  let netSel = null;
  let netDrag = null;
  let netPan = null;
  let netZoom = 1;
  let netResult = {};
  const $netSpin = () => document.getElementById('net-spinner');
  const $netSvg = () => document.getElementById('net-svg');
  const $netCanvas = () => document.getElementById('net-canvas');
  const $netStatus = () => document.getElementById('net-status');

  function netNodeEl(id) { return document.getElementById('net-n-' + id); }

  function netBuildPalette() {
    const box = document.getElementById('net-pal');
    if (!box) return;
    box.innerHTML = '';
    netPalette.forEach(g => {
      const wrap = document.createElement('div');
      wrap.className = 'net-pal-group';
      wrap.innerHTML = '<div class="plabel">' + g.group + '</div>';
      g.items.forEach(it => {
        const b = document.createElement('button');
        b.type = 'button'; b.className = 'pal-item';
        b.innerHTML = '<span class="ptask">' + it.task + '</span>' +
          '<span class="pdesc">' + it.desc + '</span>';
        b.addEventListener('click', () => netAddComponent(it.task, it.args));
        wrap.appendChild(b);
      });
      box.appendChild(wrap);
    });
  }

  function netPlaceId() {
    const n = netState.components.length;
    return {x: 40 + (n % 3) * 220, y: 40 + Math.floor(n / 3) * 130};
  }

  function netAddComponent(task, args) {
    const id = 'c' + netSeq++;
    netState.components.push({id: id, task: task, args: Object.assign({}, args || {})});
    netLayout[id] = netPlaceId();
    netRender();
  }

  function netRemoveComponent(id) {
    netState.components = netState.components.filter(c => c.id !== id);
    netState.edges = netState.edges.filter(e => e.source !== id && e.target !== id);
    delete netLayout[id];
    delete netResult[id];
    if (netPending && netPending.sourceNode === id) netPending = null;
    if (netWire && netWire.sourceNode === id) netWire = null;
    if (netSel === id) { netSel = null; netRenderInspector(); }
    netRender();
  }

  function netClear() {
    netState.components = [];
    netState.edges = [];
    Object.keys(netLayout).forEach(k => delete netLayout[k]);
    netPending = null; netWire = null; netSel = null; netResult = {};
    netRender(); netRenderInspector();
    netSetStatus('Empty network - add components from the palette.');
  }

  function netSetStatus(t) { if ($netStatus()) $netStatus().textContent = t; }

  function netNodeInputs(id) {
    const c = netState.components.find(x => x.id === id);
    if (!c || !c.args) return [];
    return Object.keys(c.args);
  }

  function netPortPos(id, which) {
    const el = netNodeEl(id);
    if (!el) return {x: 0, y: 0};
    const r = el.getBoundingClientRect();
    const cr = $netCanvas().getBoundingClientRect();
    const x = (r.left - cr.left) + (which === 'in' ? 0 : r.width);
    const y = (r.top - cr.top) + r.height / 2;
    return {x: x / netZoom, y: y / netZoom};
  }

  // Convert a client-space point to canvas (unzoomed) coordinates.
  function netClientToCanvas(clientX, clientY) {
    const cr = $netCanvas().getBoundingClientRect();
    return {x: (clientX - cr.left) / netZoom, y: (clientY - cr.top) / netZoom};
  }

  // Start a drag-and-drop connector from an output port.
  function netWireStart(id, ev) {
    ev.stopPropagation();
    ev.preventDefault();
    const s = netPortPos(id, 'out');
    netWire = {sourceNode: id, sx: s.x, sy: s.y, x: s.x, y: s.y};
    netSelect(id);
    netSetStatus('Drag to an input port to connect.');
    netRenderEdges();
  }

  // Update the live preview line while dragging.
  function netWireMove(ev) {
    if (!netWire) return;
    const p = netClientToCanvas(ev.clientX, ev.clientY);
    netWire.x = p.x; netWire.y = p.y;
    netRenderEdges();
  }

  // Complete the edge if released over an input port, else cancel.
  function netWireEnd(ev) {
    if (!netWire) return;
    const target = document.elementFromPoint(ev.clientX, ev.clientY);
    const inPort = target && target.classList && target.classList.contains('net-port')
      && target.classList.contains('in');
    if (inPort) {
      const el = target.closest('.net-node-el');
      const tid = el ? el.id.replace('net-n-', '') : null;
      if (tid && tid !== netWire.sourceNode) {
        netState.edges.push({source: netWire.sourceNode, source_field: 'value',
          target: tid, target_arg: 'value'});
        netResult = {};
        netSetStatus('Connected ' + netWire.sourceNode + ' -> ' + tid + '.');
      }
    }
    netWire = null;
    netRender();
  }

  function netSelect(id, ev) {
    if (ev) ev.stopPropagation();
    netSel = id;
    netRender(); netRenderInspector();
  }

  function netRenderInspector() {
    const box = document.getElementById('net-insp');
    if (!box) return;
    if (!netSel) { box.innerHTML = 'Select a node to edit it.'; return; }
    const c = netState.components.find(x => x.id === netSel);
    if (!c) { box.innerHTML = 'Select a node.'; return; }
    let h = '<div class="insp-field"><label>id</label><input disabled value="' + c.id + '"/></div>';
    h += '<div class="insp-field"><label>task</label>' +
      '<input disabled value="' + c.task + '"/></div>';
    const keys = Object.keys(c.args || {});
    keys.forEach(k => {
      h += '<div class="insp-field"><label>arg - ' + k + '</label>' +
        '<input data-arg="' + k + '" value="' + String(c.args[k]) + '"' +
        ' onchange="netSetArg(\'' + c.id + '\',\'' + k + '\',this.value)"/></div>';
    });
    if (!keys.length) h += '<p class="note">This task has no editable inputs.</p>';
    h += '<div class="insp-sec"><label>verifier</label>' +
      '<select data-verf onchange="netSetVer(this)">' +
      verifierChoices.map(v => '<option value="' + v + '"' + (c.verifier === v ? ' selected' : '') +
        '>' + (v || '- none -') + '</option>').join('') + '</select></div>';
    h += '<div class="insp-sec"><label>child (delegate)</label><input value="' + (c.child || '') +
      '" onchange="netSetChild(this)"/></div>';
    h += '<div class="insp-sec"><button type="button" onclick="netRemoveComponent(\'' + c.id +
      '\')">Delete node</button></div>';
    box.innerHTML = h;
  }

  function netSetArg(id, key, raw) {
    const c = netState.components.find(x => x.id === id);
    if (!c) return;
    const v = (raw || '').trim();
    c.args[key] = (v === '' || isNaN(Number(v))) ? v : Number(v);
    netRender();
  }
  function netSetVer(sel) {
    const c = netState.components.find(x => x.id === netSel);
    if (c) { c.verifier = sel.value || null; netRender(); }
  }
  function netSetChild(inp) {
    const c = netState.components.find(x => x.id === netSel);
    if (c) { c.child = (inp.value || '').trim() || null; netRender(); }
  }

  function netRender() {
    const canvas = $netCanvas();
    const svg = $netSvg();
    canvas.querySelectorAll('.net-node-el').forEach(n => n.remove());
    canvas.style.transform = 'scale(' + netZoom + ')';
    canvas.style.transformOrigin = '0 0';
    netState.components.forEach(c => {
      const p = netLayout[c.id] || {x: 20, y: 20};
      if (!netLayout[c.id]) netLayout[c.id] = p;
      const el = document.createElement('div');
      el.className = 'net-node-el' +
        (netSel === c.id ? ' sel' : '') +
        (netResult[c.id] && netResult[c.id].verified ? ' ok' : '') +
        (netResult[c.id] && !netResult[c.id].verified ? ' err' : '');
      el.id = 'net-n-' + c.id;
      el.style.left = p.x + 'px';
      el.style.top = p.y + 'px';
      el.innerHTML =
        '<span class="net-port in"></span>' +
        '<span class="net-node-title">' + c.id +
        '<span class="net-node-task">' + c.task + '</span></span>' +
        (netResult[c.id] ?
          '<div class="net-node-val">' +
          (netResult[c.id].verified ? 'check ' : 'x ') +
          String(netResult[c.id].value === undefined ? '' : netResult[c.id].value) +
          '</div>' : '') +
        '<span class="net-port out"></span>';
      el.addEventListener('mousedown', (ev) => {
        if (ev.target.classList.contains('net-port')) return;
        ev.stopPropagation();
        netSelect(c.id);
        netDrag = {id: c.id, dx: ev.clientX - p.x * netZoom, dy: ev.clientY - p.y * netZoom};
        ev.preventDefault();
      });
      el.addEventListener('click', (ev) => { ev.stopPropagation(); netSelect(c.id); });
      el.addEventListener('dblclick', (ev) => { ev.stopPropagation(); netRemoveComponent(c.id); });
      el.querySelector('.net-port.out').addEventListener('mousedown', (ev) => {
        netWireStart(c.id, ev);
      });
      el.querySelector('.net-port.out').addEventListener('click', (ev) => {
        ev.stopPropagation();
        netPending = {sourceNode: c.id};
        netSelect(c.id);
        netSetStatus('Now click the input port of the target node.');
      });
      el.querySelector('.net-port.in').addEventListener('click', (ev) => {
        ev.stopPropagation();
        if (!netPending || netPending.sourceNode === c.id) {
          netPending = null; netRender(); return; }
        netState.edges.push({source: netPending.sourceNode, source_field: 'value',
          target: c.id, target_arg: 'value'});
        netPending = null; netResult = {};
        netRender();
      });
      canvas.appendChild(el);
    });
    netRenderEdges();
  }

  function netRenderEdges() {
    const svg = $netSvg();
    let paths = '';
    netState.edges.forEach(e => {
      const s = netPortPos(e.source, 'out');
      const t = netPortPos(e.target, 'in');
      const mx = (s.x + t.x) / 2;
      paths += '<path class="net-edge-path" d="M' + s.x + ',' + s.y + ' C' + mx + ',' + s.y +
        ' ' + mx + ',' + t.y + ' ' + t.x + ',' + t.y + '" data-ed="' + netEdgeKey(e) + '"/>';
    });
    if (netWire) {
      const s = {x: netWire.sx, y: netWire.sy};
      const t = {x: netWire.x, y: netWire.y};
      const mx = (s.x + t.x) / 2;
      paths += '<path class="net-edge-path net-wire-preview" d="M' + s.x + ',' + s.y +
        ' C' + mx + ',' + s.y + ' ' + mx + ',' + t.y + ' ' + t.x + ',' + t.y + '"/>';
    }
    svg.innerHTML = paths;
    svg.setAttribute('width', $netCanvas().clientWidth || 600);
    svg.setAttribute('height', $netCanvas().clientHeight || 460);
  }

  function netEdgeKey(e) {
    return e.source + '::' + e.source_field + '::' + e.target + '::' + e.target_arg;
  }

  $netCanvas().addEventListener('mousedown', (ev) => {
    if (ev.target === $netCanvas()) {
      netPan = {x0: ev.clientX, y0: ev.clientY};
      $netCanvas().classList.add('panning');
    }
  });
  document.addEventListener('mouseup', (ev) => {
    if (netWire) { netWireEnd(ev); return; }
    netPan = null; netDrag = null; $netCanvas().classList.remove('panning');
  });
  document.addEventListener('mousemove', (ev) => {
    if (netWire) { netWireMove(ev); return; }
    if (netDrag) {
      const el = netNodeEl(netDrag.id); if (!el) return;
      const p = {x: (ev.clientX - netDrag.dx) / netZoom, y: (ev.clientY - netDrag.dy) / netZoom};
      netLayout[netDrag.id] = p;
      el.style.left = p.x + 'px'; el.style.top = p.y + 'px';
      netRenderEdges();
    }
  });
  $netCanvas().addEventListener('wheel', (ev) => {
    ev.preventDefault();
    netZoom = Math.min(2.5, Math.max(0.5, netZoom * (ev.deltaY < 0 ? 1.1 : 0.9)));
    netRender();
  }, {passive: false});

  async function netRun() {
    const spin = $netSpin();
    netResult = {};
    netRender();
    if (netState.components.length === 0) { netSetStatus('Nothing to run.'); return; }
    if (spin) spin.style.display = 'inline-block';
    netSetStatus('Running via the verified spine...');
    try {
      const r = await fetch('/network', {
        method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(netState)
      });
      const data = await r.json();
      if (data.results) data.results.forEach(s => {
        netResult[s.id] = {verified: s.verified, value: s.value};
      });
      if (data.ok) netSetStatus('Network ok (' + data.completed + ' step(s))');
      else netSetStatus('Network FAILED: ' + (data.error || 'unverified step'));
    } catch (err) {
      netSetStatus('request failed: ' + err);
    } finally {
      if (spin) spin.style.display = 'none';
    }
    netRender();
  }

  async function netSave() {
    const ni = document.getElementById('net-name');
    const name = ni ? ni.value.trim() : '';
    if (!name) { netSetStatus('Enter a network name to save.'); return; }
    const r = await fetch('/network/save', {
      method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: name, network: netState})
    });
    const data = await r.json();
    if (data.ok) { netRefreshList(); netSetStatus('Saved "' + name + '".'); }
    else netSetStatus('Save failed: ' + (data.error || 'unknown'));
  }

  async function netRefreshList() {
    try {
      const sel = document.getElementById('net-load-sel');
      if (!sel) return;
      const r = await fetch('/network/list');
      const data = await r.json();
      sel.innerHTML = '';
      (data.names || []).forEach(n => {
        const o = document.createElement('option'); o.value = n; o.textContent = n;
        sel.appendChild(o);
      });
    } catch (e) { /* best-effort */ }
  }

  async function netLoad() {
    const sel = document.getElementById('net-load-sel');
    const name = sel && sel.value;
    if (!name) { netSetStatus('Select a saved network to load.'); return; }
    const r = await fetch('/network/load/' + encodeURIComponent(name));
    const data = await r.json();
    if (data.ok) {
      netState.components = (data.network.components || []).map(c => Object.assign({}, c));
      netState.edges = (data.network.edges || []).map(e => Object.assign({}, e));
      Object.keys(netLayout).forEach(k => delete netLayout[k]);
      netState.components.forEach((c, i) => {
        netLayout[c.id] = {x: 40 + (i % 3) * 220, y: 40 + Math.floor(i / 3) * 130};
      });
      netResult = {}; netSel = null; netPending = null;
      netRender(); netRenderInspector();
      netSetStatus('Loaded "' + name + '".');
    } else netSetStatus('Load failed: ' + (data.error || 'unknown'));
  }

  function netAutoLayout() {
    const col = {};
    netState.components.forEach(c => col[c.id] = 0);
    let changed = true, guard = 0;
    while (changed && guard++ < 100) {
      changed = false;
      netState.edges.forEach(e => {
        if (col[e.target] <= col[e.source]) { col[e.target] = col[e.source] + 1; changed = true; }
      });
    }
    const buckets = {};
    netState.components.forEach(c => {
      const cv = col[c.id] || 0;
      (buckets[cv] = buckets[cv] || []).push(c.id);
    });
    Object.keys(buckets).forEach(k => {
      const colX = 40 + Number(k) * 230;
      let row = 0;
      buckets[k].sort().forEach(id => {
        netLayout[id] = {x: colX, y: 40 + row * 130};
        row++;
      });
    });
    netRender();
    netSetStatus('Auto-layout applied.');
  }

  document.getElementById('net-run').addEventListener('click', netRun);
  document.getElementById('net-clear').addEventListener('click', netClear);
  document.getElementById('net-layout').addEventListener('click', netAutoLayout);
  document.getElementById('net-save').addEventListener('click', netSave);
  document.getElementById('net-load').addEventListener('click', netLoad);
  netBuildPalette();
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

# The operator activity-feed script (kept out of the f-string so its JS
# object braces are not mistaken for f-string interpolations).
_ACTIVITY_JS = r"""\
<script>
  (async () => {
    const box = document.getElementById('activity-list');
    if (!box) return;
    try {
      const r = await fetch('/activity');
      const data = await r.json();
      box.innerHTML = '';
      const entries = data.entries || [];
      if (!entries.length) { box.textContent = 'no activity recorded yet.'; return; }
      for (const e of entries) {
        const div = document.createElement('div');
        div.className = 'chat-turn';
        const badge = e.verified ? '\u2713' : '\u2717';
        div.innerHTML = '<b>' + esc(e.kind) + '</b> ' + esc(e.action) +
          ' <span class=\'pill\'>' + badge + '</span>';
        box.appendChild(div);
      }
    } catch (err) { box.textContent = 'activity unavailable.'; }
  })();
</script>
"""

# The sidebar navigation script: one page = one visible pane.
_NAV_JS = r"""\
<script>
  const PANES = ['dashboard', 'chat', 'provision', 'registry', 'designer', 'docs'];
  function navShow(page) {
    for (const p of PANES) {
      const pane = document.getElementById('pane-' + p);
      if (pane) pane.classList.toggle('active', p === page);
    }
    document.querySelectorAll('.side-link').forEach(l =>
      l.classList.toggle('active', l.dataset.page === page));
  }
  document.querySelectorAll('.side-link').forEach(l => {
    l.addEventListener('click', () => {
      const page = l.dataset.page;
      if (page === 'refresh') { window.location.reload(); return; }
      if (page) navShow(page);
    });
  });
  // Default pane is the dashboard (overview).
  navShow('dashboard');
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
<title>Agent-Centric FBP — Dashboard</title>
<style>{_PAGE_CSS}</style></head>
<body>
<div class='app'>

<aside class='sidebar'>
  <div class='brand'>Agent-Centric<small>FBP subsystem · deterministic</small></div>
  <nav>
    <div class='side-section'>Workspace</div>
    <button class='side-link active' data-page='dashboard' type='button'>
      <span class='ico'>⊙</span>Dashboard</button>
    <button class='side-link' data-page='chat' type='button'>
      <span class='ico'>💬</span>Chat</button>
    <button class='side-link' data-page='provision' type='button'>
      <span class='ico'>⚙️</span>Provision</button>
    <button class='side-link' data-page='registry' type='button'>
      <span class='ico'>🗂️</span>Registry</button>
    <div class='side-section'>Design</div>
    <button class='side-link' data-page='designer' type='button'>
      <span class='ico'>🕸️</span>Designer</button>
    <div class='side-section'>Learn</div>
    <button class='side-link' data-page='docs' type='button'>
      <span class='ico'>📚</span>Docs</button>
  </nav>
  <div class='foot'>Local-first · verified spine · fail-closed</div>
</aside>

<main class='main'>

<header>
  <h1>Agent-Centric · FBP</h1>
  <p>A deterministic, agent-centric, flow-based subsystem. A rooted tree of agents;
  work flows <b>down</b> as directives, responsibility bubbles <b>up</b> — each parent
  re-verifies a child's value before accepting it.</p>
  {err}
</header>

<section id='pane-dashboard'>
  <div class='stats'>
    <div class='stat'><div class='k'>Runs</div>
      <div class='v blue'>{summary.get('run_count', 0)}</div></div>
    <div class='stat'><div class='k'>Verified</div>
      <div class='v green'>{summary.get('verified_runs', 0)}</div></div>
    <div class='stat'><div class='k'>Identities</div>
      <div class='v'>{len(identities)}</div></div>
    <div class='stat'><div class='k'>Mode</div><div class='v'>FBP</div></div>
  </div>
  {action_note}
  {err}
  <div class='card'>
    <h2>Live agent tree</h2>
    <table><thead><tr><th>Identity</th><th>Kind</th><th>Capabilities</th><th>Grants</th></tr></thead>
    <tbody>{rows}</tbody></table>
  </div>
  <div class='card'>
    <h2>Session summary</h2>
    <p class='pill'>{caps}</p>
    <p>Identities: {len(identities)} · {' · '.join(identities) if identities else '—'}</p>
  </div>
  <div class='card'>
    <h2>Activity feed</h2>
    <p class='note'>A bounded, append-only audit of operator actions — each
    recorded through the verified spine. Read-only; see
    <a href='/activity'>/activity</a> for the JSON.</p>
    <div id='activity-list' class='chat-history'></div>
    {_ACTIVITY_JS}
  </div>
  <div class='card'>
    <h2>Actions</h2>
    <div class='actions'>
      <a href='/'>Refresh</a>
      <a href='/action/run'>Run a deterministic demo action</a>
      <a href='/ledger'>View ledger</a>
      <a href='/state.json'>state.json</a>
    </div>
    {action_note}
  </div>
</section>

<section id='pane-chat'>

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
</section>

<section id='pane-registry'>

<div class='card'>
<h2>Network of Experts</h2>
<p class='note'>Each component is a <b>domain</b>; this read-only view picks which
kind of expert serves it — <b>deterministic</b> method, <b>learned</b> (a
per-domain SLM), or <b>human</b> — and shows the running cost. The model is not
the expert; the network is.</p>
<div id='experts-list' class='chat-history'></div>
<p class='note'>Total cost: <span id='experts-total'>—</span></p>
{_EXPERTS_JS}
</div>

<div class='card'>
<h2>Domain SLM (learned experts)</h2>
<p class='note'>Which domains <b>warrant</b> a per-domain expert SLM? This is the
deterministic <code>select_expert</code> recommendation — the <b>learned</b> tier,
chosen only when a deterministic method can't cover the residue. Building the
actual is an opt-in provider (<code>fbp/slm.py</code>); this only shows the
recommendation.</p>
<div id='slm-list' class='chat-history'></div>
{_SLM_JS}
</div>

<div class='card'>
<h2>Domain Registry</h2>
<p class='note'>The read-only <b>catalog</b> of domains (tenant-aware): id, name,
contract, and the chosen expert kind. It records <i>what</i> and <i>how</i>;
it never decides — authority stays in the tree topology.</p>
<div id='domains-list' class='chat-history'></div>
{_DOMAINS_JS}
</div>

<div class='card'>
<h2>Artifact Vault</h2>
<p class='note'>The read-only, <b>write-once evidence</b> of what each domain
produced — each run's verified output, source refs, residue, and cost. Keyed by
(tenant, domain, run); never mutable.</p>
<div id='artifacts-list' class='chat-history'></div>
<p class='note'>Total cost: <span id='artifacts-total'>—</span></p>
{_ARTIFACTS_JS}

</div>
</section>

<section id='pane-provision'>

<div class='card'>
<h2>Provision a domain expert</h2>
<p class='note'>Run one deterministic provisioning pass from the live tree:
<b>select</b> the expert kind → <b>plan</b> the hardware tier → <b>train</b>
(opt-in provider; offline stub by default) → <b>account</b> cost → <b>settle</b>
under an explicit grant. Real providers never run here; unverifiable domains,
over-budget plans, and missing grants fail closed.</p>
<label for='prov-domain'>Domain</label>
<select id='prov-domain' class='pill'></select>
<label for='prov-model'>Base model</label>
<select id='prov-model' class='pill'></select>
<span id='prov-tier' class='note' style='margin-left:.6rem'></span>
<br/>
<label for='prov-budget'>Budget</label>
<input id='prov-budget' type='number' placeholder='100 (optional cost cap)'/>
<br/>
<button id='prov-run' type='button'>Provision (stub, offline)</button>
<span id='prov-spinner' class='spinner' style='display:none'></span>
<pre id='prov-result' class='note'></pre>
{_PROVISION_JS}
</div>
</section>

<section id='pane-designer'>

<div class='card'>
<h2>Component Network (visual programming)</h2>
<p class='note'>Wire components into a directed data-flow graph. Each component is an FBP
<code>task</code>; each edge feeds a target's input from a source's output. The
network compiles to an ordered plan and runs through the verified spine — cycles
and unknown references fail closed.</p>

<div class='net-grid'>

  <div class='net-palette'>
    <h4>Component palette</h4>
    <div id='net-pal'></div>
    <div class='insp-sec'><h4 style='margin-top:0'>Saved</h4>
      <input id='net-name' placeholder='network name'/>
      <div class='net-toolbar' style='margin-top:.4rem'>
        <button id='net-save' type='button'>Save</button>
        <button id='net-load' type='button'>Load</button>
        <select id='net-load-sel'></select>
      </div>
    </div>
  </div>

  <div class='net-stage'>
    <div class='net-toolbar'>
      <button id='net-layout' type='button'>⇲ Auto-layout</button>
      <button id='net-clear' type='button'>Clear</button>
      <button id='net-run' class='primary' type='button'>▶ Run network</button>
      <span id='net-spinner' class='spinner' style='display:none'></span>
      <span class='net-hint'>drag bg to pan·wheel to zoom·dbl-click a node to delete</span>
    </div>
    <div id='net-canvas' class='net-canvas'>
      <svg id='net-svg'></svg>
    </div>
    <div id='net-status' class='net-status'>Empty network — add components, then wire ports.</div>
  </div>

  <div class='net-inspector'>
    <h4>Inspector</h4>
    <div id='net-insp'>Select a node to edit its inputs, verifier, and child.</div>
  </div>

</div>
{_NETWORK_JS}
</div>
</section>

<section id='pane-docs'>
<div class='card docs'>
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

  <h2>Network of Experts AI</h2>
  <p><i>The model is not the expert; the network is.</i> An AI is a network of
  narrow domain experts, each verified by a deterministic verifier. Every
  component of a component network is a <b>domain</b> served by the best
  expert kind: <b>deterministic</b> method, <b>learned</b> (a per-domain SLM), or
  <b>human</b> — chosen by <code>select_expert</code>, accounted by
  <code>CostLedger</code>, evidenced in the Artifact Vault.</p>

  <h2>The three tiers</h2>
  <ul>
    <li><b>Deterministic</b> — a hand-tuned method; cheapest and fully auditable.</li>
    <li><b>Learned</b> — a per-domain SLM (opt-in external provider), planned by
        the training tier and built from a recommended base-model catalog.</li>
    <li><b>Paid</b> — micro-payment settlement under an explicit operator grant;
        the natural home for a future pay-for-service.</li>
  </ul>

  <h2>Domain Registry + Artifact Vault</h2>
  <p>The registry is a <b>passive catalog</b>, never an authority — authority
  stays in the tree topology. The vault is <b>write-once evidence</b>:
  append-only, keyed by (tenant, domain, run), never mutable.</p>

  <h2>Provisioning</h2>
  <p>Provisioning is an explicit <b>grant</b>, never a reflex. The flow is
  <code>select → plan → train → account → settle</code>; every stage is
  deterministic and fail-closed. The plan picks the hardware tier from corpus
  size, method, <b>and the base model's size-class floor</b>.</p>
</div>
</section>

</main>
</div>
{_NAV_JS}
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
    bills_path: str | os.PathLike[str] | None = None,
    registry_path: str | os.PathLike[str] | None = None,
    activity_path: str | os.PathLike[str] | None = None,
) -> None:
    """Serve the FBP landing page (blocking). Pass --open to open a browser.

    ``history_path`` optionally grants a durable, cross-restart chat-history
    store (an explicit opt-in; without it the transcript is in-memory only).
    ``networks_path`` optionally grants durable, cross-restart storage for
    saved component networks (an explicit opt-in; without it they are
    in-memory only). ``bills_path`` optionally grants a durable, cross-restart
    bills registry (an explicit opt-in; without it the registry is in-memory
    only). ``registry_path`` optionally grants durable, cross-restart storage
    for the Domain Registry + artifact vault.
    ``activity_path`` optionally grants a durable, cross-restart operator
    activity feed (an explicit opt-in; without it the feed is in-memory only).
    """
    server = FbpLandingServer(
        host=host, port=port, history_path=history_path, networks_path=networks_path,
        bills_path=bills_path, registry_path=registry_path,
        activity_path=activity_path,
    )
    if open_browser:
        url = f"http://{host}:{port}"
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
    server.serve_forever()