"""Tests for orchestration (``fbp/orchestrate.py``): chat artifact → FBP plan.

The seam that ties the chat window into FBP: a pinned, canonical artifact is
mapped to an ordered ``run`` plan executed through the driver's spine (parent
re-verifies, ledger, replay). These tests are deterministic and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.orchestrate import (
    SCHEMAS,
    extract_intent,
    plan_from_artifact,
    plan_from_schema,
    run_artifact_plan,
)


class TestSchemaDriven:
    """Schema-driven orchestration: a typed artifact is validated/coerced
    against its intent's schema (fail-closed) before it maps to a plan."""

    def test_typed_double_plans(self) -> None:
        steps = plan_from_schema({"intent": "double", "value": 21})
        assert steps == [{"task": "double", "args": {"value": 21}}]

    def test_typed_sum_plans(self) -> None:
        steps = plan_from_schema({"intent": "sum", "a": 2, "b": 3})
        assert steps == [{"task": "sum", "args": {"a": 2, "b": 3}}]

    def test_intent_defaults_to_run(self) -> None:
        steps = plan_from_schema({"task": "double", "args": {"value": 21}})
        assert steps == [{"task": "double", "args": {"value": 21}}]

    def test_coerces_numeric_strings(self) -> None:
        steps = plan_from_schema({"intent": "sum", "a": "2", "b": "3"})
        assert steps == [{"task": "sum", "args": {"a": 2, "b": 3}}]

    def test_missing_required_field_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "sum", "a": 2})

    def test_bad_type_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "double", "value": "not-a-number"})

    def test_unknown_intent_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "delete_everything", "value": 1})

    def test_schemas_expose_declared_fields(self) -> None:
        assert set(SCHEMAS) >= {"run", "double", "sum"}
        assert SCHEMAS["sum"]["fields"]["a"]["required"] is True

    def test_run_artifact_plan_runs_typed_intent(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register(
            "sum", lambda a, b: a + b, source_url="file:///tasks/sum"
        )
        driver.configure(tasks=("sum",))
        try:
            result = run_artifact_plan(driver, {"intent": "sum", "a": 2, "b": 3})
            assert result["ok"] is True
            assert result["results"][0]["value"] == 5
        finally:
            driver.close()

    def test_run_artifact_plan_typed_fail_closed(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        try:
            result = run_artifact_plan(driver, {"intent": "sum", "a": 2})
            assert result["ok"] is False
            assert "required" in result["error"]
        finally:
            driver.close()


class TestExtractIntent:
    def test_defaults_to_run(self) -> None:
        assert extract_intent({}) == "run"
        assert extract_intent({"task": "double"}) == "run"

    def test_reads_intent(self) -> None:
        assert extract_intent({"intent": "inspect"}) == "inspect"

    def test_rejects_unsupported(self) -> None:
        with pytest.raises(ValueError):
            extract_intent({"intent": "delete_everything"})


class TestPlanFromArtifact:
    def test_single_task(self) -> None:
        steps = plan_from_artifact({"task": "double", "args": {"value": 21}})
        assert steps == [{"task": "double", "args": {"value": 21}}]

    def test_multi_step(self) -> None:
        steps = plan_from_artifact(
            {"steps": [{"task": "a"}, {"task": "b", "args": {"x": 2}}]}
        )
        assert [s["task"] for s in steps] == ["a", "b"]
        assert steps[1]["args"] == {"x": 2}

    def test_default_verifier_applied(self) -> None:
        steps = plan_from_artifact(
            {"task": "double"}, verifier="even"
        )
        assert steps[0]["verifier"] == "even"

    def test_unorchestrable_fails(self) -> None:
        with pytest.raises(ValueError):
            plan_from_artifact({"intent": "status"})  # no task, no steps

    def test_step_missing_task_fails(self) -> None:
        with pytest.raises(ValueError):
            plan_from_artifact({"steps": [{"args": {}}]})


class TestRunArtifactPlan:
    def test_runs_through_driver(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register(
            "double", lambda value: value * 2, source_url="file:///tasks/double"
        )
        driver.register("even", lambda value: isinstance(value, int) and value % 2 == 0)
        driver.configure(tasks=("double",), verifiers=("even",))
        try:
            result = run_artifact_plan(
                driver, {"task": "double", "args": {"value": 21}}, verifier="even"
            )
            assert result["ok"] is True
            assert result["results"][0]["verified"] is True
            assert result["results"][0]["value"] == 42
        finally:
            driver.close()

    def test_fail_closed_when_step_unverified(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        driver.register(
            "double", lambda value: value * 2, source_url="file:///tasks/double"
        )
        driver.register("odd", lambda value: isinstance(value, int) and value % 2 == 1)
        driver.configure(tasks=("double",), verifiers=("odd",))
        try:
            # double(21)=42 is even, odd verifier demotes -> fail closed.
            result = run_artifact_plan(
                driver, {"task": "double", "args": {"value": 21}}, verifier="odd"
            )
            assert result["ok"] is False
            assert result["completed"] == 0
        finally:
            driver.close()

    def test_artifact_not_plannable_returns_error(self) -> None:
        from agent_centric.fbp.driver import FbpDriver

        driver = FbpDriver()
        try:
            result = run_artifact_plan(driver, {"intent": "status"})
            assert result["ok"] is False
            assert "error" in result
        finally:
            driver.close()

class TestBillsIntents:
    """Schema-driven bills intents route to the ``bills`` child (the mission loop)."""

    def test_bills_intake_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_intake", "draft": {"id": "b1", "vendor": "GasCo"}}
        )
        assert steps == [
            {"task": "bills_intake", "args": {"draft": {"id": "b1", "vendor": "GasCo"}},
             "child": "bills"}
        ]

    def test_bills_accept_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_accept", "draft": {"id": "b1"}}
        )
        assert steps[0]["task"] == "bills_accept"
        assert steps[0]["child"] == "bills"

    def test_bills_calendar_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_calendar", "from_date": "2026-01-01", "to_date": "2026-12-31"}
        )
        assert steps == [
            {"task": "bills_calendar",
             "args": {"from_date": "2026-01-01", "to_date": "2026-12-31"},
             "child": "bills"}
        ]

    def test_bills_intake_missing_draft_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "bills_intake"})

    def test_bills_calendar_missing_dates_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "bills_calendar", "from_date": "2026-01-01"})

    def test_bills_intents_in_schemas(self) -> None:
        assert "bills_intake" in SCHEMAS
        assert SCHEMAS["bills_accept"]["child"] == "bills"


