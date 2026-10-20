"""End-to-end: a real uvicorn process, real HTTP, real Docker.

The API runs as a subprocess started the way production starts it (the
entry-point factory, configured only through environment variables) and is
driven over HTTP against throwaway containers. `minecraft-dev` is never used.
"""

import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx2 as httpx
import pytest

pytestmark = pytest.mark.integration

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
STARTUP_TIMEOUT = 30


def _free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def api(tmp_path_factory):
    token = secrets.token_urlsafe(32)
    port = _free_port()
    log_path = tmp_path_factory.mktemp("api") / "api.log"
    env = {**os.environ, "GAME_HOSTING_API_TOKEN": token}
    env.pop("GAME_HOSTING_API_DOCS", None)
    with open(log_path, "wb") as log_file:
        process = subprocess.Popen(
            [
                sys.executable, "-m", "uvicorn", "--factory",
                "app.api.entrypoint:build_app", "--host", "127.0.0.1",
                "--port", str(port),
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
                    pytest.fail(f"API exited early:\n{log_path.read_text()}")
                try:
                    if httpx.get(f"{base_url}/health", timeout=2).status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                if time.monotonic() > deadline:
                    pytest.fail(f"API did not start:\n{log_path.read_text()}")
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


def _call(api, method, path, authorized=True, **kwargs):
    headers = api["headers"] if authorized else {}
    return httpx.request(
        method, api["base_url"] + path, headers=headers, timeout=60, **kwargs
    )


def test_full_lifecycle_over_http(api, sdk, container_name):
    path = f"/servers/{container_name}"
    assert _call(api, "get", path).json() == {
        "server": container_name,
        "status": "created",
    }
    assert _call(api, "post", f"{path}/start").json()["status"] == "running"

    logs = _call(api, "get", f"{path}/logs?tail=5")
    assert logs.status_code == 200
    assert logs.headers["content-type"].startswith("text/plain")
    assert "hello-from-it" in logs.text

    assert _call(api, "post", f"{path}/restart?timeout=1").json()["status"] == "running"
    assert _call(api, "post", f"{path}/stop?timeout=1").json()["status"] == "exited"
    assert sdk.containers.get(container_name).status == "exited"


def test_requests_without_a_valid_token_change_nothing(api, sdk, container_name):
    path = f"/servers/{container_name}"
    assert _call(api, "post", f"{path}/start", authorized=False).status_code == 401
    bad = {"Authorization": "Bearer not-the-right-token-0123456789"}
    response = httpx.post(api["base_url"] + f"{path}/start", headers=bad, timeout=60)
    assert response.status_code == 401
    assert sdk.containers.get(container_name).status == "created"


def test_unmanaged_container_is_invisible_over_http(api, sdk, unmanaged_container_name):
    path = f"/servers/{unmanaged_container_name}"
    assert _call(api, "get", path).json()["status"] == "not_found"
    assert _call(api, "post", f"{path}/start").status_code == 404
    assert _call(api, "post", f"{path}/stop").status_code == 404
    assert sdk.containers.get(unmanaged_container_name).status == "created"


def test_unknown_server_and_invalid_input(api):
    assert _call(api, "post", "/servers/game-hosting-it-missing/start").status_code == 404
    assert _call(api, "get", "/servers/-bad").status_code == 422
    assert _call(api, "post", "/servers/x/stop?timeout=0").status_code == 422


def test_docs_are_not_exposed_and_health_is_open(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert _call(api, "get", path, authorized=False).status_code == 404
    assert _call(api, "get", "/health", authorized=False).json() == {"status": "ok"}


def test_server_log_records_rejections_but_never_the_token(api):
    _call(api, "post", "/servers/x/start", authorized=False)
    log = Path(api["log_path"]).read_text(errors="replace")
    assert "Rejected request with a missing or invalid token" in log
    assert "API configured; interactive docs disabled" in log
    assert api["token"] not in log
