"""Persistence interfaces (ports) used by the application services.

The application layer depends on these protocols, never on a database. Each
use case runs inside a `UnitOfWork`: changes become permanent only on an
explicit `commit()`; leaving the block without committing (or with an
exception) discards them.

Errors are the application errors from `app.errors`: lookups of missing
entities raise `UserNotFoundError`, `PlanNotFoundError` or `ServerNotFoundError`
(the latter for a platform server id); violating a unique value raises
`AlreadyExistsError`; storage that cannot be reached raises
`DatabaseUnavailableError`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Protocol
from uuid import UUID

if TYPE_CHECKING:
    from app.domain.models import GameServer, Plan, User


class UserRepository(Protocol):
    def add(self, user: User) -> None:
        """Store a new user. Raises AlreadyExistsError if the e-mail is taken."""

    def get(self, user_id: UUID) -> User:
        """Raises UserNotFoundError."""

    def find_by_email(self, email: str) -> User | None:
        """The e-mail is normalised (trimmed, lowercased) before searching."""


class PlanRepository(Protocol):
    def add(self, plan: Plan) -> None:
        """Store a new plan. Raises AlreadyExistsError if the slug is taken."""

    def get(self, plan_id: UUID) -> Plan:
        """Raises PlanNotFoundError."""

    def find_by_slug(self, slug: str) -> Plan | None: ...


class GameServerRepository(Protocol):
    def add(self, server: GameServer) -> None:
        """Store a new server.

        Raises UserNotFoundError / PlanNotFoundError if the owner or plan does
        not exist, and AlreadyExistsError if the container name is taken.
        """

    def get(self, server_id: UUID) -> GameServer:
        """Raises ServerNotFoundError."""

    def find_by_container_name(self, container_name: str) -> GameServer | None: ...

    def list_by_owner(self, owner_id: UUID) -> list[GameServer]:
        """Ordered by creation time, then id."""

    def list_all(self) -> list[GameServer]:
        """Ordered by creation time, then id."""


class UnitOfWork(Protocol):
    users: UserRepository
    plans: PlanRepository
    servers: GameServerRepository

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None:
        """Discards uncommitted changes. Never commits implicitly."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
