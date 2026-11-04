"""Development and operator command-line interface.

Usage (from the backend/ directory):

    # Control a container by name (no database needed)
    python cli.py [--server NAME] status | start | stop [--timeout S] | restart [--timeout S] | logs [--tail N]

    # Operator administration (needs GAME_HOSTING_DATABASE_URL)
    python cli.py plans add --slug SLUG --name NAME --memory-mb MB --cpu-millicores M
    python cli.py plans list
    python cli.py servers adopt CONTAINER --owner EMAIL --plan SLUG --name NAME
    python cli.py servers list

This is a thin layer over the application services: it parses arguments,
calls them, and turns application errors into messages and exit codes. The
administration commands are a bootstrap for the operator until users, server
creation and plan management exist as proper features (Phases 4, 5 and 7).

Exit codes: 0 ok, 1 other application error, 2 invalid usage (argparse),
3 not found (server, plan or user), 4 Docker unavailable, 5 operation failed,
6 already exists, 7 database unavailable, 8 invalid data, 9 configuration.
"""

import argparse
import os
import sys

from app.composition import build_services
from app.errors import (
    AlreadyExistsError,
    ConfigurationError,
    DatabaseUnavailableError,
    GameHostingError,
    InvalidDataError,
    PlanNotFoundError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
    UserNotFoundError,
)
from app.infrastructure.db.database import DATABASE_URL_VARIABLE
from app.infrastructure.docker_client import DockerClient
from app.services.server_service import (
    DEFAULT_LOG_TAIL,
    DEFAULT_STOP_TIMEOUT,
    ServerService,
)

DEFAULT_SERVER = "minecraft-dev"

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_NOT_FOUND = 3
EXIT_RUNTIME_UNAVAILABLE = 4
EXIT_OPERATION_FAILED = 5
EXIT_ALREADY_EXISTS = 6
EXIT_DATABASE_UNAVAILABLE = 7
EXIT_INVALID_DATA = 8
EXIT_CONFIGURATION = 9

# Most specific errors first; GameHostingError is the generic fallback.
_EXIT_CODES = (
    (ServerNotFoundError, EXIT_NOT_FOUND),
    (UserNotFoundError, EXIT_NOT_FOUND),
    (PlanNotFoundError, EXIT_NOT_FOUND),
    (RuntimeUnavailableError, EXIT_RUNTIME_UNAVAILABLE),
    (DatabaseUnavailableError, EXIT_DATABASE_UNAVAILABLE),
    (ServerOperationError, EXIT_OPERATION_FAILED),
    (AlreadyExistsError, EXIT_ALREADY_EXISTS),
    (InvalidDataError, EXIT_INVALID_DATA),
    (GameHostingError, EXIT_ERROR),
)

CONTAINER_COMMANDS = ("status", "start", "stop", "restart", "logs")
ADMIN_COMMANDS = ("plans", "servers")


def _positive_int(value):
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"'{value}' is not a whole number")
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return number


