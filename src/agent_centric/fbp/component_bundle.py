"""Deterministic component bundle (SPEC-0007 §3/§4, Phase 1a).

A **component bundle** is the distributable artifact: a deterministic, canonical
tar containing the component's ``component.json`` manifest plus its files. Its
``sha256`` is the immutable ``tree_sha256`` pinned in ``components.lock``.

Determinism: members are written in sorted order with fixed metadata (``mtime=0``,
``uid/gid=0``, no user/group names), so the same input always yields the same
bytes and therefore the same hash — independent of the host filesystem or the
git tar format.

A **composite** may *contain* children (embedded): a child component's files
(including its own ``component.json``) live inside the parent bundle and are
covered by the parent's ``tree_sha256``. ``list_members`` / ``read_member`` let
the graph resolver verify that an embedded child is present and is a real
``component.v1`` (not merely a data folder).
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from collections.abc import Mapping
from pathlib import Path, PurePosixPath

from ..contracts.component import ComponentManifest

BUNDLE_MANIFEST = "component.json"
_SKIP_DIRS = {".git"}


class BundleError(ValueError):
    """A bundle is malformed or unsafe (fail-closed)."""


def _check_name(name: str) -> None:
    if not name or name.startswith("/") or ".." in PurePosixPath(name).parts:
        raise BundleError(f"unsafe bundle member name: {name!r}")


def component_json(manifest: ComponentManifest) -> bytes:
    """Canonical JSON serialization of a component manifest."""
    return json.dumps(
        manifest.to_dict(), sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


def _tar_info(name: str, size: int) -> tarfile.TarInfo:
    info = tarfile.TarInfo(name=name)
    info.size = size
    info.mtime = 0
    info.mode = 0o644
    info.uid = 0
    info.gid = 0
    info.uname = ""
    info.gname = ""
    return info


def build_bundle(
    manifest: ComponentManifest, payload: Mapping[str, bytes] | None = None
) -> bytes:
    """Build a deterministic bundle from a manifest and optional payload files."""
    files: dict[str, bytes] = {BUNDLE_MANIFEST: component_json(manifest)}
    for name, data in (payload or {}).items():
        _check_name(name)
        files[name] = data
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w", format=tarfile.GNU_FORMAT) as tar:
        for name in sorted(files):
            data = files[name]
            tar.addfile(_tar_info(name, len(data)), io.BytesIO(data))
    return buffer.getvalue()


def bundle_sha256(bundle: bytes) -> str:
    """The content address of a bundle (sha256, hex)."""
    return hashlib.sha256(bundle).hexdigest()


def build_bundle_from_dir(root: Path, *, manifest_name: str = BUNDLE_MANIFEST) -> bytes:
    """Build a bundle from a component source directory (canonicalized).

    Embedded children (``children/<name>/...``) are ordinary files under the
    root, so they are included automatically and therefore covered by the
    bundle hash.
    """
    root = Path(root)
    manifest_path = root / manifest_name
    if not manifest_path.is_file():
        raise BundleError(f"no {manifest_name} in {root}")
    manifest = ComponentManifest.from_dict(json.loads(manifest_path.read_text("utf-8")))
    payload: dict[str, bytes] = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in _SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if rel.as_posix() == manifest_name:
            continue
        payload[rel.as_posix()] = path.read_bytes()
    return build_bundle(manifest, payload)


def load_manifest(bundle: bytes) -> ComponentManifest:
    """Load the ``component.v1`` manifest out of a bundle (no disk extraction)."""
    return ComponentManifest.from_dict(
        json.loads(read_member(bundle, BUNDLE_MANIFEST))
    )


def list_members(bundle: bytes) -> tuple[str, ...]:
    """Return the sorted regular-file member names of a bundle.

    Deterministic (sorted). Used to verify that an embedded child is present.
    """
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as tar:
        names = sorted(m.name for m in tar.getmembers() if m.isfile())
    return tuple(names)


def read_member(bundle: bytes, name: str) -> bytes:
    """Read one regular-file member from a bundle (fail-closed on absence).

    The member name is validated (no absolute path, no ``..``), so a caller can
    never be tricked into reading outside the bundle's logical root.
    """
    _check_name(name)
    with tarfile.open(fileobj=io.BytesIO(bundle), mode="r:") as tar:
        try:
            member = tar.getmember(name)
        except KeyError as exc:
            raise BundleError(f"bundle has no member {name!r}") from exc
        if not member.isfile():
            raise BundleError(f"bundle member {name!r} is not a regular file")
        handle = tar.extractfile(member)
        if handle is None:
            raise BundleError(f"bundle member {name!r} is not readable")
        data = handle.read()
    return data
