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


class DatabaseUnavailableError(GameHostingError):
    """The platform database (PostgreSQL) cannot be reached."""


class InvalidDataError(GameHostingError):
    """A value does not satisfy the rules of the domain (for example a bad e-mail)."""


class UserNotFoundError(GameHostingError):
    """The requested user does not exist."""

    def __init__(self, user_id):
        super().__init__(f"User '{user_id}' was not found.")
        self.user_id = user_id


class PlanNotFoundError(GameHostingError):
    """The requested plan does not exist."""

    def __init__(self, plan_id):
        super().__init__(f"Plan '{plan_id}' was not found.")
        self.plan_id = plan_id


class AlreadyExistsError(GameHostingError):
    """A unique value (e-mail, plan slug, container name) is already taken."""

    def __init__(self, entity, field):
        super().__init__(f"A {entity} with this {field} already exists.")
        self.entity = entity
        self.field = field
