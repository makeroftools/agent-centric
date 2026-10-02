"""UI targets (SPEC-0022) — a minimal, target-agnostic ``web`` + ``cli`` renderer.

A **UI target** is an *edge*, never a node in the execution tree: a renderer plus a
sandbox for one medium. It consumes the **target-neutral tree** a ``ui.v1``
component's ``render`` produces and emits that medium deterministically. This module
implements the two targets the SPEC-0022 roadmap calls for -- ``web`` and ``cli`` --
over reference components, proving the ABI is **target-agnostic**: the *same*
manifest and the *same* pinned projection render to each medium, byte-identically on
every run. It is pure, offline, and stdlib-only (no framework dependency in core).

Fail-closed by construction (SPEC-0022 §6, §8, §9):

- **Verify before execute.** A component's manifest is strict-ABI-validated and its
  content address is carried into the result before its view code runs; an unknown
  ABI version or a projection it did not declare refuses.
- **Provenance-tiered sandbox.** Provenance sets the capability tier: ``trusted``
  may hold its tightly-scoped declared capabilities; ``appointed`` runs **A0** with
  zero capabilities; ``discovered`` is refused unless explicitly allowed. The tier
  is recorded; the view is handed only its granted projection (no ambient authority).
- **Default deny.** An undeclared projection or directive is refused. A directive is
  a content-addressed **intent**; the target mutates nothing.
- **Pin before render.** A composition has a content address computed *before* any
  render. Unknown or target/ABI-incompatible components are **skipped**, and the
  surface **degrades to the raw projection** instead of failing whole.
- **Honest provenance.** A projection carries its assurance status, and every medium
  renders that status truthfully (an unverified claim never renders as verified).

A true OS/wasm capability sandbox is the *target host's* boundary (SPEC-0022 §8);
core provides the tiering, the default-deny boundaries, and the deterministic
projection -- never ambient authority.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..contracts.ui import (
    Assurance,
    DirectiveBinding,
    PinnedUIRef,
    UIComponent,
    UITarget,
    assert_directive_declared,
    assert_projection_granted,
    validate_ui,
)

SURFACE_SCHEMA = "cbp.ui.surface.v1"
ASSEMBLY_SCHEMA = "cbp.ui.composition.v1"
PROJECTION_SCHEMA = "cbp.projection.v1"
TREE_SCHEMA = "cbp.ui.tree.v1"

TREE_PROJECTION_SCHEMA = "cbp.web.tree.v1"
LEDGER_PROJECTION_SCHEMA = "cbp.web.ledger.v1"
DIAGRAM_PROJECTION_SCHEMA = "cbp.diagram.v1"

# The targets this module implements. ``os``/``embedded`` remain declared in the
# ABI but are not built here; requesting them refuses rather than guessing.
AVAILABLE_TARGETS: tuple[str, ...] = (UITarget.WEB, UITarget.CLI)

_SCHEMA_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")

_ASSURANCE_SEVERITY = {
    Assurance.VERIFIED: 0,
    Assurance.MODEL: 1,
    Assurance.UNVERIFIED: 2,
    Assurance.REFUSED: 3,
}
_PROVENANCE_SEVERITY = {"trusted": 0, "appointed": 1, "discovered": 2}


class UIError(ValueError):
    """A UI edge refused an input (fail-closed; explicit, never partial)."""


class SandboxError(UIError):
    """A provenance tier refused to run the UI (fail-closed)."""


# ---------------------------------------------------------------------------
# canonical helpers
# ---------------------------------------------------------------------------


def _canonical_bytes(document: Any) -> bytes:
    return json.dumps(document, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _canonical_text(document: Any) -> str:
    return json.dumps(document, sort_keys=True, separators=(",", ":"))


def _content_hash(document: Any) -> str:
    return hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    return _canonical_text(value)


# ---------------------------------------------------------------------------
# the projection boundary (read side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Projection:
    """A pinned, provenance-honest document a UI may display (SPEC-0022 §4).

    ``content_hash`` is the sha256 of the canonical document (``{schema, data}``);
    ``assurance`` is the honest verification status of that document.
    """

    schema: str
    data: Any
    assurance: str = Assurance.VERIFIED
    content_hash: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.schema, str) or not _SCHEMA_RE.match(self.schema):
            raise UIError(f"projection schema must be a lowercase id: {self.schema!r}")
        if self.assurance not in Assurance.all():
            raise UIError(f"unknown projection assurance: {self.assurance!r}")
        computed = self.compute_hash()
        if self.content_hash and self.content_hash != computed:
            raise UIError("projection content_hash does not match its document")
        object.__setattr__(self, "content_hash", computed)

    def document(self) -> dict[str, Any]:
        """The pinned document the hash covers."""
        return {"schema": self.schema, "data": self.data}

    def compute_hash(self) -> str:
        return _content_hash(self.document())

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": self.schema,
            "content_hash": self.content_hash,
            "assurance": self.assurance,
            "data": self.data,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Projection:
        return cls(
            schema=str(data["schema"]),
            data=data.get("data"),
            assurance=str(data.get("assurance", Assurance.VERIFIED)),
            content_hash=str(data.get("content_hash", "")),
        )

    @classmethod
    def of(cls, schema: str, data: Any, *, assurance: str = Assurance.VERIFIED) -> Projection:
        return cls(schema=schema, data=data, assurance=assurance)


# ---------------------------------------------------------------------------
# the directive boundary (effect side)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Directive:
    """A content-addressed intent emitted by a UI (SPEC-0022 §5).

    It records the manifest's ``requires_approval`` flag but **executes nothing**;
    the verified spine runs it, and irreversible acts keep their operator gates.
    """

    name: str
    args: dict[str, Any] = field(default_factory=dict)
    requires_approval: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not _SCHEMA_RE.match(self.name):
            raise UIError(f"directive name must be a lowercase id: {self.name!r}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": "ui.directive.v1",
            "name": self.name,
            "args": dict(self.args),
            "requires_approval": self.requires_approval,
        }

    def canonical_bytes(self) -> bytes:
        return _canonical_bytes({"name": self.name, "args": self.args})

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()


def submit_directive(
    component: UIComponent, name: str, args: Mapping[str, Any] | None = None
) -> Directive:
    """Build the content-addressed intent for a declared directive (default deny).

    An undeclared directive refuses before any intent is produced. This function is
    pure: it never mutates platform state and never executes the directive.
    """
    binding: DirectiveBinding = assert_directive_declared(component, name)
    return Directive(name=name, args=dict(args or {}), requires_approval=binding.requires_approval)


# ---------------------------------------------------------------------------
# the provenance-tiered sandbox
# ---------------------------------------------------------------------------


class Provenance:
    """Where a UI component came from (drives its sandbox tier; SPEC-0015)."""

    TRUSTED = "trusted"
    APPOINTED = "appointed"
    DISCOVERED = "discovered"

    __slots__ = ()

    @classmethod
    def all(cls) -> tuple[str, ...]:
        return (cls.TRUSTED, cls.APPOINTED, cls.DISCOVERED)

    @classmethod
    def is_known(cls, provenance: str) -> bool:
        return provenance in cls.all()


@dataclass(frozen=True)
class SandboxPolicy:
    """The capability tier a UI runs under. Zero ambient authority, always."""

    provenance: str
    capabilities: tuple[str, ...]
    ambient_authority: bool = False


def sandbox_policy(
    provenance: str,
    component: UIComponent,
    *,
    allow_discovered: bool = False,
) -> SandboxPolicy:
    """Resolve the sandbox tier for ``provenance`` (fail-closed).

    ``trusted`` may hold its declared capabilities; ``appointed`` runs A0 (zero
    capabilities); ``discovered`` is refused unless explicitly allowed.
    """
    if not Provenance.is_known(provenance):
        raise SandboxError(f"unknown UI provenance: {provenance!r}")
    if provenance == Provenance.DISCOVERED and not allow_discovered:
        raise SandboxError(
            "discovered UI is never auto-trusted; refusing (fail-closed). "
            "Pass allow_discovered=True only under an explicit operator gate."
        )
    if provenance == Provenance.TRUSTED:
        capabilities = tuple(sorted(cap.name for cap in component.capabilities))
    else:
        capabilities = ()  # A0: appointed (and opted-in discovered) hold nothing
    return SandboxPolicy(provenance=provenance, capabilities=capabilities)


# ---------------------------------------------------------------------------
# the target-neutral tree
# ---------------------------------------------------------------------------


def tree_view(
    title: str,
    *,
    schema: str,
    assurance: str,
    provenance: str,
    children: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    """The root of a target-neutral tree (canonical, medium-independent)."""
    return {
        "node": "view",
        "title": title,
        "schema": schema,
        "assurance": assurance,
        "provenance": provenance,
        "children": [dict(child) for child in children],
    }


def tree_heading(text: str, level: int = 1) -> dict[str, Any]:
    return {"node": "heading", "text": text, "level": int(level)}


def tree_text(text: str) -> dict[str, Any]:
    return {"node": "text", "text": text}


def tree_field(label: str, value: Any) -> dict[str, Any]:
    return {"node": "field", "label": label, "value": _as_text(value)}


def tree_list(items: Iterable[Any], *, ordered: bool = False) -> dict[str, Any]:
    return {"node": "list", "items": [_as_text(item) for item in items], "ordered": bool(ordered)}


def tree_table(columns: Sequence[Any], rows: Iterable[Sequence[Any]]) -> dict[str, Any]:
    return {
        "node": "table",
        "columns": [_as_text(column) for column in columns],
        "rows": [[_as_text(cell) for cell in row] for row in rows],
    }


def tree_hash(tree: Mapping[str, Any]) -> str:
    """The content address of a target-neutral tree."""
    return _content_hash(dict(tree))


# ---------------------------------------------------------------------------
# the targets (renderers)
# ---------------------------------------------------------------------------


class UITargetRenderer(Protocol):
    """A renderer + sandbox for one medium (SPEC-0022 §3)."""

    @property
    def target(self) -> str: ...

    def render(
        self, tree: Mapping[str, Any], *, tokens: Mapping[str, Any] | None = None
    ) -> str: ...


class CliTarget:
    """A deterministic text renderer for the terminal (``cli`` target)."""

    @property
    def target(self) -> str:
        return UITarget.CLI

    def render(self, tree: Mapping[str, Any], *, tokens: Mapping[str, Any] | None = None) -> str:
        return "\n".join(_cli_lines(tree, color=False)) + "\n"


class WebTarget:
    """A deterministic, script-free HTML renderer for the browser (``web`` target)."""

    @property
    def target(self) -> str:
        return UITarget.WEB

    def render(self, tree: Mapping[str, Any], *, tokens: Mapping[str, Any] | None = None) -> str:
        fragment = _web_fragment(tree, tokens or {})
        return (
            "<!doctype html>\n"
            '<html lang="en"><head><meta charset="utf-8">'
            "<title>CBP UI</title>\n"
            "<style>"
            "body{font-family:sans-serif;background:#ffffff;color:#0f172a;margin:2rem}"
            ".cbp-view{border-left:4px solid var(--cbp-accent,#38bdf8);padding-left:1rem;"
            "margin-bottom:2rem}"
            "table{border-collapse:collapse}th,td{border:1px solid #cbd5e1;padding:2px 8px}"
            ".cbp-assurance{color:#64748b;font-size:.85rem}"
            "</style></head>\n"
            "<body>\n"
            f"{fragment}\n"
            "</body></html>\n"
        )


def target_binding(target: str) -> UITargetRenderer:
    """Resolve a target name to its renderer (fail-closed on an unbuilt target)."""
    if target == UITarget.WEB:
        return WebTarget()
    if target == UITarget.CLI:
        return CliTarget()
    raise UIError(
        f"UI target {target!r} is not implemented in core; "
        f"available: {list(AVAILABLE_TARGETS)}"
    )


def _cli_lines(node: Mapping[str, Any], *, color: bool) -> list[str]:
    kind = node.get("node")
    if kind == "view":
        title = str(node.get("title", ""))
        header = f"# {title}"
        if color:
            header = f"\x1b[1m{header}\x1b[0m"
        lines = [
            header,
            f"[{node.get('assurance', '')} / {node.get('provenance', '')}] "
            f"schema={node.get('schema', '')}",
        ]
        for child in node.get("children", []):
            lines.extend(_cli_lines(child, color=color))
        return lines
    if kind == "heading":
        text = f"{'#' * int(node.get('level', 1))} {node.get('text', '')}"
        return [f"\x1b[1m{text}\x1b[0m" if color else text]
    if kind == "text":
        return [str(node.get("text", ""))]
    if kind == "field":
        return [f"{node.get('label', '')}: {node.get('value', '')}"]
    if kind == "list":
        items = node.get("items", [])
        if node.get("ordered"):
            return [f"{index}. {item}" for index, item in enumerate(items, start=1)]
        return [f"- {item}" for item in items]
    if kind == "table":
        columns = [str(column) for column in node.get("columns", [])]
        rows = [[str(cell) for cell in row] for row in node.get("rows", [])]
        widths = [
            max([len(columns[index])] + [len(row[index]) for row in rows])
            for index in range(len(columns))
        ]
        def _row(cells: Sequence[str]) -> str:
            padded = [cells[i].ljust(widths[i]) for i in range(len(columns))]
            return " | ".join(padded).rstrip()
        lines = [_row(columns), "-+-".join("-" * width for width in widths)]
        lines.extend(_row(row) for row in rows)
        return lines
    raise UIError(f"unsupported UI tree node: {kind!r}")


def _web_fragment(node: Mapping[str, Any], tokens: Mapping[str, Any]) -> str:
    kind = node.get("node")
    if kind == "view":
        accent = html.escape(str(tokens.get("accent", "#38bdf8")), quote=True)
        schema = html.escape(str(node.get("schema", "")), quote=True)
        assurance = html.escape(str(node.get("assurance", "")), quote=True)
        provenance = html.escape(str(node.get("provenance", "")), quote=True)
        children = "".join(_web_fragment(child, tokens) for child in node.get("children", []))
        return (
            f'<section class="cbp-view" data-schema="{schema}" '
            f'data-assurance="{assurance}" data-provenance="{provenance}" '
            f'style="--cbp-accent:{accent}">'
            f"<h1>{html.escape(str(node.get('title', '')))}</h1>"
            f'<p class="cbp-assurance">assurance: {assurance} &middot; '
            f"provenance: {provenance}</p>"
            f"{children}</section>"
        )
    if kind == "heading":
        level = min(max(int(node.get("level", 1)), 1), 6)
        return f"<h{level}>{html.escape(str(node.get('text', '')))}</h{level}>"
    if kind == "text":
        return f"<p>{html.escape(str(node.get('text', '')))}</p>"
    if kind == "field":
        return (
            '<p class="cbp-field">'
            f'<span class="cbp-label">{html.escape(str(node.get("label", "")))}</span>: '
            f'<span class="cbp-value">{html.escape(str(node.get("value", "")))}</span></p>'
        )
    if kind == "list":
        tag = "ol" if node.get("ordered") else "ul"
        items = "".join(f"<li>{html.escape(str(item))}</li>" for item in node.get("items", []))
        return f"<{tag}>{items}</{tag}>"
    if kind == "table":
        head = "".join(
            f"<th>{html.escape(str(column))}</th>" for column in node.get("columns", [])
        )
        body = "".join(
            "<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>"
            for row in node.get("rows", [])
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"
    raise UIError(f"unsupported UI tree node: {kind!r}")


# ---------------------------------------------------------------------------
# the view boundary (what a UI component exports)
# ---------------------------------------------------------------------------


class UIView(Protocol):
    """A UI component's export surface: ``describe`` (manifest) + ``render``."""

    manifest: UIComponent

    def render(self, projection: Projection) -> dict[str, Any]: ...


