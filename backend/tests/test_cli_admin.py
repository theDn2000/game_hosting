import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest

import cli
from app.domain.models import Game, GameServer, Plan, User
from app.errors import (
    AlreadyExistsError,
    DatabaseUnavailableError,
    InvalidDataError,
    PlanNotFoundError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    UserNotFoundError,
)
from app.infrastructure.db.database import DATABASE_URL_VARIABLE
from app.services.server_registry import Adoption, RegisteredServer
from app.services.server_service import ServerStatus

NOW = datetime(2026, 11, 4, tzinfo=UTC)
PASSWORD = "cli-s3cret-p4ss"


def registered(name="My world", container="minecraft-dev", email="ana@example.com", slug="dev"):
    owner = User.new(email, created_at=NOW)
    plan = Plan.new(slug, slug.title(), memory_mb=4096, cpu_millicores=2000)
    server = GameServer(
        id=GameServer.new(owner.id, plan.id, name).id,
        owner_id=owner.id,
        plan_id=plan.id,
        game=Game.MINECRAFT_JAVA,
        name=name,
        container_name=container,
        created_at=NOW,
    )
    return RegisteredServer(server, owner, plan, ServerStatus.RUNNING)


class FakePlans:
    def __init__(self, error=None):
        self.error, self.calls, self.plans = error, [], []

    def add_plan(self, slug, name, memory_mb, cpu_millicores):
        self.calls.append(("add", slug, name, memory_mb, cpu_millicores))
        if self.error:
            raise self.error
        plan = Plan.new(slug, name, memory_mb, cpu_millicores)
        self.plans.append(plan)
        return plan

    def list_plans(self):
        self.calls.append(("list",))
        if self.error:
            raise self.error
        return list(self.plans)


class FakeRegistry:
    def __init__(self, error=None, servers=(), owner_created=True):
        self.error, self.servers, self.owner_created = error, list(servers), owner_created
        self.calls = []

    def adopt_container(self, container, owner_email, plan_slug, name):
        self.calls.append(("adopt", container, owner_email, plan_slug, name))
        if self.error:
            raise self.error
        return Adoption(registered(name, container, owner_email, plan_slug), self.owner_created)

    def list_servers(self):
        self.calls.append(("list",))
        if self.error:
            raise self.error
        return self.servers


class FakeAdmin:
    def __init__(self, plans=None, registry=None):
        self.plans = plans or FakePlans()
        self.registry = registry or FakeRegistry()
        self.closed = 0

    def close(self):
        self.closed += 1


def run(argv, admin=None):
    admin = admin or FakeAdmin()
    return cli.main(argv, admin=admin), admin


# --- plans -----------------------------------------------------------------------


