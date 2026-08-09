"""Smoke tests for the minimal operator CLI (Volley 017).

These prove the CLI's three commands work end-to-end against a temporary
file-backed store, that they fail closed with a non-zero exit code on missing
trajectories, and that the console entry point is wired up. They are lightweight
and deterministic: they exercise the public ``main`` function directly rather
than spawning a subprocess.
"""

from __future__ import annotations

from pathlib import Path

from meta_harness.cli import main


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
        assert "demo-bills-mark-paid: VERIFIED" in out

    def test_run_persists_trajectories(self, tmp_path: Path) -> None:
        _run(tmp_path, "run")
        # Each demo task produced a durable trajectory file (the workspace dir
        # is also created under the store dir, so count only .jsonl files).
        files = [p for p in tmp_path.iterdir() if p.suffix == ".jsonl"]
        assert len(files) == 11


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
        """``python -m meta_harness`` is wired to the CLI main."""
        import meta_harness.__main__ as m  # noqa: F401

        assert callable(m.main)