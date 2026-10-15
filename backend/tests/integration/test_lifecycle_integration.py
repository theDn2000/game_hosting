import pytest

import cli
from app.errors import ServerNotFoundError
from app.services.server_service import ServerStatus

pytestmark = pytest.mark.integration


def started_at(sdk, name):
    return sdk.containers.get(name).attrs["State"]["StartedAt"]


def test_full_lifecycle(sdk, service, container_name):
    assert service.get_server_status(container_name) == ServerStatus.CREATED

    assert service.start_server(container_name) == ServerStatus.RUNNING
    assert service.start_server(container_name) == ServerStatus.RUNNING

    first_start = started_at(sdk, container_name)
    assert service.restart_server(container_name, timeout=1) == ServerStatus.RUNNING
    assert started_at(sdk, container_name) != first_start

    assert service.stop_server(container_name, timeout=1) == ServerStatus.EXITED
    assert service.stop_server(container_name, timeout=1) == ServerStatus.EXITED

    assert service.start_server(container_name) == ServerStatus.RUNNING


def test_logs_are_returned_as_text(service, container_name):
    service.start_server(container_name)
    logs = service.get_server_logs(container_name, tail=10)
    assert "hello-from-it" in logs


def test_missing_server(service):
    name = "game-hosting-it-does-not-exist"
    assert service.get_server_status(name) == ServerStatus.NOT_FOUND
    with pytest.raises(ServerNotFoundError):
        service.start_server(name)


def test_cli_end_to_end(capsys, container_name):
    assert cli.main(["--server", container_name, "start"]) == cli.EXIT_OK
    assert cli.main(["--server", container_name, "status"]) == cli.EXIT_OK
    assert cli.main(["--server", container_name, "logs", "--tail", "5"]) == cli.EXIT_OK
    assert cli.main(["--server", container_name, "stop", "--timeout", "1"]) == cli.EXIT_OK
    output = capsys.readouterr().out
    assert f"{container_name}: running" in output
    assert "hello-from-it" in output
    assert f"{container_name}: exited" in output


def test_cli_missing_server_exit_code(capsys):
    code = cli.main(["--server", "game-hosting-it-does-not-exist", "start"])
    assert code == cli.EXIT_NOT_FOUND
    assert "was not found" in capsys.readouterr().err
