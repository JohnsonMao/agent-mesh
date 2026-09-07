"""Command-line seam tests for the manual Codex connection probe."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parents[2]
FAKE_SERVER = ROOT / "tests" / "fixtures" / "fake_codex_app_server.py"
PROBE = ROOT / "codex_probe.py"


def run_probe(*arguments: str, server_mode: str = "normal") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(PROBE),
            "Say hello",
            *arguments,
            "--server-command",
            sys.executable,
            str(FAKE_SERVER),
            server_mode,
        ],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_probe_logs_in_streams_text_and_reports_new_thread() -> None:
    result = run_probe()

    assert result.returncode == 0, result.stderr
    assert "https://auth.openai.com/codex/device" in result.stdout
    assert "ABCD-1234" in result.stdout
    assert "hello" in result.stdout
    assert "Codex thread: thread-1" in result.stdout


def test_probe_resumes_requested_thread() -> None:
    result = run_probe("--thread-id", "persisted-thread")

    assert result.returncode == 0, result.stderr
    assert "Codex thread: persisted-thread" in result.stdout


def test_probe_exits_nonzero_for_failed_turn() -> None:
    result = run_probe(server_mode="turn-failed")

    assert result.returncode == 1
    assert "Codex turn ended with status failed" in result.stderr
