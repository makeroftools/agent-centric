"""Expert selection + cost/residue ledger (Network of Experts AI, made concrete).

This is the deterministic core of the **"Network of Experts AI"** axiom: an AI is
a network of narrow domain experts, and *the network is the intelligence*. Each
component of a component network is a **domain**; this module decides, for a
given domain (its input schema, output shape, and deterministic verifier),
*which kind of expert* should serve it, and it accounts for the **cost** and the
**irreducible residue** that expert produces.

Expert kinds, ranked by preference (deterministic first):

- ``deterministic`` — a hand-tuned method (extractor/validator/rule). Cheapest,
  most auditable, fully reproducible. The north star: use it wherever possible.
- ``learned`` — a small, specialized model (an SLM) adapted to the domain. Only
  warranted when the deterministic method cannot cover the residue *and* the
  domain is "learnable" (its verifier can score a candidate expert). Sourced via
  an opt-in, external provider contract — never trained inside the core.
- ``human`` — the irreducible residue that no deterministic or learned expert
  can cover; the human is in the loop, and their authorization may become a
  durable deterministic rule later.

The engine is **pure and offline**: selection is a function of the domain's
contract + measured residue + cost bounds, and the ledger is an append-only
accounting record. It never runs a model and never mutates durable state on its
own — it *decides* and *accounts*, deterministically and fail-closed. The
External training/payment adapters (an SLM provider, micro-payment settlement)
are opt-in; this module defines the contract they serve.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# The expert kinds, ordered from most-to-least preferred.
EXPERT_DETERMINISTIC = "deterministic"
EXPERT_LEARNED = "learned"
EXPERT_HUMAN = "human"
EXPERT_KINDS = (EXPERT_DETERMINISTIC, EXPERT_LEARNED, EXPERT_HUMAN)


class ExpertsError(ValueError):
    """A domain/expert-selection input violated its contract (fail-closed)."""


# ---------------------------------------------------------------------------
# The domain model
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Domain:
    """A single component's *domain*: its contract and how it is served.

    Attributes:
        id: A stable, unique id within the network (the component id).
        name: A short human-readable domain name.
        input_schema: A typed schema the domain consumes (``chat_pipeline``-style:
            ``{"fields": {name: {"type", "required", "default", "enum"}}}``).
        output_fields: The named fields the verifier checks on the output.
        determinism: The measured determinism score (0..1) of the latest residue;
            ``None`` until the first observed run (assumed high for selection).
        residue: The latest measured irreducible-residue (0..1); how much of the
            domain a deterministic method could not cover on the last run.
    """

    id: str
    name: str
    input_schema: dict[str, Any] = field(default_factory=dict)
    output_fields: tuple[str, ...] = field(default_factory=tuple)
    determinism: float | None = None
    residue: float | None = None

    @property
    def achievable(self) -> bool:
        """Whether a deterministic method could ever serve this domain.

        A domain is deterministically achievable iff it has a verifiable output
        (a non-empty ``output_fields``). Fail-closed: an unverifiable domain is
        not deterministically achievable.
        """
        return bool(self.output_fields)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe, deterministic serialization (for the ledger / page)."""
        return {
            "id": self.id,
            "name": self.name,
            "input_schema": dict(self.input_schema),
            "output_fields": list(self.output_fields),
            "determinism": self.determinism,
            "residue": self.residue,
            "achievable": self.achievable,
        }


def domain_from_dict(data: dict[str, Any]) -> Domain:
    """Reconstruct a ``Domain`` from a ``to_dict()`` payload (fail-closed).

    Raises:
        ExpertsError: If the payload is malformed (no id/name).
    """
    if not isinstance(data, dict):
        raise ExpertsError("domain must be a dict")
    did = data.get("id")
    name = data.get("name")
    if not isinstance(did, str) or not did:
        raise ExpertsError("domain requires a string 'id'")
    if not isinstance(name, str) or not name:
        raise ExpertsError(f"domain {did!r} requires a string 'name'")
    fields = data.get("output_fields") or ()
    return Domain(
        id=did,
        name=name,
        input_schema=dict(data.get("input_schema") or {}),
        output_fields=tuple(f for f in fields if isinstance(f, str)),
        determinism=data.get("determinism"),
        residue=data.get("residue"),
    )


# --- Expert selection -------------------------------------------------------


@dataclass(frozen=True)
class ExpertSelection:
    """The chosen kind of expert + the reasoning (a deterministic decision).

    Attributes:
        kind: One of ``deterministic`` / ``learned`` / ``human``.
        reason: A short human-readable explanation.
        warranted: True when a learned (SLM) expert is warranted for this domain,
            even if the current selection resolves it deterministically.
    """

    kind: str
    reason: str
    warranted: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "reason": self.reason, "warranted": self.warranted}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(high, max(low, value))


