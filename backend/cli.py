"""Development command-line interface for controlling a game server.

Usage (from the backend/ directory):

    python cli.py status
    python cli.py start
    python cli.py stop [--timeout SECONDS]
    python cli.py restart [--timeout SECONDS]
    python cli.py logs [--tail LINES]

This is a thin layer over ServerService: it parses arguments, calls the
service, and turns application errors into messages and exit codes.
Exit codes: 0 ok, 1 other application error, 2 invalid usage (argparse),
3 server not found, 4 Docker unavailable, 5 operation failed.
"""

import argparse
import sys

from app.errors import (
    GameHostingError,
    RuntimeUnavailableError,
    ServerNotFoundError,
    ServerOperationError,
)
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

# Most specific errors first; GameHostingError is the generic fallback.
_EXIT_CODES = (
    (ServerNotFoundError, EXIT_NOT_FOUND),
    (RuntimeUnavailableError, EXIT_RUNTIME_UNAVAILABLE),
    (ServerOperationError, EXIT_OPERATION_FAILED),
    (GameHostingError, EXIT_ERROR),
)


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
        prog="cli.py", description="Control a game server container."
    )
    parser.add_argument(
        "--server",
        default=DEFAULT_SERVER,
        help=f"server (container) name; default: {DEFAULT_SERVER}",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    commands.add_parser("status", help="show the server status")
    commands.add_parser("start", help="start the server")

    for name, help_text in (("stop", "stop the server"), ("restart", "restart the server")):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            "--timeout",
            type=_positive_int,
            default=DEFAULT_STOP_TIMEOUT,
            help=f"seconds to wait before Docker kills the server; default: {DEFAULT_STOP_TIMEOUT}",
        )

    logs = commands.add_parser("logs", help="show the latest server logs")
    logs.add_argument(
        "--tail",
        type=_positive_int,
        default=DEFAULT_LOG_TAIL,
        help=f"number of lines to show; default: {DEFAULT_LOG_TAIL}",
    )
    return parser


def _run_command(args, service):
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


def build_service():
    return ServerService(DockerClient())


def main(argv=None, service=None):
    args = build_parser().parse_args(argv)
    if service is None:
        service = build_service()
    try:
        _run_command(args, service)
    except GameHostingError as error:
        print(f"error: {error}", file=sys.stderr)
        for error_type, exit_code in _EXIT_CODES:
            if isinstance(error, error_type):
                return exit_code
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