def test_plans_add_creates_the_plan_and_prints_its_details(capsys):
    code, admin = run(
        ["plans", "add", "--slug", "dev", "--name", "Dev plan", "--memory-mb", "4096",
         "--cpu-millicores", "2000"]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert admin.plans.calls == [("add", "dev", "Dev plan", 4096, 2000)]
    assert "Plan 'dev' added." in out
    assert f"id: {admin.plans.plans[0].id}" in out
    assert "memory_mb: 4096" in out and "cpu_millicores: 2000" in out


def test_plans_list_shows_a_table(capsys):
    admin = FakeAdmin()
    admin.plans.plans = [
        Plan.new("basic", "Basic", memory_mb=2048, cpu_millicores=1000),
        Plan.new("pro", "Professional", memory_mb=8192, cpu_millicores=4000),
    ]
    code, _ = run(["plans", "list"], admin)
    lines = capsys.readouterr().out.splitlines()
    assert code == cli.EXIT_OK
    assert lines[0].split() == ["SLUG", "NAME", "MEMORY_MB", "CPU_MILLICORES"]
    assert lines[1].split() == ["basic", "Basic", "2048", "1000"]
    assert lines[2].split() == ["pro", "Professional", "8192", "4000"]
    # columns line up
    assert lines[1].index("Basic") == lines[2].index("Professional") == lines[0].index("NAME")


def test_plans_list_when_empty(capsys):
    code, _ = run(["plans", "list"])
    assert code == cli.EXIT_OK
    assert capsys.readouterr().out == "(no plans)\n"


# --- servers ---------------------------------------------------------------------


def test_servers_adopt_prints_the_new_platform_id_and_what_was_created(capsys):
    code, admin = run(
        ["servers", "adopt", "minecraft-dev", "--owner", "ana@example.com", "--plan", "dev",
         "--name", "My world"]
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert admin.registry.calls == [("adopt", "minecraft-dev", "ana@example.com", "dev", "My world")]
    assert "Adopted container 'minecraft-dev'" in out
    assert "id: " in out and "name: My world" in out
    assert "owner: ana@example.com (created)" in out
    assert "plan: dev" in out and "status: running" in out


def test_servers_adopt_reports_an_existing_owner(capsys):
    admin = FakeAdmin(registry=FakeRegistry(owner_created=False))
    code, _ = run(
        ["servers", "adopt", "other", "--owner", "ana@example.com", "--plan", "dev", "--name", "Shown"],
        admin,
    )
    out = capsys.readouterr().out
    assert code == cli.EXIT_OK
    assert admin.registry.calls == [("adopt", "other", "ana@example.com", "dev", "Shown")]
    assert "owner: ana@example.com (existing)" in out


def test_servers_list_shows_the_operator_view_including_the_container(capsys):
    admin = FakeAdmin(registry=FakeRegistry(servers=[registered("World A"), registered("B", "b-container", "bob@example.com", "pro")]))
    code, _ = run(["servers", "list"], admin)
    lines = capsys.readouterr().out.splitlines()
    assert code == cli.EXIT_OK
    assert lines[0].split() == ["ID", "NAME", "CONTAINER", "OWNER", "PLAN", "STATUS"]
    assert "minecraft-dev" in lines[1] and "ana@example.com" in lines[1] and "running" in lines[1]
    assert "b-container" in lines[2] and "bob@example.com" in lines[2] and "pro" in lines[2]


def test_servers_list_when_empty(capsys):
    code, _ = run(["servers", "list"])
    assert code == cli.EXIT_OK
    assert capsys.readouterr().out == "(no servers)\n"


# --- errors ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "error, exit_code",
    [
        (AlreadyExistsError("plan", "slug"), cli.EXIT_ALREADY_EXISTS),
        (PlanNotFoundError("dev"), cli.EXIT_NOT_FOUND),
        (UserNotFoundError("x"), cli.EXIT_NOT_FOUND),
        (ServerNotFoundError("minecraft-dev"), cli.EXIT_NOT_FOUND),
        (DatabaseUnavailableError("db down"), cli.EXIT_DATABASE_UNAVAILABLE),
        (RuntimeUnavailableError("docker down"), cli.EXIT_RUNTIME_UNAVAILABLE),
        (InvalidDataError("bad value"), cli.EXIT_INVALID_DATA),
    ],
)
def test_administration_errors_become_messages_and_distinct_exit_codes(capsys, error, exit_code):
    admin = FakeAdmin(plans=FakePlans(error=error), registry=FakeRegistry(error=error))
    for argv in (
        ["plans", "list"],
        ["servers", "list"],
        ["servers", "adopt", "c", "--owner", "a@b.co", "--plan", "dev", "--name", "N"],
    ):
        code, _ = run(argv, admin)
        captured = capsys.readouterr()
        assert code == exit_code
        assert captured.out == ""
        assert captured.err == f"error: {error}\n"


def test_exit_codes_are_all_different_where_it_matters_to_scripts():
    codes = {
        cli.EXIT_ERROR, cli.EXIT_NOT_FOUND, cli.EXIT_RUNTIME_UNAVAILABLE,
        cli.EXIT_OPERATION_FAILED, cli.EXIT_ALREADY_EXISTS,
        cli.EXIT_DATABASE_UNAVAILABLE, cli.EXIT_INVALID_DATA, cli.EXIT_CONFIGURATION,
    }
    assert len(codes) == 8 and 0 not in codes and 2 not in codes  # 2 is argparse's


@pytest.mark.parametrize(
    "argv",
    [
        ["plans"],
        ["plans", "add"],
        ["plans", "add", "--slug", "dev", "--name", "N", "--memory-mb", "0", "--cpu-millicores", "1"],
        ["plans", "add", "--slug", "dev", "--name", "N", "--memory-mb", "1", "--cpu-millicores", "x"],
        ["servers"],
        ["servers", "adopt"],
        ["servers", "adopt", "c", "--owner", "a@b.co"],
        ["servers", "adopt", "c", "--plan", "dev"],
        ["servers", "adopt", "c", "--owner", "a@b.co", "--plan", "dev"],  # --name is required
    ],
)
def test_invalid_administration_usage_exits_with_2_without_calling_anything(argv):
    admin = FakeAdmin()
    with pytest.raises(SystemExit) as excinfo:
        cli.main(argv, admin=admin)
    assert excinfo.value.code == 2
    assert admin.plans.calls == [] and admin.registry.calls == []


# --- configuration and lifecycle ----------------------------------------------------


def test_administration_without_a_database_url_exits_with_a_configuration_error(
    capsys, monkeypatch
):
    monkeypatch.delenv(DATABASE_URL_VARIABLE, raising=False)
    assert cli.main(["plans", "list"]) == cli.EXIT_CONFIGURATION
    err = capsys.readouterr().err
    assert DATABASE_URL_VARIABLE in err and "is not set" in err


@pytest.mark.parametrize("url", [f"postgresql://u:{PASSWORD}@h/db", f"sqlite:///{PASSWORD}.db", f"x:{PASSWORD}@y"])
def test_an_unusable_database_url_is_reported_without_echoing_it(capsys, monkeypatch, url):
    monkeypatch.setenv(DATABASE_URL_VARIABLE, url)
    assert cli.main(["servers", "list"]) == cli.EXIT_CONFIGURATION
    captured = capsys.readouterr()
    assert PASSWORD not in captured.err and PASSWORD not in captured.out
    assert DATABASE_URL_VARIABLE in captured.err


def test_container_commands_never_need_the_database(monkeypatch):
    monkeypatch.delenv(DATABASE_URL_VARIABLE, raising=False)
    monkeypatch.setattr(cli, "build_admin", lambda: pytest.fail("the database must not be needed"))

    class Service:
        def get_server_status(self, name):
            return ServerStatus.RUNNING

    assert cli.main(["status"], service=Service()) == cli.EXIT_OK


@pytest.mark.parametrize("fail", [False, True])
def test_services_built_by_the_cli_are_always_closed(monkeypatch, fail):
    admin = FakeAdmin(registry=FakeRegistry(error=DatabaseUnavailableError("down") if fail else None))
    monkeypatch.setattr(cli, "build_admin", lambda: admin)
    cli.main(["servers", "list"])
    assert admin.closed == 1


def test_injected_services_are_not_closed_by_the_cli():
    code, admin = run(["servers", "list"])
    assert admin.closed == 0


def test_real_process_without_a_database_url_exits_with_9_and_no_traceback():
    env = {k: v for k, v in os.environ.items() if k != DATABASE_URL_VARIABLE}
    result = subprocess.run(
        [sys.executable, "cli.py", "plans", "list"],
        cwd=os.path.dirname(os.path.abspath(cli.__file__)),
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == cli.EXIT_CONFIGURATION
    assert "Traceback" not in result.stderr
    assert DATABASE_URL_VARIABLE in result.stderr
