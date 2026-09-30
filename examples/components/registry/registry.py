"""Reference implementation of the example ``registry`` component entry.

This is the code a distributed bundle would ship. It is **documentation** only:
the harness executes the allowlisted in-tree entry
``agent_centric.cbp.registry_component:catalog_snapshot`` (see
``component_runtime.py``), which is byte-for-byte this behavior.
"""

from __future__ import annotations

from typing import Any


def catalog_snapshot(payload: Any) -> dict[str, Any]:
    """Validate and canonicalize a passive capability catalog (pure)."""
    if not isinstance(payload, dict):
        raise TypeError("catalog_snapshot payload must be a mapping")
    raw = payload.get("catalog")
    if not isinstance(raw, list):
        raise TypeError("catalog must be a list")
    entries: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise TypeError("catalog entry must be a mapping")
        name = item.get("name")
        if not isinstance(name, str) or not name:
            raise TypeError("catalog entry name must be a non-empty string")
        entries.append(
            {
                "name": name,
                "source_url": str(item.get("source_url", "")),
                "kind": str(item.get("kind", "python")),
            }
        )
    entries.sort(key=lambda entry: entry["name"])
    return {"catalog": entries}
