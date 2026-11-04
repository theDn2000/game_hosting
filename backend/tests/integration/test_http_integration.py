"""End to end: real API process, real PostgreSQL, real Docker, real CLI.

The API runs as a subprocess started the way production starts it (entry-point
factory, configuration only through environment variables) and is driven over
HTTP. Servers are registered by running the administration CLI, exactly as the
operator does. Containers are throwaway; `minecraft-dev` is never used.
"""

import secrets
import uuid

import httpx2 as httpx
import pytest

from app.infrastructure.db.database import create_database_engine
from tests.support.migrations import upgrade
from tests.support.processes import api_process, output_field, run_cli

pytestmark = pytest.mark.integration

PLAN_SLUG = "it-plan"
OWNER = "operator@example.com"


@pytest.fixture(scope="module")
def database_url(database_factory):
    """An empty migrated database with one plan, shared by this module."""
    url = database_factory.create()
    engine = create_database_engine(url)
    try:
        upgrade(engine)
    finally:
        engine.dispose()
    added = run_cli(
        url, "plans", "add", "--slug", PLAN_SLUG, "--name", "Integration plan",
        "--memory-mb", "512", "--cpu-millicores", "500",
    )
    assert added.returncode == 0, added.stderr
    return url


@pytest.fixture(scope="module")
def api(database_url, tmp_path_factory):
    with api_process(tmp_path_factory.mktemp("api") / "api.log", database_url) as running:
        yield running


def call(api, method, path, authorized=True, **kwargs):
    headers = api["headers"] if authorized else {}
    return httpx.request(method, api["base_url"] + path, headers=headers, timeout=60, **kwargs)


def adopt(database_url, container, owner=OWNER, name="Integration world"):
    args = ["servers", "adopt", container, "--owner", owner, "--plan", PLAN_SLUG, "--name", name]
    result = run_cli(database_url, *args)
    assert result.returncode == 0, result.stderr
    return output_field(result.stdout, "id")


def test_a_registered_server_goes_through_its_whole_lifecycle_over_http(
    api, database_url, sdk, container_name
):
    server_id = adopt(database_url, container_name, name="Lifecycle world")
    path = f"/servers/{server_id}"

    assert call(api, "get", path).json() == {
        "id": server_id, "name": "Lifecycle world", "game": "minecraft-java", "status": "created",
    }
    assert call(api, "post", f"{path}/start").json()["status"] == "running"
    logs = call(api, "get", f"{path}/logs?tail=5")
    assert logs.status_code == 200 and "hello-from-it" in logs.text
    assert call(api, "post", f"{path}/restart?timeout=1").json()["status"] == "running"
    assert call(api, "post", f"{path}/stop?timeout=1").json()["status"] == "exited"
    assert sdk.containers.get(container_name).status == "exited"

    listed = call(api, "get", "/servers")
    assert server_id in [s["id"] for s in listed.json()]


def test_the_container_name_never_appears_in_any_response(api, database_url, container_name):
    server_id = adopt(database_url, container_name)
    for method, path in (
        ("get", "/servers"),
        ("get", f"/servers/{server_id}"),
        ("post", f"/servers/{server_id}/start"),
        ("get", f"/servers/{server_id}/logs"),
        ("post", f"/servers/{server_id}/stop?timeout=1"),
    ):
        response = call(api, method, path)
        assert container_name not in response.text, path
        assert OWNER not in response.text, path


def test_requests_without_a_valid_token_change_nothing(api, database_url, sdk, container_name):
    server_id = adopt(database_url, container_name)
    path = f"/servers/{server_id}/start"
    assert call(api, "post", path, authorized=False).status_code == 401
    bad = {"Authorization": "Bearer not-the-right-token-0123456789"}
    assert httpx.post(api["base_url"] + path, headers=bad, timeout=60).status_code == 401
    assert sdk.containers.get(container_name).status == "created"


def test_a_container_that_is_not_managed_cannot_be_registered_or_reached(
    api, database_url, sdk, unmanaged_container_name
):
    result = run_cli(
        database_url, "servers", "adopt", unmanaged_container_name,
        "--owner", OWNER, "--plan", PLAN_SLUG, "--name", "Must not exist",
    )
    assert result.returncode == 3
    assert "was not found" in result.stderr
    listing = run_cli(database_url, "servers", "list").stdout
    assert unmanaged_container_name not in listing
    assert sdk.containers.get(unmanaged_container_name).status == "created"


def test_an_adopted_server_whose_container_disappears_is_reported_not_found(
    api, database_url, sdk, container_name
):
    server_id = adopt(database_url, container_name)
    sdk.containers.get(container_name).remove(force=True)
    assert call(api, "get", f"/servers/{server_id}").json()["status"] == "not_found"
    assert call(api, "post", f"/servers/{server_id}/start").status_code == 404


def test_unknown_ids_and_invalid_input(api):
    assert call(api, "get", f"/servers/{uuid.uuid4()}").status_code == 404
    assert call(api, "post", f"/servers/{uuid.uuid4()}/start").status_code == 404
    assert call(api, "get", "/servers/minecraft-dev").status_code == 422
    assert call(api, "post", f"/servers/{uuid.uuid4()}/stop?timeout=0").status_code == 422


def test_docs_are_not_exposed_and_health_is_open(api):
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert call(api, "get", path, authorized=False).status_code == 404
    assert call(api, "get", "/health", authorized=False).json() == {"status": "ok"}


def test_registrations_survive_an_api_restart(database_url, tmp_path, container_name):
    server_id = adopt(database_url, container_name, name="Durable world")
    with api_process(tmp_path / "second.log", database_url) as second:
        response = call(second, "get", f"/servers/{server_id}")
    assert response.status_code == 200
    assert response.json()["name"] == "Durable world"


def test_with_the_database_down_the_api_answers_503_and_stays_alive(tmp_path):
    unreachable = "postgresql+psycopg://gh_user:db-s3cret-p4ss@127.0.0.1:1/gh_db"
    with api_process(tmp_path / "down.log", unreachable) as down:
        assert call(down, "get", "/health", authorized=False).status_code == 200
        response = call(down, "get", "/servers")
        assert response.status_code == 503
        assert response.json() == {"detail": "The platform database is unavailable."}
        assert "db-s3cret-p4ss" not in response.text
    log = (tmp_path / "down.log").read_text(errors="replace")
    assert "db-s3cret-p4ss" not in log
    assert "gh_user:***@127.0.0.1:1/gh_db" in log


def test_the_server_log_records_rejections_but_never_a_secret(api, database_url):
    call(api, "post", f"/servers/{uuid.uuid4()}/start", authorized=False)
    log = api["log_path"].read_text(errors="replace")
    assert "Rejected request with a missing or invalid token" in log
    assert "API configured; database" in log
    assert api["token"] not in log
    assert "***@" in log  # the database URL is logged redacted
    password = database_url.split(":")[2].split("@")[0]
    assert password not in log
