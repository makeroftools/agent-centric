"""Design contract (versioned) — ``design.v1`` (SPEC-0008).

A **design** is the declarative authoring artifact: a ``network.v1`` graph plus
the component references it *requests* (a name and a tag/range/commit). It is
what a human (via n8n) or an agent **composes or generates**; it carries no
execution authority. Requested references are resolved to immutable commits and
verified only at **pin time** (SPEC-0007 §4), so a design never floats at run
time.

The document is canonical and content-hashable (``design_hash``): the same design
always serializes to the same bytes. Contracts remain **additive-only**.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any

DESIGN_SCHEMA = "design.v1"


@dataclass(frozen=True)
class RequestedComponent:
    """A component a design *requests* (resolved to a commit only at pin time).

    Attributes:
        name: The component name.
        requested: A tag, range, or commit — opaque here; the pinner resolves it.
    """

    name: str
    requested: str

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Requested component name must be non-empty.")
        if not self.requested:
            raise ValueError("Requested component ref must be non-empty.")

    def to_dict(self) -> dict[str, str]:
        return {"name": self.name, "requested": self.requested}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RequestedComponent:
        return cls(name=str(data["name"]), requested=str(data["requested"]))


@dataclass(frozen=True)
class EnvelopeGrant:
    """A per-component hard resource envelope declared by a design (SPEC-0008 §4).

    ``bounds`` is a JSON-ready ``ResourceEnvelope`` payload (``step_limit``,
    ``size_cap``, ``latency_seconds``, ``child_limit``, ``extra``). The contract
    stores only the declarative payload so it stays pure and additive; the
    bounds are constructed and validated fail-closed as a runtime
    ``ResourceEnvelope`` at design-validation time (``cbp.design``).
    """

    component: str
    bounds: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.component:
            raise ValueError("Envelope grant component must be non-empty.")
        if not isinstance(self.bounds, dict):
            raise ValueError("Envelope grant bounds must be a mapping.")

    def to_dict(self) -> dict[str, Any]:
        return {"component": self.component, "bounds": dict(self.bounds)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnvelopeGrant:
        if not isinstance(data, dict):
            raise ValueError("Envelope grant must be an object.")
        return cls(
            component=str(data.get("component", "")),
            bounds=dict(data.get("bounds") or {}),
        )


@dataclass(frozen=True)
class Design:
    """An immutable ``design.v1`` document."""

    id: str
    network: dict[str, Any]
    title: str = ""
    components: tuple[RequestedComponent, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    envelopes: tuple[EnvelopeGrant, ...] = ()
    schema: str = DESIGN_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != DESIGN_SCHEMA:
            raise ValueError(f"Unsupported design schema: {self.schema!r}")
        if not self.id:
            raise ValueError("Design id must be non-empty.")
        if not isinstance(self.network, dict):
            raise ValueError("Design network must be a mapping.")
        names = [c.name for c in self.components]
        if len(names) != len(set(names)):
            raise ValueError("Design requested component names must be unique.")
        envelopes = [e.component for e in self.envelopes]
        if len(envelopes) != len(set(envelopes)):
            raise ValueError("Design envelope components must be unique.")

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "id": self.id,
            "title": self.title,
            "network": self.network,
            "components": [c.to_dict() for c in self.components],
            "metadata": dict(self.metadata),
        }
        # Additive: omit when empty so a design without envelopes keeps its
        # exact prior canonical bytes (and thus its design_hash).
        if self.envelopes:
            data["envelopes"] = [e.to_dict() for e in self.envelopes]
        return data

    def canonical_bytes(self) -> bytes:
        """Canonical, deterministic serialization used for hashing."""
        return json.dumps(
            self.to_dict(), sort_keys=True, separators=(",", ":")
        ).encode("utf-8")

    def design_hash(self) -> str:
        """The content address of this design (sha256, hex)."""
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Design:
        try:
            schema = str(data["schema"])
            design_id = str(data["id"])
            network = data["network"]
        except KeyError as exc:
            raise ValueError(f"Design missing field: {exc!r}") from exc
        if not isinstance(network, dict):
            raise ValueError("Design network must be a mapping.")
        raw_components = data.get("components", [])
        if not isinstance(raw_components, list):
            raise ValueError("Design components must be a list.")
        metadata = data.get("metadata") or {}
        if not isinstance(metadata, dict):
            raise ValueError("Design metadata must be a mapping.")
        raw_envelopes = data.get("envelopes", [])
        if not isinstance(raw_envelopes, list):
            raise ValueError("Design envelopes must be a list.")
        return cls(
            id=design_id,
            network=network,
            title=str(data.get("title", "")),
            components=tuple(
                RequestedComponent.from_dict(c) for c in raw_components
            ),
            metadata=dict(metadata),
            envelopes=tuple(
                EnvelopeGrant.from_dict(e) for e in raw_envelopes
            ),
            schema=schema,
        )
