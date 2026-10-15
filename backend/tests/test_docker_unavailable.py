"""Docker-unavailable behaviour with the real SDK, no Docker needed.

DOCKER_HOST points at a closed local port, so the real SDK fails to connect.
This complements the mock-based unit tests with an actual connection failure.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

import cli
from app.errors import RuntimeUnavailableError
from app.infrastructure.docker_client import DockerClient

UNREACHABLE_DOCKER_HOST = "tcp://127.0.0.1:1"
BACKEND_DIR = Path(__file__).resolve().parent.parent


@pytest.fixture
def unreachable_docker(monkeypatch):
    monkeypatch.setenv("DOCKER_HOST", UNREACHABLE_DOCKER_HOST)


def test_real_sdk_connection_failure_is_runtime_unavailable(unreachable_docker):
    with pytest.raises(RuntimeUnavailableError):
        DockerClient().get_status("minecraft-dev")


def test_cli_in_process_reports_docker_unavailable(unreachable_docker, capsys):
    assert cli.main(["status"]) == cli.EXIT_RUNTIME_UNAVAILABLE
    captured = capsys.readouterr()
    assert captured.err.startswith("error: Cannot reach Docker")
    assert captured.out == ""


def test_cli_process_exits_with_4_and_no_traceback():
    env = {**os.environ, "DOCKER_HOST": UNREACHABLE_DOCKER_HOST}
    result = subprocess.run(
        [sys.executable, "cli.py", "start"],
        cwd=BACKEND_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 4
    assert "Traceback" not in result.stderr
    assert result.stderr.startswith("error: Cannot reach Docker")
