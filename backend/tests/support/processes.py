"""Helpers to run the real API and CLI as separate processes in tests."""

import os
import re
import secrets
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import httpx2 as httpx
import pytest

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
STARTUP_TIMEOUT = 30


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _environment(database_url, **extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GAME_HOSTING_")}
    env["GAME_HOSTING_DATABASE_URL"] = database_url
    env.update(extra)
    return env


@contextmanager
def api_process(log_path, database_url, token=None):
    """Start the API like production does and wait until /health answers.

    Yields a dict with the base URL, the token, auth headers and the log path.
    The process is terminated on exit.
    """
    token = token or secrets.token_urlsafe(32)
    port = free_port()
    env = _environment(database_url, GAME_HOSTING_API_TOKEN=token)
    with open(log_path, "wb") as log_file:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "--factory",
                "app.api.entrypoint:build_app", "--host", "127.0.0.1", "--port", str(port),
            ],
            cwd=BACKEND_DIR,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        base_url = f"http://127.0.0.1:{port}"
        try:
            deadline = time.monotonic() + STARTUP_TIMEOUT
            while True:
                if process.poll() is not None:
                    pytest.fail(f"API exited early:\n{Path(log_path).read_text(errors='replace')}")
                try:
                    if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"API did not start:\n{Path(log_path).read_text(errors='replace')}")
                time.sleep(0.2)
            yield {
                "base_url": base_url,
                "token": token,
                "headers": {"Authorization": f"Bearer {token}"},
                "log_path": log_path,
            }
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()


def run_cli(database_url, *args):
    """Run `cli.py` as a subprocess with the database URL in its environment."""
    return subprocess.run(
        [sys.executable, "cli.py", *args],
        cwd=BACKEND_DIR,
        env=_environment(database_url),
        capture_output=True,
        text=True,
        timeout=120,
    )


def output_field(output, field):
    """The value of a `field: value` line in the CLI's output."""
    match = re.search(rf"^{re.escape(field)}: (.+)$", output, re.M)
    assert match, f"no '{field}:' line in:\n{output}"
    return match.group(1).strip()