class TreeView:
    """Reference UI component: projects a driver tree readout."""

    manifest = UIComponent(
        id="cbp.tree-view",
        targets=(UITarget.WEB, UITarget.CLI),
        projections=(TREE_PROJECTION_SCHEMA,),
        slots=("main",),
        tokens={"accent": "#34d399"},
    )

    def render(self, projection: Projection) -> dict[str, Any]:
        assert_projection_granted(self.manifest, projection.schema)
        data = projection.data
        if not isinstance(data, dict) or "tree" not in data:
            raise UIError("tree projection must be a {tree: [...]} document")
        nodes = data.get("tree") or []
        rows = [
            [
                node.get("identity", ""),
                node.get("kind", ""),
                ",".join(sorted(node.get("capabilities") or [])),
                ",".join(sorted(node.get("verifiers") or [])),
            ]
            for node in nodes
        ]
        return tree_view(
            "Agent tree",
            schema=projection.schema,
            assurance=projection.assurance,
            provenance=Provenance.TRUSTED,
            children=[
                tree_field("nodes", len(nodes)),
                tree_table(("identity", "kind", "capabilities", "verifiers"), rows),
            ],
        )


class LedgerView:
    """Reference UI component: projects the recorded directive ledger."""

    manifest = UIComponent(
        id="cbp.ledger-view",
        targets=(UITarget.WEB, UITarget.CLI),
        projections=(LEDGER_PROJECTION_SCHEMA,),
        slots=("main",),
        tokens={"accent": "#a78bfa"},
    )

    def render(self, projection: Projection) -> dict[str, Any]:
        assert_projection_granted(self.manifest, projection.schema)
        data = projection.data
        if not isinstance(data, dict) or "ledger" not in data:
            raise UIError("ledger projection must be a {ledger: {...}} document")
        ledger = data.get("ledger") or {}
        rows = [
            [correlation, entry.get("kind", ""), entry.get("payload", {})]
            for correlation, entry in sorted(ledger.items())
        ]
        return tree_view(
            "Directive ledger",
            schema=projection.schema,
            assurance=projection.assurance,
            provenance=Provenance.TRUSTED,
            children=[
                tree_field("entries", data.get("count", len(rows))),
                tree_table(("correlation", "kind", "payload"), rows),
            ],
        )