def select_expert(
    domain: Domain,
    *,
    determinism: float | None = None,
    residue: float | None = None,
    max_residue: float = 0.2,
    determinism_ceiling: float = 0.9,
    must_learn: bool = False,
) -> ExpertSelection:
    """Select which kind of expert should serve ``domain``.

    Deterministic and fail-closed:

    - An unverifiable domain (no ``output_fields``) is **human** — an output that
      cannot be checked must not be trusted; the human gates it.
    - A domain whose residue is below ``max_residue`` is **deterministic** — the
      method already covers it; no SLM, no human.
    - A **learned** (SLM) expert is warranted only when a real residue remains
      (``residue``/``determinism`` is below the ceiling) *and* the caller requests
      it (``must_learn``) or the residue is substantial. The engine *recommends*;
      the caller decides whether to actually build/select a learned expert (that
      is the opt-in provider boundary). ``must_learn`` forces a learned
      selection; it never bypasses any verifier.
    - Every un-learnt residue is **human** — the irreducible residue that reaches
      a person.
    """
    if not domain.achievable:
        return ExpertSelection(
            EXPERT_HUMAN, "domain has no verifiable output; human review required"
        )
    resid = _clamp(residue) if residue is not None else (domain.residue or 0.0)
    det = _clamp(determinism) if determinism is not None else (
        domain.determinism if domain.determinism is not None else 1.0
    )
    if must_learn:
        # The operator explicitly asked the platform to adapt a per-domain
        # expert; this never bypasses any verifier, it only selects ``learned``.
        return ExpertSelection(
            EXPERT_LEARNED,
            f"operator-requested learned expert for residue {resid:.2f}",
            warranted=True,
        )
    if resid <= max_residue:
        return ExpertSelection(
            EXPERT_DETERMINISTIC,
            f"residue {resid:.2f} <= {max_residue:.2f}; a deterministic method covers it",
        )
    if det < determinism_ceiling:
        return ExpertSelection(
            EXPERT_LEARNED,
            f"residue {resid:.2f} exceeds the deterministic method; a per-domain "
            f"expert (SLM) is warranted",
            warranted=True,
        )
    return ExpertSelection(
        EXPERT_HUMAN,
        f"residue {resid:.2f} cannot be safely auto-resolved; human review",
    )


# --- Cost / residue accounting ----------------------------------------------


@dataclass(frozen=True)
class CostAccount:
    """One recorded run of a domain: what it cost and how much residue remained.

    All cost fields are deterministic, integer-friendly amounts supplied by the
    caller (e.g. tokens, micro-units, estimated fiat in some base unit) — this
    module only *accounts* them, it does not price anything.

    Attributes:
        domain: The domain id this run belonged to.
        kind: The expert kind that served it.
        cost: The additive cost of the run (in the caller's chosen unit).
        residue: The measured irreducible residue (0..1) after the run.
        via: An optional source reference (e.g. the deterministic rule id, or a
            learned-expert/provider id).
    """

    domain: str
    kind: str
    cost: int
    residue: float
    via: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "domain": self.domain,
            "kind": self.kind,
            "cost": self.cost,
            "residue": self.residue,
            "via": self.via,
        }


class CostLedger:
    """An append-only, deterministic accounting record per domain.

    Right now it is a bounded in-memory record. It can be mapped onto a durable,
    single-writer store (like ``TrajectoryStore``) by a caller that grants a
    path; nothing is written here without an explicit grant.
    """

    def __init__(self, max_entries: int = 10_000) -> None:
        if max_entries <= 0:
            raise ExpertsError("max_entries must be positive")
        self._max = max_entries
        self._entries: list[CostAccount] = []

    def record(self, account: CostAccount) -> None:
        """Append a run's account, bounding the record size."""
        if account.kind not in EXPERT_KINDS:
            raise ExpertsError(
                f"unknown expert kind {account.kind!r}; "
                f"expected one of {', '.join(EXPERT_KINDS)}"
            )
        self._entries.append(account)
        if len(self._entries) > self._max:
            del self._entries[: len(self._entries) - self._max]

    def entries(self) -> tuple[CostAccount, ...]:
        return tuple(self._entries)

    def total_cost(self, *, domain: str | None = None) -> int:
        """Sum the cost of every entry (optionally for one domain)."""
        return sum(
            e.cost for e in self._entries if domain is None or e.domain == domain
        )

    def runs(self, *, domain: str | None = None) -> int:
        return sum(1 for e in self._entries if domain is None or e.domain == domain)

    def residue_avg(self, *, domain: str | None = None) -> float:
        """The mean residue over recorded runs for ``domain`` (0.0 when none)."""
        entries = [e for e in self._entries if domain is None or e.domain == domain]
        if not entries:
            return 0.0
        return sum(e.residue for e in entries) / len(entries)