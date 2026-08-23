"""Tests for orchestration (``fbp/orchestrate.py``): chat artifact → FBP plan.

The seam that ties the chat window into FBP: a pinned, canonical artifact is
mapped to an ordered ``run`` plan executed through the driver's spine (parent
re-verifies, ledger, replay). These tests are deterministic and offline.
"""

from __future__ import annotations

import pytest

from agent_centric.fbp.orchestrate import (
    extract_intent,
    plan_from_artifact,
    run_artifact_plan,
)


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