class DiagramView:
    """Reference UI component: projects a pinned ``cbp.diagram.v1`` document."""

    manifest = UIComponent(
        id="cbp.diagram-view",
        targets=(UITarget.WEB, UITarget.CLI),
        projections=(DIAGRAM_PROJECTION_SCHEMA,),
        directives=(DirectiveBinding("open-review"),),
        slots=("main",),
        tokens={"accent": "#38bdf8"},
    )

    def render(self, projection: Projection) -> dict[str, Any]:
        assert_projection_granted(self.manifest, projection.schema)
        data = projection.data
        if not isinstance(data, dict) or data.get("schema") != DIAGRAM_PROJECTION_SCHEMA:
            raise UIError("diagram projection must be a cbp.diagram.v1 document")
        nodes = data.get("nodes") or []
        edges = data.get("edges") or []
        return tree_view(
            f"Network {data.get('network_sha256', '')[:12]}",
            schema=projection.schema,
            assurance=projection.assurance,
            provenance=Provenance.TRUSTED,
            children=[
                tree_field("network sha256", data.get("network_sha256", "")),
                tree_field("diagram sha256", data.get("content_hash", "")),
                tree_table(
                    ("id", "task", "rank"),
                    [
                        [node.get("id", ""), node.get("task", ""), node.get("rank", "")]
                        for node in nodes
                    ],
                ),
                tree_table(
                    ("from", "to", "capacity"),
                    [
                        [edge.get("from", ""), edge.get("to", ""), edge.get("capacity", "")]
                        for edge in edges
                    ],
                ),
            ],
        )


