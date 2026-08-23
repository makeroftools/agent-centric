"""Domain-of-Experts registry + artifact repository (observability + provenance).

This is the **catalog → decision → accounting → evidence** layer that completes
the "Network of Experts AI" model. Two pure, deterministic stores:

- ``DomainRegistry`` — a **read-only catalog** of domains: id, name, contract
  (input schema + output fields), measured determinism/residue, the selected
  expert kind, and provenance. It is a *passive catalog, never an authority* —
  authority stays in the tree topology. It records *what* and *how*; it never
  decides.
- ``ArtifactRepository`` — an **append-only, write-once** store of domain run
  artifacts: the verified output of each run, keyed by ``(tenant, domain,
  run)``, carrying source refs, residue, cost, and the expert kind that served
  it. It is *evidence*, never mutable.

Both are **tenant-aware from the start**: every domain and artifact carries an
owner/tenant id. That is what makes a future paid, multi-tenant web service an
*additive* layer over this core rather than a rewrite — the service is a hosted,
shared observability + provenance layer, never the governance layer.

The module is pure and offline: it never runs a model, never grants authority,
and never mutates durable state on its own. Persistence is an explicit grant
(a caller maps these onto a durable store when it chooses a path).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .experts import EXPERT_KINDS, Domain, ExpertSelection, select_expert


class RegistryError(ValueError):
    """A registry/repository input violated its contract (fail-closed)."""


# ---------------------------------------------------------------------------
# Domain registry (read-only catalog, tenant-aware)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DomainRecord:
    """A catalog entry for one domain, as observed by the registry.

    Attributes:
        tenant: The owner/tenant id (the customer the domain belongs to).
        domain: The ``Domain`` (its contract + measured determinism/residue).
        selection: The selected expert kind + reasoning (a deterministic
            decision, recorded at observation time).
        provenance: Optional source reference (e.g. the component network or
            config that defined the domain).
    """

    tenant: str
    domain: Domain
    selection: ExpertSelection
    provenance: str = ""

    @property
    def id(self) -> str:
        return self.domain.id

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "id": self.domain.id,
            "name": self.domain.name,
            "input_schema": dict(self.domain.input_schema),
            "output_fields": list(self.domain.output_fields),
            "determinism": self.domain.determinism,
            "residue": self.domain.residue,
            "achievable": self.domain.achievable,
            "expert_kind": self.selection.kind,
            "warranted": self.selection.warranted,
            "reason": self.selection.reason,
            "provenance": self.provenance,
        }


class DomainRegistry:
    """A read-only, deterministic catalog of domains (a passive observer).

    Domains are added idempotently by ``(tenant, id)`` — re-adding replaces the
    record (a fresh observation), never duplicates. It is a catalog, not an
    authority: it records how each domain is served but never decides.
    """

    def __init__(self) -> None:
        self._records: dict[tuple[str, str], DomainRecord] = {}

    def observe(
        self,
        domain: Domain,
        *,
        tenant: str = "",
        selection: ExpertSelection | None = None,
        provenance: str = "",
    ) -> DomainRecord:
        """Record (or refresh) a domain observation for ``tenant``.

        The selection is computed deterministically from the domain unless one is
        supplied. Fail-closed: a domain with no id is rejected.
        """
        if not domain.id:
            raise RegistryError("domain requires a non-empty 'id'")
        sel = selection if selection is not None else select_expert(domain)
        record = DomainRecord(
            tenant=tenant, domain=domain, selection=sel, provenance=provenance
        )
        self._records[(tenant, domain.id)] = record
        return record

    def get(self, domain_id: str, *, tenant: str = "") -> DomainRecord | None:
        return self._records.get((tenant, domain_id))

    def all(self, *, tenant: str | None = None) -> tuple[DomainRecord, ...]:
        """Every record, sorted deterministically by (tenant, id)."""
        records = [
            r
            for (t, _), r in self._records.items()
            if tenant is None or t == tenant
        ]
        return tuple(sorted(records, key=lambda r: (r.tenant, r.id)))

    def by_expert_kind(self, kind: str, *, tenant: str | None = None) -> tuple[DomainRecord, ...]:
        """Records whose selected expert kind matches ``kind``."""
        if kind not in EXPERT_KINDS:
            raise RegistryError(
                f"unknown expert kind {kind!r}; expected one of {', '.join(EXPERT_KINDS)}"
            )
        return tuple(r for r in self.all(tenant=tenant) if r.selection.kind == kind)

    def count(self, *, tenant: str | None = None) -> int:
        return len(self.all(tenant=tenant))


# ---------------------------------------------------------------------------
# Artifact repository (append-only, write-once evidence)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Artifact:
    """One verified run artifact for a domain (write-once evidence).

    Attributes:
        tenant: The owner/tenant id.
        domain: The domain id this artifact belongs to.
        run: A stable run id (deterministic, never auto-generated).
        value: The verified output of the run.
        kind: The expert kind that served it.
        residue: The measured irreducible residue (0..1) after the run.
        cost: The additive cost of the run (caller's unit).
        sources: Optional source references (e.g. the rule id / model id that
            informed the output).
    """

    tenant: str
    domain: str
    run: str
    value: Any
    kind: str
    residue: float
    cost: int
    sources: tuple[dict[str, Any], ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tenant": self.tenant,
            "domain": self.domain,
            "run": self.run,
            "value": self.value,
            "kind": self.kind,
            "residue": self.residue,
            "cost": self.cost,
            "sources": [dict(s) for s in self.sources],
        }


class ArtifactRepository:
    """An append-only, write-once store of domain run artifacts.

    Artifacts are keyed by ``(tenant, domain, run)``. Re-recording the same key
    fails closed (write-once evidence — a run's verified output is immutable).
    """

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str, str], Artifact] = {}

    def record(self, artifact: Artifact) -> Artifact:
        """Append an artifact; fail closed if the (tenant, domain, run) key exists."""
        if artifact.kind not in EXPERT_KINDS:
            raise RegistryError(
                f"unknown expert kind {artifact.kind!r}; "
                f"expected one of {', '.join(EXPERT_KINDS)}"
            )
        key = (artifact.tenant, artifact.domain, artifact.run)
        if key in self._artifacts:
            raise RegistryError(
                f"artifact already recorded for ({artifact.tenant}, "
                f"{artifact.domain}, {artifact.run}); write-once"
            )
        self._artifacts[key] = artifact
        return artifact

    def get(self, domain: str, run: str, *, tenant: str = "") -> Artifact | None:
        return self._artifacts.get((tenant, domain, run))

    def all(self, *, tenant: str | None = None, domain: str | None = None) -> tuple[Artifact, ...]:
        """Every artifact, sorted deterministically by (tenant, domain, run)."""
        out = [
            a
            for (t, d, _), a in self._artifacts.items()
            if (tenant is None or t == tenant) and (domain is None or d == domain)
        ]
        return tuple(sorted(out, key=lambda a: (a.tenant, a.domain, a.run)))

    def count(self, *, tenant: str | None = None, domain: str | None = None) -> int:
        return len(self.all(tenant=tenant, domain=domain))

    def total_cost(self, *, tenant: str | None = None, domain: str | None = None) -> int:
        return sum(a.cost for a in self.all(tenant=tenant, domain=domain))