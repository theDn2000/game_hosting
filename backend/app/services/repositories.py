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
    from datetime import datetime

    from app.domain.auth import LoginFailure, Session
    from app.domain.models import GameServer, Plan, User


class UserRepository(Protocol):
    def add(self, user: User) -> None:
        """Store a new user. Raises AlreadyExistsError if the e-mail is taken."""

    def get(self, user_id: UUID) -> User:
        """Raises UserNotFoundError."""

    def find_by_email(self, email: str) -> User | None:
        """The e-mail is normalised (trimmed, lowercased) before searching."""

    def update(self, user: User) -> None:
        """Replace the stored e-mail, role, password hash and active flag of the
        user with this id. Raises UserNotFoundError if there is none and
        AlreadyExistsError if the new e-mail belongs to another user."""

    def list_all(self) -> list[User]:
        """Ordered by e-mail."""


class PlanRepository(Protocol):
    def add(self, plan: Plan) -> None:
        """Store a new plan. Raises AlreadyExistsError if the slug is taken."""

    def get(self, plan_id: UUID) -> Plan:
        """Raises PlanNotFoundError."""

    def find_by_slug(self, slug: str) -> Plan | None: ...

    def list_all(self) -> list[Plan]:
        """Ordered by slug."""


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


class SessionRepository(Protocol):
    """Server-side sign-in sessions. Only the hash of a token is ever stored.

    A session is *active* when it is not revoked and has not passed its absolute
    expiry; idle expiry is decided by the caller from `last_seen_at`.
    """

    def add(self, session: Session) -> None:
        """Raises UserNotFoundError for an unknown user and AlreadyExistsError
        for a token hash that is already stored."""

    def find_by_token_hash(self, token_hash: str) -> Session | None: ...

    def touch(self, session_id: UUID, when: datetime) -> None:
        """Record activity. Unknown sessions are ignored."""

    def revoke(self, session_id: UUID, when: datetime) -> None:
        """Mark as revoked at `when`; an already revoked or unknown session is
        left untouched."""

    def revoke_all_for_user(self, user_id: UUID, when: datetime) -> int:
        """Revoke every session of the user that is not revoked yet; returns how
        many were revoked."""

    def revoke_oldest_beyond(self, user_id: UUID, keep: int, now: datetime) -> int:
        """Keep only the `keep` newest active sessions of the user and revoke the
        rest; returns how many were revoked."""

    def list_active_for_user(self, user_id: UUID, now: datetime) -> list[Session]:
        """Newest first."""

    def delete_finished_before(self, before: datetime, limit: int) -> int:
        """Delete at most `limit` sessions that expired or were revoked before
        `before`; returns how many were deleted."""


class LoginFailureRepository(Protocol):
    """Recent failed sign-ins, used to throttle repeated attempts."""

    def add(self, failure: LoginFailure) -> None: ...

    def recent(
        self,
        since: datetime,
        email_hash: str | None = None,
        client_key: str | None = None,
        limit: int = 1000,
    ) -> list[datetime]:
        """Times of the failures at or after `since`, oldest first, optionally
        filtered by e-mail hash and/or client key (both = that exact pair)."""

    def delete(self, email_hash: str, client_key: str) -> int:
        """Forget the failures of this exact pair; returns how many."""

    def delete_older_than(self, before: datetime, limit: int) -> int:
        """Delete at most `limit` failures older than `before`; returns how many."""


class UnitOfWork(Protocol):
    users: UserRepository
    plans: PlanRepository
    servers: GameServerRepository
    sessions: SessionRepository
    login_failures: LoginFailureRepository

    def __enter__(self) -> UnitOfWork: ...

    def __exit__(self, exc_type, exc, traceback) -> None:
        """Discards uncommitted changes. Never commits implicitly."""

    def commit(self) -> None: ...

    def rollback(self) -> None: ...


UnitOfWorkFactory = Callable[[], UnitOfWork]