# ---------------------------------------------------------------------------
# assembly / pinning (the read surface)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class UISection:
    """One rendered UI component inside a surface (provenance-honest)."""

    component_id: str
    ui_hash: str
    provenance: str
    capabilities: tuple[str, ...]
    projection_schema: str
    projection_hash: str
    assurance: str
    tree_hash: str
    tree: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "component_id": self.component_id,
            "ui_hash": self.ui_hash,
            "provenance": self.provenance,
            "capabilities": list(self.capabilities),
            "projection_schema": self.projection_schema,
            "projection_hash": self.projection_hash,
            "assurance": self.assurance,
            "tree_hash": self.tree_hash,
            "tree": self.tree,
        }


@dataclass(frozen=True)
class UIRef:
    """A UI component + its view bound at a provenance tier."""

    component: UIComponent
    view: UIView
    provenance: str = Provenance.TRUSTED

    def __post_init__(self) -> None:
        if not Provenance.is_known(self.provenance):
            raise UIError(f"unknown UI provenance: {self.provenance!r}")

    def identity(self) -> dict[str, Any]:
        return {
            "id": self.component.id,
            "ui_hash": self.component.ui_hash(),
            "targets": list(self.component.targets),
            "provenance": self.provenance,
        }

    def validate(self, *, target: str, allow_discovered: bool) -> SandboxPolicy:
        if target not in self.component.targets:
            raise UIError(f"UI component {self.component.id!r} does not target {target!r}")
        report = validate_ui(self.component, required_targets=(target,))
        if not report.ok:
            raise UIError("; ".join(report.errors))
        return sandbox_policy(self.provenance, self.component, allow_discovered=allow_discovered)

    def render_section(
        self, projection: Projection, *, target: str, allow_discovered: bool = False
    ) -> UISection:
        policy = self.validate(target=target, allow_discovered=allow_discovered)
        assert_projection_granted(self.component, projection.schema)
        tree = self.view.render(projection)
        if not isinstance(tree, dict) or tree.get("node") != "view":
            raise UIError(f"UI component {self.component.id!r} produced a malformed tree")
        tree = dict(tree)
        tree["provenance"] = self.provenance
        tree["assurance"] = projection.assurance
        return UISection(
            component_id=self.component.id,
            ui_hash=self.component.ui_hash(),
            provenance=self.provenance,
            capabilities=policy.capabilities,
            projection_schema=projection.schema,
            projection_hash=projection.content_hash,
            assurance=projection.assurance,
            tree_hash=tree_hash(tree),
            tree=tree,
        )


