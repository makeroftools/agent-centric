"""Web-tier tests for the operator activity feed wiring (Web-in-progress).

These cover the ``/activity`` route, the dashboard Activity card, and that real
operator actions (demo / bills intake / accept) record activity entries with
their verification status — while staying additive and read-only with respect
to durable state.
"""

from __future__ import annotations

import json

from agent_centric.fbp.web import FbpLandingServer, _render_landing


class TestActivityRoute:
    def test_activity_readout_is_deterministic_and_empty(self) -> None:
        server = FbpLandingServer()
        try:
            readout = server._activity_readout()
            assert readout["count"] == 0
            assert readout["entries"] == []
            assert readout["durable"] is False
            # Deterministic: repeated reads are identical.
            assert readout == server._activity_readout()
        finally:
            server._driver.close()

    def test_demo_action_records_activity(self) -> None:
        server = FbpLandingServer()
        try:
            server._run_demo()
            readout = server._activity_readout()
            assert readout["count"] == 1
            entry = readout["entries"][0]
            assert entry["kind"] == "action"
            assert entry["action"] == "run demo double(21)"
            assert entry["verified"] is True
        finally:
            server._driver.close()

    def test_bills_accept_records_activity(self) -> None:
        server = FbpLandingServer()
        try:
            draft = {
                "id": "bill-activity-1",
                "vendor": "GasCo",
                "amount_cents": 1000,
                "due_date": "2026-11-01",
            }
            server._bills_accept(json.dumps({"draft": draft}))
            readout = server._activity_readout()
            entry = readout["entries"][-1]
            assert entry["kind"] == "bills"
            assert entry["action"] == "Accept bill"
            assert entry["verified"] is True
        finally:
            server._driver.close()

    def test_network_run_records_activity(self) -> None:
        server = FbpLandingServer()
        try:
            server._run_network(
                '{"components": [{"id": "a", "task": "double", '
                '"args": {"value": 21}}], "edges": []}'
            )
            readout = server._activity_readout()
            # One entry from the network run.
            kinds = {e["kind"] for e in readout["entries"]}
            assert "network" in kinds
        finally:
            server._driver.close()

    def test_activity_feed_is_bounded_on_page(self) -> None:
        """A large number of actions never overgrows the in-memory feed."""
        server = FbpLandingServer()
        try:
            for _ in range(600):
                server._run_demo()
            readout = server._activity_readout()
            assert readout["count"] <= 500  # MAX_ACTIVITY_ENTRIES cap
        finally:
            server._driver.close()

    def test_render_includes_activity_card(self) -> None:
        html = _render_landing({})
        assert "Activity feed" in html
        assert "id='activity-list'" in html


class TestActivityDurable:
    def test_activity_durable_by_grant(self, tmp_path) -> None:
        """With a granted activity path, the feed persists across a restart."""
        path = str(tmp_path / "activity.json")
        s1 = FbpLandingServer(activity_path=path)
        try:
            s1._run_demo()
            assert s1._activity_readout()["durable"] is True
            assert s1._activity_readout()["count"] == 1
        finally:
            s1._driver.close()
        s2 = FbpLandingServer(activity_path=path)
        try:
            readout = s2._activity_readout()
            assert readout["count"] == 1
            assert readout["entries"][0]["action"] == "run demo double(21)"
        finally:
            s2._driver.close()