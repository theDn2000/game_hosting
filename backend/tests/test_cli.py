from unittest.mock import patch

import pytest

import cli
from app.errors import (
    GameHostingError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
from app.services.server_service import ServerStatus


class FakeService:
    """Stand-in for ServerService that records calls and can raise an error."""

    def __init__(self, error=None):
        self.error = error
        self.calls = []

    def _record(self, *call):
        self.calls.append(call)
        if self.error:
            raise self.error

    def get_server_status(self, server):
        self._record("status", server)
        return ServerStatus.RUNNING

    def start_server(self, server):
        self._record("start", server)
        return ServerStatus.RUNNING

    def stop_server(self, server, timeout):
        self._record("stop", server, timeout)
        return ServerStatus.EXITED

    def restart_server(self, server, timeout):
        self._record("restart", server, timeout)
        return ServerStatus.RUNNING

    def get_server_logs(self, server, tail):
        self._record("logs", server, tail)
        return "line 1\nline 2\n"


def run(argv, service=None):
    service = service or FakeService()
    return cli.main(argv, service=service), service


@pytest.mark.parametrize(
    "argv, expected_call",
    [
        (["status"], ("status", "minecraft-dev")),
        (["start"], ("start", "minecraft-dev")),
        (["stop"], ("stop", "minecraft-dev", 30)),
        (["restart"], ("restart", "minecraft-dev", 30)),
        (["logs"], ("logs", "minecraft-dev", 100)),
    ],
)
def test_commands_call_service_with_defaults(argv, expected_call):
    code, service = run(argv)
    assert code == cli.EXIT_OK
    assert service.calls == [expected_call]


def test_server_option_overrides_default():
    _, service = run(["--server", "other-server", "start"])
    assert service.calls == [("start", "other-server")]


def test_timeout_and_tail_options_are_passed():
    _, service = run(["stop", "--timeout", "60"])
    assert service.calls == [("stop", "minecraft-dev", 60)]
    _, service = run(["restart", "--timeout", "5"])
    assert service.calls == [("restart", "minecraft-dev", 5)]
    _, service = run(["logs", "--tail", "7"])
    assert service.calls == [("logs", "minecraft-dev", 7)]


@pytest.mark.parametrize(
    "argv, expected_output",
    [
        (["status"], "minecraft-dev: running\n"),
        (["start"], "minecraft-dev: running\n"),
        (["stop"], "minecraft-dev: exited\n"),
        (["restart"], "minecraft-dev: running\n"),
        (["logs"], "line 1\nline 2\n"),
    ],
)
def test_command_output(capsys, argv, expected_output):
    run(argv)
    captured = capsys.readouterr()
    assert captured.out == expected_output
    assert captured.err == ""


@pytest.mark.parametrize(
    "error, exit_code",
    [
        (ServerNotFoundError("minecraft-dev"), 3),
        (RuntimeUnavailableError("docker is down"), 4),
        (ServerOperationError("operation failed"), 5),
        (GameHostingError("something else"), 1),
    ],
)
def test_application_errors_become_message_and_exit_code(capsys, error, exit_code):
    code, _ = run(["start"], FakeService(error=error))
    captured = capsys.readouterr()
    assert code == exit_code
    assert captured.out == ""
    assert captured.err == f"error: {error}\n"
    assert "Traceback" not in captured.err


def test_unexpected_errors_are_not_swallowed():
    with pytest.raises(RuntimeError):
        run(["status"], FakeService(error=RuntimeError("bug")))


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["unknown"],
        ["stop", "--timeout", "0"],
        ["stop", "--timeout", "abc"],
        ["restart", "--timeout", "-3"],
        ["logs", "--tail", "0"],
    ],
)
def test_invalid_usage_exits_with_2_without_calling_service(argv):
    service = FakeService()
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv, service=service)
    assert excinfo.value.code == 2
    assert service.calls == []


def test_builds_the_real_service_only_when_none_is_given():
    fake = FakeService()
    with patch("cli.build_service", return_value=fake) as build_service:
        assert cli.main(["status"]) == cli.EXIT_OK
    build_service.assert_called_once_with()
    assert fake.calls == [("status", "minecraft-dev")]