@dataclass(frozen=True)
class UISurfaceResult:
    """The deterministic outcome of rendering a UI surface to one target."""

    target: str
    rendered: bool
    output: str
    composition_hash: str
    assurance: str
    sections: tuple[UISection, ...]
    skipped: tuple[dict[str, str], ...]
    degraded: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": SURFACE_SCHEMA,
            "target": self.target,
            "rendered": self.rendered,
            "degraded": self.degraded,
            "composition_hash": self.composition_hash,
            "assurance": self.assurance,
            "sections": [section.to_dict() for section in self.sections],
            "skipped": [dict(entry) for entry in self.skipped],
        }


class UIAssembly:
    """A content-addressed UI composition, pinned before it renders (SPEC-0022 §7)."""

    def __init__(self, refs: Iterable[UIRef], *, name: str = "cbp.ui") -> None:
        self._refs = tuple(
            sorted(refs, key=lambda ref: (ref.component.id, ref.component.ui_hash()))
        )
        self._name = name
        self._identity = {
            "schema": ASSEMBLY_SCHEMA,
            "name": name,
            "components": [ref.identity() for ref in self._refs],
        }
        self._hash = _content_hash(self._identity)

    @property
    def name(self) -> str:
        return self._name

    @property
    def composition_hash(self) -> str:
        return self._hash

    def refs(self) -> tuple[UIRef, ...]:
        return self._refs

    def components(self) -> tuple[UIComponent, ...]:
        return tuple(ref.component for ref in self._refs)

    def to_dict(self) -> dict[str, Any]:
        return {**self._identity, "composition_hash": self._hash}

    def render_document(
        self,
        *,
        target: str,
        projections: Mapping[str, Projection],
        allow_discovered: bool = False,
    ) -> UISurfaceResult:
        """Render the composition to one target; skip unknown/incompatible UI.

        A composition with no applicable component **degrades to the raw
        projection** (never a guessed render). The composition hash is computed at
        construction, before any view code runs.
        """
        binding = target_binding(target)
        sections: list[UISection] = []
        skipped: list[dict[str, str]] = []
        for ref in self._refs:
            schema = next(
                (name for name in ref.component.projections if name in projections), None
            )
            if schema is None:
                skipped.append({"component": ref.component.id, "reason": "no granted projection"})
                continue
            try:
                sections.append(
                    ref.render_section(
                        projections[schema], target=target, allow_discovered=allow_discovered
                    )
                )
            except SandboxError as exc:
                skipped.append({"component": ref.component.id, "reason": str(exc)})
            except UIError as exc:
                skipped.append({"component": ref.component.id, "reason": str(exc)})
        if not sections:
            raw = _canonical_text(
                {
                    "schema": PROJECTION_SCHEMA,
                    "projections": [projections[name].to_dict() for name in sorted(projections)],
                }
            )
            return UISurfaceResult(
                target=target,
                rendered=False,
                output=raw,
                composition_hash=self._hash,
                assurance=Assurance.UNVERIFIED,
                sections=(),
                skipped=tuple(skipped),
                degraded=True,
            )
        assurance = _weakest(
            (section.assurance for section in sections), _ASSURANCE_SEVERITY, Assurance.VERIFIED
        )
        provenance = _weakest(
            (section.provenance for section in sections),
            _PROVENANCE_SEVERITY,
            Provenance.TRUSTED,
        )
        root = tree_view(
            self._name,
            schema=SURFACE_SCHEMA,
            assurance=assurance,
            provenance=provenance,
            children=[section.tree for section in sections],
        )
        return UISurfaceResult(
            target=target,
            rendered=True,
            output=binding.render(root),
            composition_hash=self._hash,
            assurance=assurance,
            sections=tuple(sections),
            skipped=tuple(skipped),
            degraded=bool(skipped),
        )

    def render(
        self, *, target: str, projection: Projection, allow_discovered: bool = False
    ) -> UISurfaceResult:
        """Render the composition against a single projection."""
        return self.render_document(
            target=target,
            projections={projection.schema: projection},
            allow_discovered=allow_discovered,
        )


