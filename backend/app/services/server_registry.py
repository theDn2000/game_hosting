"""Platform servers: the link between a platform id and a runtime container.

Customers and the API know servers only by their platform UUID. This service
looks the id up in the database, obtains the internal container name, and
delegates the actual work to `ServerService`, which keeps operating on container
names and knows nothing about users or plans.

Authorization: every operation takes the `Actor` performing it, decided by the
caller from a verified session and never from client input. A customer may act
only on servers they own; an administrator on all of them. A server that exists
but belongs to someone else is reported exactly like one that does not exist
(`ServerNotFoundError`, same message), so the existence of other customers'
servers cannot be probed, and the check happens before Docker is touched.
Operations that are not about one server (adoption) need an administrator and
raise `PermissionDeniedError` otherwise. An actor that is not an `Actor` is a
programming error and is rejected outright (deny by default).

The database transaction is always closed before talking to Docker, so a slow
container operation never holds a connection or a transaction open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.auth import Actor
from app.domain.models import GameServer, User
from app.errors import (
    AlreadyExistsError,
    InvalidDataError,
    PermissionDeniedError,
    PlanNotFoundError,
    ServerNotFoundError,
)
from app.services.server_service import (
    DEFAULT_LOG_TAIL,
    DEFAULT_STOP_TIMEOUT,
    ServerStatus,
)

if TYPE_CHECKING:
    from uuid import UUID

    from app.domain.models import Plan
    from app.services.repositories import UnitOfWorkFactory
    from app.services.server_service import ServerService


@dataclass(frozen=True, slots=True)
class RegisteredServer:
    """A platform server with its owner, plan and live runtime status."""

    server: GameServer
    owner: User
    plan: Plan
    status: ServerStatus


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 100


@dataclass(frozen=True, slots=True)
class Page:
    """One page of a list plus the total number of items, so clients can page."""

    items: list
    total: int
    limit: int
    offset: int


@dataclass(frozen=True, slots=True)
class Adoption:
    registered: RegisteredServer
    owner_created: bool


class ServerRegistry:
    def __init__(self, uow_factory: UnitOfWorkFactory, server_service: ServerService):
        self._uow_factory = uow_factory
        self._service = server_service

    # --- reading ------------------------------------------------------------

    def list_servers(self, actor, limit=DEFAULT_PAGE_SIZE, offset=0):
        """A page of the servers the actor may see (all of them for an
        administrator, their own otherwise), oldest first.

        Costs three database queries and one Docker call whatever the page
        size: owners and plans are fetched in bulk, and so are the statuses.
        """
        _require_actor(actor)
        if (
            not isinstance(limit, int)
            or isinstance(limit, bool)
            or not 1 <= limit <= MAX_PAGE_SIZE
        ):
            raise InvalidDataError(f"limit must be between 1 and {MAX_PAGE_SIZE}.")
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise InvalidDataError("offset must not be negative.")
        scope = None if actor.is_admin else actor.user_id
        with self._uow_factory() as uow:
            servers = uow.servers.list_page(scope, limit, offset)
            total = uow.servers.count(scope)
            owners = uow.users.get_many(s.owner_id for s in servers)
            plans = uow.plans.get_many(s.plan_id for s in servers)
        statuses = (
            self._service.get_server_statuses([s.container_name for s in servers])
            if servers
            else {}
        )
        items = [
            RegisteredServer(
                server,
                owners[server.owner_id],
                plans[server.plan_id],
                statuses[server.container_name],
            )
            for server in servers
        ]
        return Page(items, total, limit, offset)

    def get_server(self, actor, server_id: UUID):
        """A server and its status. A registered server whose container is gone
        is reported with `ServerStatus.NOT_FOUND`, not as an error."""
        server, owner, plan = self._load(actor, server_id)
        return RegisteredServer(
            server, owner, plan, self._service.get_server_status(server.container_name)
        )

    # --- lifecycle (delegated to ServerService by container name) -------------

    def start_server(self, actor, server_id: UUID):
        server, owner, plan = self._load(actor, server_id)
        status = self._service.start_server(server.container_name)
        return RegisteredServer(server, owner, plan, status)

    def stop_server(self, actor, server_id: UUID, timeout: int = DEFAULT_STOP_TIMEOUT):
        server, owner, plan = self._load(actor, server_id)
        status = self._service.stop_server(server.container_name, timeout=timeout)
        return RegisteredServer(server, owner, plan, status)

    def restart_server(self, actor, server_id: UUID, timeout: int = DEFAULT_STOP_TIMEOUT):
        server, owner, plan = self._load(actor, server_id)
        status = self._service.restart_server(server.container_name, timeout=timeout)
        return RegisteredServer(server, owner, plan, status)

    def get_server_logs(self, actor, server_id: UUID, tail: int = DEFAULT_LOG_TAIL):
        server, _, _ = self._load(actor, server_id)
        return self._service.get_server_logs(server.container_name, tail=tail)

    # --- administration -----------------------------------------------------

    def adopt_container(self, actor, container_name, owner_email, plan_slug, name):
        """Register an existing managed container as a platform server.

        The container must exist and carry the managed label. The plan must
        exist; the owner is created if no user has that e-mail. `name` is the
        display name shown to customers and is deliberately required: it must
        not default to the container name, which is an internal detail.
        Everything is stored in one transaction: if any step fails, nothing is
        kept. Administrators only.
        """
        _require_actor(actor)
        if not actor.is_admin:
            raise PermissionDeniedError()
        status = self._service.get_server_status(container_name)
        if status is ServerStatus.NOT_FOUND:
            raise ServerNotFoundError(container_name)
        with self._uow_factory() as uow:
            plan = uow.plans.find_by_slug(plan_slug)
            if plan is None:
                raise PlanNotFoundError(plan_slug)
            if uow.servers.find_by_container_name(container_name) is not None:
                raise AlreadyExistsError("server", "container name")
            candidate = User.new(owner_email)
            owner = uow.users.find_by_email(candidate.email)
            owner_created = owner is None
            if owner_created:
                owner = candidate
                uow.users.add(owner)
            server = GameServer.new(
                owner.id, plan.id, name, container_name=container_name
            )
            uow.servers.add(server)
            uow.commit()
        return Adoption(RegisteredServer(server, owner, plan, status), owner_created)

    # --- internals ----------------------------------------------------------

    def _load(self, actor, server_id):
        """The server, its owner and its plan, read in a short transaction.

        Raises `ServerNotFoundError` both when there is no such server and when
        the actor may not access it, with identical content.
        """
        _require_actor(actor)
        with self._uow_factory() as uow:
            server = uow.servers.get(server_id)
            if not actor.is_admin and server.owner_id != actor.user_id:
                raise ServerNotFoundError(server_id)
            return server, uow.users.get(server.owner_id), uow.plans.get(server.plan_id)


def _require_actor(actor):
    if not isinstance(actor, Actor):
        raise TypeError("an Actor is required; operations are never anonymous")