def build_parser():
    parser = argparse.ArgumentParser(
        prog="cli.py", description="Control game server containers and administer the platform."
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"container name for status/start/stop/restart/logs; default: {DEFAULT_SERVER}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="show the container status")
    commands.add_parser("start", help="start the container")

    for name, help_text in (("stop", "stop the container"), ("restart", "restart the container")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--timeout",
            type=_positive_int,
            default=DEFAULT_STOP_TIMEOUT,
            help=f"seconds to wait before Docker kills the server; default: {DEFAULT_STOP_TIMEOUT}",
        )

    logs = commands.add_parser("logs", help="show the latest container logs")
    logs.add_argument(
        "--tail",
        type=_positive_int,
        default=DEFAULT_LOG_TAIL,
        help=f"number of lines to show; default: {DEFAULT_LOG_TAIL}",
    )

    plans = commands.add_parser("plans", help=f"manage plans (needs {DATABASE_URL_VARIABLE})")
    plan_actions = plans.add_subparsers(dest="action", required=True)
    add_plan = plan_actions.add_parser("add", help="create a plan")
    add_plan.add_argument("--slug", required=True, help="short unique id, e.g. dev")
    add_plan.add_argument("--name", required=True, help="display name")
    add_plan.add_argument("--memory-mb", type=_positive_int, required=True)
    add_plan.add_argument(
        "--cpu-millicores", type=_positive_int, required=True, help="1000 = one CPU core"
    )
    plan_actions.add_parser("list", help="list plans")

    servers = commands.add_parser(
        "servers", help=f"manage registered servers (needs {DATABASE_URL_VARIABLE})"
    )
    server_actions = servers.add_subparsers(dest="action", required=True)
    adopt = server_actions.add_parser(
        "adopt",
        help="register an existing managed container as a platform server",
    )
    adopt.add_argument("container", help="name of the existing container")
    adopt.add_argument("--owner", required=True, help="owner e-mail (created if new)")
    adopt.add_argument("--plan", required=True, help="slug of an existing plan")
    adopt.add_argument(
        "--name", required=True, help="display name shown to customers (not the container name)"
    )
    server_actions.add_parser("list", help="list registered servers")
    return parser


def _table(headers, rows):
    if not rows:
        return None
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    lines = ["  ".join(str(cell).ljust(width) for cell, width in zip(row, widths)).rstrip()
             for row in [headers, *rows]]
    return "\n".join(lines)


def _run_container_command(args, service):
    if args.command == "status":
        status = service.get_server_status(args.server)
    elif args.command == "start":
        status = service.start_server(args.server)
    elif args.command == "stop":
        status = service.stop_server(args.server, timeout=args.timeout)
    elif args.command == "restart":
        status = service.restart_server(args.server, timeout=args.timeout)
    else:  # logs
        print(service.get_server_logs(args.server, tail=args.tail), end="")
        return
    print(f"{args.server}: {status}")


def _run_plans_command(args, admin):
    if args.action == "add":
        plan = admin.plans.add_plan(
            args.slug, args.name, args.memory_mb, args.cpu_millicores
        )
        print(f"Plan '{plan.slug}' added.")
        print(f"id: {plan.id}")
        print(f"memory_mb: {plan.memory_mb}")
        print(f"cpu_millicores: {plan.cpu_millicores}")
        return
    table = _table(
        ("SLUG", "NAME", "MEMORY_MB", "CPU_MILLICORES"),
        [(p.slug, p.name, p.memory_mb, p.cpu_millicores) for p in admin.plans.list_plans()],
    )
    print(table or "(no plans)")


def _run_servers_command(args, admin):
    if args.action == "adopt":
        adoption = admin.registry.adopt_container(
            args.container, args.owner, args.plan, name=args.name
        )
        registered = adoption.registered
        print(f"Adopted container '{args.container}' as a platform server.")
        print(f"id: {registered.server.id}")
        print(f"name: {registered.server.name}")
        print(
            f"owner: {registered.owner.email} "
            f"({'created' if adoption.owner_created else 'existing'})"
        )
        print(f"plan: {registered.plan.slug}")
        print(f"status: {registered.status}")
        return
    table = _table(
        ("ID", "NAME", "CONTAINER", "OWNER", "PLAN", "STATUS"),
        [
            (r.server.id, r.server.name, r.server.container_name, r.owner.email, r.plan.slug, r.status)
            for r in admin.registry.list_servers()
        ],
    )
    print(table or "(no servers)")


def build_service():
    return ServerService(DockerClient())


def build_admin():
    """The real services for the administration commands (needs the database URL)."""
    url = os.environ.get(DATABASE_URL_VARIABLE, "")
    if not url:
        raise ConfigurationError(
            f"{DATABASE_URL_VARIABLE} is not set. Set it to the database URL "
            "(postgresql+psycopg://user:password@host:port/dbname) and retry."
        )
    try:
        return build_services(url)
    except ValueError as error:
        # The message never contains the URL, so it is safe to show.
        raise ConfigurationError(f"{DATABASE_URL_VARIABLE} is not usable: {error}") from None


def main(argv=None, service=None, admin=None):
    args = build_parser().parse_args(argv)
    built_admin = None
    try:
        if args.command in ADMIN_COMMANDS:
            if admin is None:
                admin = built_admin = build_admin()
            runner = _run_plans_command if args.command == "plans" else _run_servers_command
            runner(args, admin)
        else:
            _run_container_command(args, service if service is not None else build_service())
    except ConfigurationError as error:
        print(f"error: {error}", file=sys.stderr)
        return EXIT_CONFIGURATION
    except GameHostingError as error:
        print(f"error: {error}", file=sys.stderr)
        for error_type, exit_code in _EXIT_CODES:
            if isinstance(error, error_type):
                return exit_code
    finally:
        if built_admin is not None:
            built_admin.close()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
