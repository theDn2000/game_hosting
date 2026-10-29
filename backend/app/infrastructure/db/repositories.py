"""PostgreSQL implementation of the persistence interfaces.

Implements `app.services.repositories` with SQLAlchemy. Rows are converted to
and from the domain dataclasses; nothing SQLAlchemy-specific leaves this
package. Database failures become application errors:

* connection problems -> `DatabaseUnavailableError`
* unique violations   -> `AlreadyExistsError`
* missing owner/plan  -> `UserNotFoundError` / `PlanNotFoundError`
* CHECK violations    -> `InvalidDataError` (the domain should have caught it)

Every `add` runs in a SAVEPOINT, so a rejected insert does not poison the
surrounding transaction: the unit of work can carry on, exactly like the
in-memory reference implementation.
"""

from contextlib import contextmanager

from sqlalchemy import insert, select
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.domain.models import Game, GameServer, Plan, User, normalize_email
from app.errors import (
    AlreadyExistsError,
    InvalidDataError,
    PlanNotFoundError,
    ServerNotFoundError,
    UserNotFoundError,
)
from app.infrastructure.db.database import translate_connection_errors
from app.infrastructure.db.models import GameServerRow, PlanRow, UserRow

# SQLSTATE codes reported by PostgreSQL.
_UNIQUE_VIOLATION = "23505"
_FOREIGN_KEY_VIOLATION = "23503"
_CHECK_VIOLATION = "23514"

_UNIQUE_CONSTRAINTS = {
    "pk_users": ("user", "id"),
    "uq_users_email": ("user", "email"),
    "pk_plans": ("plan", "id"),
    "uq_plans_slug": ("plan", "slug"),
    "pk_game_servers": ("server", "id"),
    "uq_game_servers_container_name": ("server", "container name"),
}


def _translate_integrity_error(exc, owner_id=None, plan_id=None):
    """The application error for a constraint violation, or None if unknown."""
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if sqlstate == _UNIQUE_VIOLATION and constraint in _UNIQUE_CONSTRAINTS:
        return AlreadyExistsError(*_UNIQUE_CONSTRAINTS[constraint])
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        if constraint == "fk_game_servers_owner_id_users":
            return UserNotFoundError(owner_id)
        if constraint == "fk_game_servers_plan_id_plans":
            return PlanNotFoundError(plan_id)
    if sqlstate == _CHECK_VIOLATION:
        return InvalidDataError(f"The database rejected the data (constraint {constraint}).")
    return None


def _to_user(row):
    return User(id=row.id, email=row.email, created_at=row.created_at)


def _to_plan(row):
    return Plan(
        id=row.id,
        slug=row.slug,
        name=row.name,
        memory_mb=row.memory_mb,
        cpu_millicores=row.cpu_millicores,
    )


def _to_server(row):
    try:
        game = Game(row.game)
    except ValueError:
        raise InvalidDataError(f"Stored server {row.id} has an unknown game.") from None
    return GameServer(
        id=row.id,
        owner_id=row.owner_id,
        plan_id=row.plan_id,
        game=game,
        name=row.name,
        container_name=row.container_name,
        created_at=row.created_at,
    )


class _SqlRepository:
    def __init__(self, session):
        self._session = session

    @contextmanager
    def _database_errors(self):
        with translate_connection_errors(self._session.get_bind()):
            yield

    def _insert(self, table_row, values, owner_id=None, plan_id=None):
        with self._database_errors():
            try:
                with self._session.begin_nested():
                    self._session.execute(insert(table_row).values(**values))
            except IntegrityError as exc:
                translated = _translate_integrity_error(exc, owner_id, plan_id)
                if translated is None:
                    raise
                raise translated from exc


class SqlUserRepository(_SqlRepository):
    def add(self, user):
        self._insert(
            UserRow, dict(id=user.id, email=user.email, created_at=user.created_at)
        )

    def get(self, user_id):
        with self._database_errors():
            row = self._session.get(UserRow, user_id)
        if row is None:
            raise UserNotFoundError(user_id)
        return _to_user(row)

    def find_by_email(self, email):
        with self._database_errors():
            row = self._session.execute(
                select(UserRow).where(UserRow.email == normalize_email(email))
            ).scalar_one_or_none()
        return _to_user(row) if row else None


class SqlPlanRepository(_SqlRepository):
    def add(self, plan):
        self._insert(
            PlanRow,
            dict(
                id=plan.id,
                slug=plan.slug,
                name=plan.name,
                memory_mb=plan.memory_mb,
                cpu_millicores=plan.cpu_millicores,
            ),
        )

    def get(self, plan_id):
        with self._database_errors():
            row = self._session.get(PlanRow, plan_id)
        if row is None:
            raise PlanNotFoundError(plan_id)
        return _to_plan(row)

    def find_by_slug(self, slug):
        with self._database_errors():
            row = self._session.execute(
                select(PlanRow).where(PlanRow.slug == slug)
            ).scalar_one_or_none()
        return _to_plan(row) if row else None


class SqlGameServerRepository(_SqlRepository):
    def add(self, server):
        self._insert(
            GameServerRow,
            dict(
                id=server.id,
                owner_id=server.owner_id,
                plan_id=server.plan_id,
                game=server.game.value,
                name=server.name,
                container_name=server.container_name,
                created_at=server.created_at,
            ),
            owner_id=server.owner_id,
            plan_id=server.plan_id,
        )

    def get(self, server_id):
        with self._database_errors():
            row = self._session.get(GameServerRow, server_id)
        if row is None:
            raise ServerNotFoundError(server_id)
        return _to_server(row)

    def find_by_container_name(self, container_name):
        with self._database_errors():
            row = self._session.execute(
                select(GameServerRow).where(GameServerRow.container_name == container_name)
            ).scalar_one_or_none()
        return _to_server(row) if row else None

    def list_by_owner(self, owner_id):
        return self._list(select(GameServerRow).where(GameServerRow.owner_id == owner_id))

    def list_all(self):
        return self._list(select(GameServerRow))

    def _list(self, statement):
        with self._database_errors():
            rows = self._session.execute(
                statement.order_by(GameServerRow.created_at, GameServerRow.id)
            ).scalars()
            return [_to_server(row) for row in rows]


class SqlUnitOfWork:
    """One transaction. Changes are permanent only after `commit()`."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def __enter__(self):
        self._session = self._session_factory()
        self.users = SqlUserRepository(self._session)
        self.plans = SqlPlanRepository(self._session)
        self.servers = SqlGameServerRepository(self._session)
        return self

    def __exit__(self, exc_type, exc, traceback):
        # Never commits implicitly. If the connection is already gone there is
        # nothing left to roll back, and raising here would hide the original
        # exception.
        try:
            self._session.rollback()
        except (OperationalError, InterfaceError):
            pass
        finally:
            self._session.close()

    def commit(self):
        with translate_connection_errors(self._session.get_bind()):
            self._session.commit()

    def rollback(self):
        with translate_connection_errors(self._session.get_bind()):
            self._session.rollback()
