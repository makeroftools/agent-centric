"""Deterministic design validation and compilation (SPEC-0008 Phase 1).

A :class:`~agent_centric.contracts.design.Design` is **untrusted input**: it is
validated deterministically and **fail-closed**, then compiled to a canonical
``network.v1`` graph. Nothing here touches the network or executes a component —
component **availability** is verified at pin time (SPEC-0007 §4), not here.

Two surfaces:

- :func:`validate_design` returns a deterministic :class:`DesignReport` (it never
  raises on content), so a UI or an agent can surface errors.
- :func:`compile_design` is the hard gate: it raises :class:`DesignError` unless
  the design is fully valid, and returns a content-hashed :class:`CompiledDesign`.

The same design always validates to the same report and compiles to the same
``design_hash`` — the property that makes authoring reproducible and diffable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..contracts.design import Design
from .envelopes import ResourceEnvelope
from .network import ComponentNetwork, NetworkError, network_from_dict

DEFAULT_MAX_COMPONENTS = 512

# The recognised resource-envelope bound keys (the ``ResourceEnvelope``
# surface). ``extra`` is a reserved free-form map for future bounds.
_ENVELOPE_BOUND_KEYS = (
    "step_limit",
    "size_cap",
    "latency_seconds",
    "child_limit",
)
_ENVELOPE_EXTRA_KEY = "extra"


class DesignError(ValueError):
    """A design is invalid and cannot be compiled (fail-closed)."""


@dataclass(frozen=True)
class DesignReport:
    """The deterministic outcome of validating a design."""

    ok: bool
    errors: tuple[str, ...]
    component_ids: tuple[str, ...]


@dataclass(frozen=True)
class CompiledDesign:
    """A validated design compiled to a canonical, content-hashed network."""

    design_hash: str
    network: ComponentNetwork
    plan: tuple[dict[str, Any], ...]
    component_ids: tuple[str, ...]


def validate_design(
    design: Design, *, max_components: int = DEFAULT_MAX_COMPONENTS
) -> DesignReport:
    """Validate a design deterministically and fail-closed.

    Checks the schema (enforced by the contract), that the network parses and
    validates (edges, self-loops, acyclicity), that it is fully wired (no unknown
    output fields), that it is non-empty and within the component ceiling, that
    requested refs are well-formed, and that every declared envelope limit is a
    valid, non-empty, unique grant on a real component. Availability is **not**
    checked here.

    Returns:
        A :class:`DesignReport`; ``ok`` is true only when ``errors`` is empty.
    """
    errors: list[str] = []
    try:
        network = network_from_dict(design.network)
    except NetworkError as exc:
        return DesignReport(ok=False, errors=(f"network: {exc}",), component_ids=())

    components = network.components()
    component_ids = tuple(component.id for component in components)
    if not components:
        errors.append("network has no components")
    if len(components) > max_components:
        errors.append(
            f"network has {len(components)} components, exceeding the "
            f"{max_components}-component limit"
        )
    for requested in design.components:
        if not requested.name.strip():
            errors.append("requested component name is blank")

    # Envelope limits (SPEC-0008 §4): a declared per-component hard resource
    # envelope must reference a real component, be unique, declare at least one
    # bound, and construct a valid runtime ``ResourceEnvelope``. Fail-closed.
    component_set = set(component_ids)
    seen_envelopes: set[str] = set()
    for grant in design.envelopes:
        comp = grant.component
        label = f"envelope for {comp!r}"
        if comp not in component_set:
            errors.append(f"{label} references an unknown component")
        if comp in seen_envelopes:
            errors.append(f"duplicate {label}")
        seen_envelopes.add(comp)
        bounds = grant.bounds
        unknown = sorted(
            set(bounds) - {*_ENVELOPE_BOUND_KEYS, _ENVELOPE_EXTRA_KEY}
        )
        if unknown:
            errors.append(f"{label} has unsupported bound key(s) {unknown}")
            continue
        if not any(bounds.get(key) is not None for key in _ENVELOPE_BOUND_KEYS):
            errors.append(f"{label} declares no resource bounds")
            continue
        if _ENVELOPE_EXTRA_KEY in bounds and not isinstance(
            bounds[_ENVELOPE_EXTRA_KEY], dict
        ):
            errors.append(f"{label}.extra must be a mapping")
            continue
        try:
            ResourceEnvelope.from_payload(dict(bounds))
        except (ValueError, TypeError) as exc:
            errors.append(f"{label}: {exc}")

    try:
        network.validate()
        network.compile()
    except NetworkError as exc:
        errors.append(f"network: {exc}")

    return DesignReport(ok=not errors, errors=tuple(errors), component_ids=component_ids)


def compile_design(
    design: Design, *, max_components: int = DEFAULT_MAX_COMPONENTS
) -> CompiledDesign:
    """Compile a design to its canonical network and content hash (fail-closed).

    Raises:
        DesignError: If the design is invalid (with the deterministic error list).
    """
    report = validate_design(design, max_components=max_components)
    if not report.ok:
        raise DesignError("; ".join(report.errors))
    network = network_from_dict(design.network)
    return CompiledDesign(
        design_hash=design.design_hash(),
        network=network,
        plan=tuple(network.compile()),
        component_ids=report.component_ids,
    )