class TestBillsIntentsExtended:
    """The full bills typed-intent surface: read-only registry, maintenance,
    deterministic accept, and rule persistence all route to the ``bills`` child."""

    def test_bills_registry_plans_to_child(self) -> None:
        steps = plan_from_schema({"intent": "bills_registry"})
        assert steps == [
            {"task": "bills_registry", "args": {}, "child": "bills"}
        ]

    def test_bills_mark_paid_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_mark_paid", "id": "b1", "note": "paid by wire"}
        )
        assert steps == [
            {"task": "bills_mark_paid",
             "args": {"id": "b1", "note": "paid by wire"}, "child": "bills"}
        ]

    def test_bills_mark_status_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_mark_status", "id": "b1", "status": "void"}
        )
        assert steps == [
            {"task": "bills_mark_status",
             "args": {"id": "b1", "status": "void"}, "child": "bills"}
        ]

    def test_bills_accept_deterministic_plans_to_child(self) -> None:
        steps = plan_from_schema(
            {"intent": "bills_accept_deterministic", "draft": {"id": "b1"}}
        )
        assert steps == [
            {"task": "bills_accept_deterministic",
             "args": {"draft": {"id": "b1"}}, "child": "bills"}
        ]

    def test_bills_rule_add_plans_to_child(self) -> None:
        rule = {"id": "r-gasco", "domain": "vendor", "method": "eq",
                "matcher": {"vendor": "GasCo"}}
        steps = plan_from_schema({"intent": "bills_rule_add", "rule": rule})
        assert steps == [
            {"task": "bills_rule_add", "args": {"rule": rule}, "child": "bills"}
        ]

    def test_bills_mark_status_missing_status_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "bills_mark_status", "id": "b1"})

    def test_bills_mark_paid_missing_id_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "bills_mark_paid"})

    def test_bills_rule_add_missing_rule_fails_closed(self) -> None:
        with pytest.raises(ValueError):
            plan_from_schema({"intent": "bills_rule_add"})

    def test_all_bills_intents_in_schemas(self) -> None:
        for intent in (
            "bills_registry", "bills_mark_paid", "bills_mark_status",
            "bills_accept_deterministic", "bills_rule_add",
        ):
            assert intent in SCHEMAS
            assert SCHEMAS[intent]["child"] == "bills"
            assert SCHEMAS[intent]["task"] == intent


class TestPlanStepLimit:
    """Orchestration plans are bounded (fail-closed) like component networks."""

    def test_small_plan_accepted(self) -> None:
        plan = plan_from_artifact({"steps": [{"task": "double", "args": {"value": 21}}]})
        assert len(plan) == 1

    def test_oversized_plan_fails_closed(self) -> None:
        from agent_centric.fbp.orchestrate import _DEFAULT_STEP_LIMIT

        steps = [{"task": "double", "args": {"value": 1}}] * (_DEFAULT_STEP_LIMIT + 1)
        with pytest.raises(ValueError, match="fail-closed"):
            plan_from_artifact({"steps": steps})

    def test_run_artifact_plan_oversized_fails_closed(self) -> None:
        from agent_centric.fbp.driver import FbpDriver
        from agent_centric.fbp.orchestrate import _DEFAULT_STEP_LIMIT

        driver = FbpDriver()
        driver.register("double", lambda value: value * 2, source_url="file:///tasks/double")
        driver.configure(tasks=("double",))
        try:
            steps = [{"task": "double", "args": {"value": 1}}] * (_DEFAULT_STEP_LIMIT + 1)
            result = run_artifact_plan(driver, {"steps": steps})
            assert result["ok"] is False
            assert result["completed"] == 0
        finally:
            driver.close()