def _weakest(
    values: Iterable[str], severity: Mapping[str, int], fallback: str
) -> str:
    candidates = list(values)
    if not candidates:
        return fallback
    return max(candidates, key=lambda value: severity.get(value, -1))


def pinned_ui_ref(
    component: UIComponent, *, behavior: str, target: str = UITarget.WEB
) -> PinnedUIRef:
    """Bind a UI component's content address to a behavior component (SPEC-0022 §4).

    This is presentation **metadata**, not a field on the behavior component: the
    returned document is separate and content-addressed, so changing the view
    never changes the behavior component's own content address or verified
    semantics. Fail-closed: a behavior-less or wrong-target binding refuses.
    """
    if not behavior:
        raise UIError("pinned_ui_ref requires a non-empty behavior name")
    if target not in component.targets:
        raise UIError(f"UI component {component.id!r} does not target {target!r}")
    return PinnedUIRef(
        behavior=behavior,
        ui_id=component.id,
        ui_hash=component.ui_hash(),
        target=target,
    )


def default_ui_composition() -> UIAssembly:
    """The pinned ``ui.v1`` composition of the web read surfaces (SPEC-0022 §7).

    It names the read routes (:mod:`agent_centric.cbp.web`) as UI components and
    pins them by content address before any render. Existing routes are unchanged;
    this is an additive projection.
    """
    return UIAssembly(
        [
            UIRef(TreeView.manifest, TreeView(), Provenance.TRUSTED),
            UIRef(LedgerView.manifest, LedgerView(), Provenance.TRUSTED),
            UIRef(DiagramView.manifest, DiagramView(), Provenance.TRUSTED),
        ],
        name="cbp.web.read",
    )
