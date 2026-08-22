"""Determinism rating + approved-rule registry (determinize-then-decide).

This makes the "we never rely on a non-deterministic output directly" rule
operational. A draft/proposition from ambiguous intake (file/email/PDF) may be
rated for determinism: how confident we are that a *deterministic method* can
reproduce it (vs. genuine human judgment).

It provides:

- ``score_determinism`` — a pure, deterministic rating (0..1 confidence) for a
  draft.
- ``Rule`` / ``RuleSet`` — a registry of approved, deterministic extraction
  rules. A human (or analyser) authorizes a rule; when future intake matches it,
  the match is *deterministic* — applied automatically, audited with the rule id
  as a source, and the human is *not* put in the loop for that case.

A match both determinizes the draft (no human needed) and is attributable to a
rule. This is read-only and side-effect-free as a pure capability; the agent
layer decides whether to auto-accept a match.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeterminismRating:
    """How deterministically a draft's extraction could be reproduced.

    Attributes:
        score: 0.0 (fully human judgment) .. 1.0 (fully deterministic).
        reason: A short human-readable explanation of the rating.
    """

    score: float
    reason: str


@dataclass(frozen=True)
class Rule:
    """An approved deterministic extraction rule.

    Attributes:
        id: A stable, deterministic id (assigned by the human/analyser).
        domain: The capability/project this rule governs (e.g. ``vendor``).
        method: A short name of the deterministic method (e.g. ``from_vendor``).
        matcher: A JSON-ready predicate over a draft, e.g. ``{"vendor":
            "GasCo"}`` meaning "matches drafts whose vendor is GasCo".
    """

    id: str
    domain: str
    method: str
    matcher: dict[str, Any] = field(default_factory=dict)

    def matches(self, draft: dict[str, Any]) -> bool:
        """True if ``draft`` satisfies every (key, value) in ``matcher``."""
        return all(draft.get(k) == v for k, v in self.matcher.items())


class RuleSet:
    """A mutable registry of approved deterministic rules."""

    def __init__(self, rules: list[Rule] | None = None) -> None:
        self._rules: dict[str, Rule] = {}
        for r in rules or ():
            self.add(r)

    def add(self, rule: Rule) -> None:
        """Record an approved rule (idempotent by id)."""
        self._rules[rule.id] = rule

    def get(self, rule_id: str) -> Rule | None:
        return self._rules.get(rule_id)

    def all(self) -> tuple[Rule, ...]:
        return tuple(sorted(self._rules.values(), key=lambda r: r.id))

    def match(self, draft: dict[str, Any]) -> Rule | None:
        """Return the first matching deterministic rule, or None."""
        for rule in self.all():
            if rule.matches(draft):
                return rule
        return None


def score_determinism(
    draft: dict[str, Any], *, has_rule: bool = False
) -> DeterminismRating:
    """Score how deterministically a draft's extraction could be reproduced.

    Args:
        draft: The intake draft ``{vendor, amount_cents, due_date, ...}``.
        has_rule: True if an approved deterministic rule already matches.

    The score is a pure function of the draft — it never consults a live model.
    A draft fully backed by an approved rule is highly deterministic; a draft
    with complete fields but no rule is moderately certain; an incomplete one
    is low (reserves room for human judgment).
    """
    vendor = draft.get("vendor")
    amount = draft.get("amount_cents")
    date = draft.get("due_date")
    fields = (
        isinstance(vendor, str) and bool(vendor),
        isinstance(amount, int) and amount >= 0,
        isinstance(date, str) and len(date) == 10,
    )
    present = sum(fields)
    if has_rule:
        return DeterminismRating(0.95, "matches an approved deterministic rule")
    if present == 3:
        return DeterminismRating(0.8, "all fields present; extractable by a stable rule")
    return DeterminismRating(
        min(0.6, 0.15 * present), "fields incomplete or ambiguous; human judgment"
    )


def resolve_with_rules(
    draft: dict[str, Any], rules: RuleSet
) -> tuple[dict[str, Any] | None, Rule | None]:
    """Resolve ``draft`` deterministically if an approved rule matches.

    Returns ``(draft, rule)`` when a rule matches — the caller may auto-accept
    it deterministically, attributing the result to ``rule.id`` — or ``(None,
    None)`` to route the draft to human review. Pure and side-effect-free.
    """
    rule = rules.match(draft)
    if rule is not None:
        return draft, rule
    return None, None