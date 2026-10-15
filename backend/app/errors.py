"""Application-level errors.

These are the only failures the application layers expose. Infrastructure
adapters translate their own exceptions (for example Docker SDK errors) into
these, so callers never depend on infrastructure-specific types.
"""


class GameHostingError(Exception):
    """Base class for all application-level errors."""


class ServerNotFoundError(GameHostingError):
    """The requested server does not exist."""

    def __init__(self, server_name):
        super().__init__(f"Server '{server_name}' was not found.")
        self.server_name = server_name


class RuntimeUnavailableError(GameHostingError):
    """The runtime that hosts the servers (Docker) cannot be reached."""


class ServerOperationError(GameHostingError):
    """The runtime was reachable but failed to perform the operation."""
