"""Pure, allowlisted behavior for the example ``registry`` component.

The registry is the **passive catalog** (``cbp/registry.py``): it holds metadata
(a name and a location) and nothing else — it does not fetch, compile, or run.
This module is the *executable* form of that catalog: a deterministic, pure
snapshot of a catalog payload, safe to compare, hash, and replay.

Like the example ``bills_component`` entries, this runs only because it is
installed in the harness and allowlisted (``component_runtime``); the distributed
bundle may ship reference code for documentation, but execution is always the
allowlisted in-tree entry.
"""

from __future__ import annotations

from typing import Any


class RegistryComponentError(TypeError):
    """A registry catalog payload is malformed (fail-closed)."""


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise RegistryComponentError(f"{name} must be a mapping")
    return value


def catalog_snapshot(payload: Any) -> dict[str, Any]:
    """Validate and canonicalize a passive capability catalog (pure).

    Payload: ``{"catalog": [{"name", "source_url", "kind"}, ...]}``. The result
    is sorted by name, so the snapshot is deterministic and stable. A missing or
    malformed catalog fails closed.
    """
    data = _mapping(payload, "catalog_snapshot payload")
    raw = data.get("catalog")
    if not isinstance(raw, list):
        raise RegistryComponentError("catalog must be a list")
    entries: list[dict[str, str]] = []
    for item in raw:
        entry = _mapping(item, "catalog entry")
        name = entry.get("name")
        if not isinstance(name, str) or not name:
            raise RegistryComponentError("catalog entry name must be a non-empty string")
        source_url = entry.get("source_url", "")
        kind = entry.get("kind", "python")
        if not isinstance(source_url, str) or not isinstance(kind, str):
            raise RegistryComponentError("catalog entry source_url/kind must be strings")
        entries.append({"name": name, "source_url": source_url, "kind": kind})
    entries.sort(key=lambda catalog_entry: catalog_entry["name"])
    return {"catalog": entries}
