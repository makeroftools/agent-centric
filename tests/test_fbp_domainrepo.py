"""Tests for the Domain-of-Experts registry + artifact repository.

This is the observability + provenance layer (catalog -> decision -> accounting
-> evidence) of "Network of Experts AI". The registry is a passive catalog
(never an authority); the artifact repository is append-only, write-once
evidence. Both are tenant-aware and deterministic.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.domainrepo import (
    Artifact,
    ArtifactRepository,
    DomainRegistry,
    RegistryError,
)
from agent_centric.fbp.experts import (
    EXPERT_DETERMINISTIC,
    EXPERT_LEARNED,
    Domain,
)


def _domain(*, did="bill-extract", residue=0.1, determinism=0.8) -> Domain:
    return Domain(
        id=did,
        name="bill extraction",
        input_schema={"fields": {"vendor": {"type": "str", "required": True}}},
        output_fields=("vendor", "amount_cents", "due_date"),
        determinism=determinism,
        residue=residue,
    )


class TestDomainRegistry:
    def test_observe_and_get(self) -> None:
        reg = DomainRegistry()
        rec = reg.observe(_domain(), tenant="acme")
        assert reg.get("bill-extract", tenant="acme") is rec
        assert rec.selection.kind == EXPERT_DETERMINISTIC

    def test_observe_is_idempotent_by_tenant_and_id(self) -> None:
        reg = DomainRegistry()
        reg.observe(_domain(), tenant="acme")
        reg.observe(_domain(residue=0.9), tenant="acme")  # refresh, not duplicate
        assert reg.count(tenant="acme") == 1

    def test_tenants_are_isolated(self) -> None:
        reg = DomainRegistry()
        reg.observe(_domain(), tenant="acme")
        reg.observe(_domain(), tenant="globex")
        assert reg.count(tenant="acme") == 1
        assert reg.count(tenant="globex") == 1
        assert reg.count() == 2
        assert reg.get("bill-extract", tenant="acme") is not None
        assert reg.get("bill-extract", tenant="globex") is not None

    def test_by_expert_kind(self) -> None:
        reg = DomainRegistry()
        reg.observe(_domain(residue=0.1), tenant="acme")  # deterministic
        reg.observe(_domain(did="ambig", residue=0.9, determinism=0.5), tenant="acme")  # learned
        assert len(reg.by_expert_kind(EXPERT_DETERMINISTIC, tenant="acme")) == 1
        assert len(reg.by_expert_kind(EXPERT_LEARNED, tenant="acme")) == 1

    def test_by_expert_kind_rejects_unknown(self) -> None:
        reg = DomainRegistry()
        with pytest.raises(RegistryError):
            reg.by_expert_kind("magic")

    def test_observe_rejects_empty_id(self) -> None:
        reg = DomainRegistry()
        with pytest.raises(RegistryError):
            reg.observe(_domain(did=""), tenant="acme")

    def test_record_to_dict(self) -> None:
        reg = DomainRegistry()
        rec = reg.observe(_domain(), tenant="acme", provenance="net:demo")
        d = rec.to_dict()
        assert d["tenant"] == "acme"
        assert d["id"] == "bill-extract"
        assert d["expert_kind"] == EXPERT_DETERMINISTIC
        assert d["provenance"] == "net:demo"


class TestArtifactRepository:
    def test_record_and_get(self) -> None:
        repo = ArtifactRepository()
        a = Artifact(
            tenant="acme", domain="bill-extract", run="r1",
            value={"vendor": "GasCo"}, kind=EXPERT_DETERMINISTIC,
            residue=0.1, cost=10, sources=({"id": "rule-1"},),
        )
        repo.record(a)
        assert repo.get("bill-extract", "r1", tenant="acme") is a

    def test_write_once(self) -> None:
        repo = ArtifactRepository()
        repo.record(Artifact(tenant="acme", domain="d", run="r1", value=1,
                             kind=EXPERT_DETERMINISTIC, residue=0.0, cost=1))
        with pytest.raises(RegistryError):
            repo.record(Artifact(tenant="acme", domain="d", run="r1", value=2,
                                 kind=EXPERT_DETERMINISTIC, residue=0.0, cost=1))

    def test_rejects_unknown_kind(self) -> None:
        repo = ArtifactRepository()
        with pytest.raises(RegistryError):
            repo.record(Artifact(tenant="acme", domain="d", run="r1", value=1,
                                 kind="magic", residue=0.0, cost=1))

    def test_tenant_and_domain_filter(self) -> None:
        repo = ArtifactRepository()
        repo.record(Artifact(tenant="acme", domain="d1", run="r1", value=1,
                             kind=EXPERT_DETERMINISTIC, residue=0.0, cost=5))
        repo.record(Artifact(tenant="acme", domain="d2", run="r1", value=1,
                             kind=EXPERT_DETERMINISTIC, residue=0.0, cost=7))
        repo.record(Artifact(tenant="globex", domain="d1", run="r1", value=1,
                             kind=EXPERT_DETERMINISTIC, residue=0.0, cost=9))
        assert repo.count(tenant="acme") == 2
        assert repo.count(tenant="acme", domain="d1") == 1
        assert repo.total_cost(tenant="acme") == 12
        assert repo.total_cost(tenant="globex") == 9


class TestIntegration:
    def test_registry_and_repository_share_domain(self) -> None:
        """A domain observed in the registry can have artifacts recorded for it."""
        reg = DomainRegistry()
        repo = ArtifactRepository()
        domain = _domain()
        rec = reg.observe(domain, tenant="acme")
        repo.record(Artifact(
            tenant="acme", domain=domain.id, run="r1",
            value={"vendor": "GasCo"}, kind=rec.selection.kind,
            residue=domain.residue or 0.0, cost=10,
        ))
        assert repo.get(domain.id, "r1", tenant="acme").kind == EXPERT_DETERMINISTIC