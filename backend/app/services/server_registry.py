"""Platform servers: the link between a platform id and a runtime container.

Customers and the API know servers only by their platform UUID. This service
looks the id up in the database, obtains the internal container name, and
delegates the actual work to `ServerService`, which keeps operating on container
names and knows nothing about users or plans.

Ownership is stored but NOT enforced yet: until users can authenticate
(Phase 4) the API's shared token acts as the operator and may act on every
server.

The database transaction is always closed before talking to Docker, so a slow
container operation never holds a connection or a transaction open.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.domain.models import GameServer, User
from app.errors import AlreadyExistsError, PlanNotFoundError, ServerNotFoundError
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


@dataclass(frozen=True, slots=True)
class Adoption:
    registered: RegisteredServer
    owner_created: bool


class ServerRegistry:
    def __init__(self, uow_factory: UnitOfWorkFactory, server_service: ServerService):
        self._uow_factory = uow_factory
        self._service = server_service

    # --- reading ------------------------------------------------------------

    def list_servers(self):
        """All registered servers, oldest first. Needs Docker for the statuses."""
        with self._uow_factory() as uow:
            servers = uow.servers.list_all()
            owners = {s.owner_id: uow.users.get(s.owner_id) for s in servers}
            plans = {s.plan_id: uow.plans.get(s.plan_id) for s in servers}
        return [
            RegisteredServer(
                server,
                owners[server.owner_id],
                plans[server.plan_id],
                self._service.get_server_status(server.container_name),
            )
            for server in servers
        ]

    def get_server(self, server_id: UUID):
        """A server and its status. A registered server whose container is gone
        is reported with `ServerStatus.NOT_FOUND`, not as an error."""
        server, owner, plan = self._load(server_id)
        return RegisteredServer(
            server, owner, plan, self._service.get_server_status(server.container_name)
        )

    # --- lifecycle (delegated to ServerService by container name) -------------

    def start_server(self, server_id: UUID):
        server, owner, plan = self._load(server_id)
        status = self._service.start_server(server.container_name)
        return RegisteredServer(server, owner, plan, status)

    def stop_server(self, server_id: UUID, timeout: int = DEFAULT_STOP_TIMEOUT):
        server, owner, plan = self._load(server_id)
        status = self._service.stop_server(server.container_name, timeout=timeout)
        return RegisteredServer(server, owner, plan, status)

    def restart_server(self, server_id: UUID, timeout: int = DEFAULT_STOP_TIMEOUT):
        server, owner, plan = self._load(server_id)
        status = self._service.restart_server(server.container_name, timeout=timeout)
        return RegisteredServer(server, owner, plan, status)

    def get_server_logs(self, server_id: UUID, tail: int = DEFAULT_LOG_TAIL):
        server, _, _ = self._load(server_id)
        return self._service.get_server_logs(server.container_name, tail=tail)

    # --- administration -----------------------------------------------------

    def adopt_container(self, container_name, owner_email, plan_slug, name):
        """Register an existing managed container as a platform server.

        The container must exist and carry the managed label. The plan must
        exist; the owner is created if no user has that e-mail. `name` is the
        display name shown to customers and is deliberately required: it must
        not default to the container name, which is an internal detail.
        Everything is stored in one transaction: if any step fails, nothing is
        kept.
        """
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

    def _load(self, server_id):
        """The server, its owner and its plan, read in a short transaction."""
        with self._uow_factory() as uow:
            server = uow.servers.get(server_id)
            return server, uow.users.get(server.owner_id), uow.plans.get(server.plan_id)
