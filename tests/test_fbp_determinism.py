"""Tests for determinism rating + approved-rule registry
(determinize-then-decide: only irreducible residue reaches a human)."""

from __future__ import annotations

from agent_centric.fbp import (
    DeterminismRating,
    Rule,
    RuleSet,
    resolve_with_rules,
    score_determinism,
)


class TestScoreDeterminism:
    def test_incomplete_draft_low_confidence(self) -> None:
        rating = score_determinism({"vendor": "GasCo"})
        assert isinstance(rating, DeterminismRating)
        assert 0.0 <= rating.score < 0.6  # low: incomplete, reserves human judgment

    def test_complete_draft_but_no_rule_medium(self) -> None:
        rating = score_determinism(
            {"vendor": "GasCo", "amount_cents": 100, "due_date": "2026-10-01"}
        )
        assert rating.score >= 0.6

    def test_rule_makes_it_deterministic(self) -> None:
        rating = score_determinism(
            {"vendor": "GasCo", "amount_cents": 100, "due_date": "2026-10-01"},
            has_rule=True,
        )
        assert rating.score >= 0.9


class TestRuleSet:
    def test_match_returns_rule(self) -> None:
        rules = RuleSet(
            [
                Rule(id="r1", domain="vendor", method="from_vendor",
                     matcher={"vendor": "GasCo"}),
            ]
        )
        draft = {"vendor": "GasCo", "amount_cents": 12345, "due_date": "2026-10-01"}
        resolved, rule = resolve_with_rules(draft, rules)
        assert rule is not None and rule.id == "r1"
        assert resolved is not None

    def test_no_match_routes_to_review(self) -> None:
        rules = RuleSet(
            [Rule(id="r1", domain="vendor", method="from_vendor", matcher={"vendor": "x"})]
        )
        resolved, rule = resolve_with_rules({"vendor": "OtherCo"}, rules)
        assert resolved is None and rule is None  # -> human review

    def test_rule_matches_requires_all_fields(self) -> None:
        rules = RuleSet(
            [Rule(id="r2", domain="vendor", method="from_vendor",
                  matcher={"vendor": "GasCo", "amount_cents": 12345})]
        )
        # A GasCo draft with the wrong amount does not match -> not deterministic.
        resolved, rule = resolve_with_rules({"vendor": "GasCo", "amount_cents": 1}, rules)
        assert rule is None

    def test_rule_set_is_idempotent_and_sorted(self) -> None:
        rules = RuleSet()
        rules.add(Rule(id="b", domain="v", method="m", matcher={"vendor": "Y"}))
        rules.add(Rule(id="a", domain="v", method="m", matcher={"vendor": "X"}))
        rules.add(Rule(id="b", domain="v", method="m", matcher={"vendor": "Y"}))
        assert [r.id for r in rules.all()] == ["a", "b"]