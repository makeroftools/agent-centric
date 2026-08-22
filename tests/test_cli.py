"""Smoke tests for the minimal operator CLI (Volley 017).

These prove the CLI's three commands work end-to-end against a temporary
file-backed store, that they fail closed with a non-zero exit code on missing
trajectories, and that the console entry point is wired up. They are lightweight
and deterministic: they exercise the public ``main`` function directly rather
than spawning a subprocess.
"""

from __future__ import annotations

from pathlib import Path

from agent_centric.cli import main


def _run(store: Path, *args: str) -> tuple[int, str]:
    import contextlib
    import io

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main(["--store", str(store), *args])
    return code, out.getvalue() + err.getvalue()


class TestCliRun:
    def test_run_demo_succeeds(self, tmp_path: Path) -> None:
        code, out = _run(tmp_path, "run")
        assert code == 0
        assert "demo-counter: VERIFIED" in out
        assert "demo-reverse: VERIFIED" in out
        assert "demo-tool: VERIFIED" in out
        assert "demo-model: VERIFIED" in out
        assert "demo-pipeline: VERIFIED" in out
        assert "demo-bills: VERIFIED" in out
        assert "demo-workspace: VERIFIED" in out
        assert "demo-email: VERIFIED" in out
        assert "demo-bills-calendar: VERIFIED" in out
        assert "demo-intake: VERIFIED" in out
        assert "demo-intake-email-draft: VERIFIED" in out

    def test_run_persists_trajectories(self, tmp_path: Path) -> None:
        _run(tmp_path, "run")
        # Each demo task produced a durable trajectory file (the workspace dir
        # is also created under the store dir, so count only .jsonl files).
        files = [p for p in tmp_path.iterdir() if p.suffix == ".jsonl"]
        assert len(files) == 12


class TestCliSummarise:
    def test_summarise_verified_trajectory(self, tmp_path: Path) -> None:
        _run(tmp_path, "run")
        code, out = _run(tmp_path, "summarise", "demo-pipeline#4")
        assert code == 0
        assert "task_id:       demo-pipeline" in out
        assert "state:         verified" in out
        assert "stage_kind:    sequential" in out

    def test_summarise_missing_fails_closed(self, tmp_path: Path) -> None:
        code, out = _run(tmp_path, "summarise", "does-not-exist")
        assert code == 1
        assert "no trajectory" in out


class TestCliReplayVerify:
    def test_replay_verify_passes(self, tmp_path: Path) -> None:
        _run(tmp_path, "run")
        code, out = _run(tmp_path, "replay-verify", "demo-pipeline#4")
        assert code == 0
        assert "replay-verify: PASSED" in out

    def test_replay_verify_missing_fails_closed(self, tmp_path: Path) -> None:
        code, out = _run(tmp_path, "replay-verify", "does-not-exist")
        assert code == 1
        assert "no trajectory" in out


class TestCliEntryPoint:
    def test_module_runs(self) -> None:
        """``python -m agent_centric`` is wired to the CLI main."""
        import agent_centric.__main__ as m  # noqa: F401

        assert callable(m.main)


class TestCliFbp:
    """The FBP subcommand and durable-ledger replay (crash-safe recovery)."""

    def test_fbp_records_and_replays_durable_ledger(self, tmp_path: Path) -> None:
        ledger = tmp_path / "session.ledger.db"
        code, _ = _run(tmp_path, "fbp", "--ledger", str(ledger))
        assert code == 0
        assert ledger.exists()

        # Replay the durable ledger (a fresh ``main`` invocation re-seeds the
        # module-level callable registry, simulating a fresh process).
        code, out = _run(tmp_path, "fbp-replay", str(ledger))
        assert code == 0, out
        assert "passed=18" in out
        assert "failed=0" in out

    def test_fbp_replay_missing_ledger_fails_closed(self, tmp_path: Path) -> None:
        code, out = _run(tmp_path, "fbp-replay", str(tmp_path / "nope.db"))
        assert code == 1

    def test_fbp_replay_over_transports(self, tmp_path: Path) -> None:
        """Durable ledger replay works over ipc and tcp (the endpoint is
        resolved per transport, not a bare inproc-style name)."""
        for transport in ("ipc", "tcp"):
            ledger = tmp_path / f"session-{transport}.ledger.db"
            code, _ = _run(tmp_path, "fbp", "--transport", transport, "--ledger", str(ledger))
            assert code == 0
            code, out = _run(tmp_path, "fbp-replay", str(ledger), "--transport", transport)
            assert code == 0, out
            assert "failed=0" in out

    def test_fbp_summary_reports_ledger(self, tmp_path: Path) -> None:
        """fbp-summary gives an operator-facing readout of a durable ledger. The
        demo contains intentional fail-closed cases (demote / unknown target /
        ungranted store key), so it reports errors and exits non-zero."""
        ledger = tmp_path / "session.ledger.db"
        code, _ = _run(tmp_path, "fbp", "--ledger", str(ledger))
        assert code == 0

        code, out = _run(tmp_path, "fbp-summary", str(ledger))
        assert "run_count=18" in out
        assert "errors=3" in out
        assert "ok=False" in out
        assert code == 1  # the demo has intentional failures

    def test_fbp_summary_missing_fails_closed(self, tmp_path: Path) -> None:
        code, out = _run(tmp_path, "fbp-summary", str(tmp_path / "nope.db"))
        assert code == 1
        assert "no ledger file" in out