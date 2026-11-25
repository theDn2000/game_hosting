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

from sqlalchemy import delete, func, insert, select, update
from sqlalchemy.exc import IntegrityError, InterfaceError, OperationalError

from app.domain.auth import Session
from app.domain.models import Game, GameServer, Plan, Role, User, normalize_email
from app.errors import (
    AlreadyExistsError,
    InvalidDataError,
    PlanNotFoundError,
    ServerNotFoundError,
    UserNotFoundError,
)
from app.infrastructure.db.database import translate_connection_errors
from app.infrastructure.db.models import (
    GameServerRow,
    LoginFailureRow,
    PlanRow,
    SessionRow,
    UserRow,
)

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
    "pk_sessions": ("session", "id"),
    "uq_sessions_token_hash": ("session", "token"),
    "pk_login_failures": ("login failure", "id"),
}


def _translate_integrity_error(exc, owner_id=None, plan_id=None):
    """The application error for a constraint violation, or None if unknown."""
    orig = exc.orig
    sqlstate = getattr(orig, "sqlstate", None)
    constraint = getattr(getattr(orig, "diag", None), "constraint_name", None)
    if sqlstate == _UNIQUE_VIOLATION and constraint in _UNIQUE_CONSTRAINTS:
        return AlreadyExistsError(*_UNIQUE_CONSTRAINTS[constraint])
    if sqlstate == _FOREIGN_KEY_VIOLATION:
        if constraint in ("fk_game_servers_owner_id_users", "fk_sessions_user_id_users"):
            return UserNotFoundError(owner_id)
        if constraint == "fk_game_servers_plan_id_plans":
            return PlanNotFoundError(plan_id)
    if sqlstate == _CHECK_VIOLATION:
        return InvalidDataError(f"The database rejected the data (constraint {constraint}).")
    return None


def _to_user(row):
    try:
        role = Role(row.role)
    except ValueError:
        raise InvalidDataError(f"Stored user {row.id} has an unknown role.") from None
    return User(
        id=row.id,
        email=row.email,
        created_at=row.created_at,
        role=role,
        password_hash=row.password_hash,
        is_active=row.is_active,
    )


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


def _to_session(row):
    return Session(
        id=row.id,
        user_id=row.user_id,
        token_hash=row.token_hash,
        created_at=row.created_at,
        last_seen_at=row.last_seen_at,
        expires_at=row.expires_at,
        revoked_at=row.revoked_at,
    )


class _SqlRepository:
    def __init__(self, session):
        self._session = session

    @contextmanager
    def _database_errors(self):
        with translate_connection_errors(self._session.get_bind()):
            yield

    def _write(self, statement, owner_id=None, plan_id=None):
        """Run an INSERT or UPDATE in a SAVEPOINT, translating constraint errors."""
        with self._database_errors():
            try:
                with self._session.begin_nested():
                    return self._session.execute(statement)
            except IntegrityError as exc:
                translated = _translate_integrity_error(exc, owner_id, plan_id)
                if translated is None:
                    raise
                raise translated from exc

    def _insert(self, table_row, values, owner_id=None, plan_id=None):
        self._write(insert(table_row).values(**values), owner_id, plan_id)


class SqlUserRepository(_SqlRepository):
    def add(self, user):
        self._insert(
            UserRow,
            dict(
                id=user.id,
                email=user.email,
                created_at=user.created_at,
                role=user.role.value,
                password_hash=user.password_hash,
                is_active=user.is_active,
            ),
        )

    def update(self, user):
        result = self._write(
            update(UserRow)
            .where(UserRow.id == user.id)
            .values(
                email=user.email,
                role=user.role.value,
                password_hash=user.password_hash,
                is_active=user.is_active,
            )
        )
        if result.rowcount == 0:
            raise UserNotFoundError(user.id)

    def list_all(self):
        with self._database_errors():
            rows = self._session.execute(select(UserRow).order_by(UserRow.email)).scalars()
            return [_to_user(row) for row in rows]

    def get_many(self, user_ids):
        ids = set(user_ids)
        if not ids:
            return {}
        with self._database_errors():
            rows = self._session.execute(select(UserRow).where(UserRow.id.in_(ids))).scalars()
            return {row.id: _to_user(row) for row in rows}

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

    def list_all(self):
        with self._database_errors():
            rows = self._session.execute(select(PlanRow).order_by(PlanRow.slug)).scalars()
            return [_to_plan(row) for row in rows]

    def get_many(self, plan_ids):
        ids = set(plan_ids)
        if not ids:
            return {}
        with self._database_errors():
            rows = self._session.execute(select(PlanRow).where(PlanRow.id.in_(ids))).scalars()
            return {row.id: _to_plan(row) for row in rows}


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

    def list_page(self, owner_id, limit, offset):
        statement = select(GameServerRow)
        if owner_id is not None:
            statement = statement.where(GameServerRow.owner_id == owner_id)
        return self._list(statement, limit=limit, offset=offset)

    def count(self, owner_id):
        statement = select(func.count()).select_from(GameServerRow)
        if owner_id is not None:
            statement = statement.where(GameServerRow.owner_id == owner_id)
        with self._database_errors():
            return self._session.execute(statement).scalar_one()

    def _list(self, statement, limit=None, offset=0):
        statement = statement.order_by(GameServerRow.created_at, GameServerRow.id)
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        with self._database_errors():
            rows = self._session.execute(statement).scalars()
            return [_to_server(row) for row in rows]


class SqlSessionRepository(_SqlRepository):
    def add(self, session):
        self._insert(
            SessionRow,
            dict(
                id=session.id,
                user_id=session.user_id,
                token_hash=session.token_hash,
                created_at=session.created_at,
                last_seen_at=session.last_seen_at,
                expires_at=session.expires_at,
                revoked_at=session.revoked_at,
            ),
            owner_id=session.user_id,
        )

    def find_by_token_hash(self, token_hash):
        with self._database_errors():
            row = self._session.execute(
                select(SessionRow).where(SessionRow.token_hash == token_hash)
            ).scalar_one_or_none()
        return _to_session(row) if row else None

    def touch(self, session_id, when):
        with self._database_errors():
            self._session.execute(
                update(SessionRow).where(SessionRow.id == session_id).values(last_seen_at=when)
            )

    def revoke(self, session_id, when):
        with self._database_errors():
            self._session.execute(
                update(SessionRow)
                .where(SessionRow.id == session_id, SessionRow.revoked_at.is_(None))
                .values(revoked_at=when)
            )

    def revoke_all_for_user(self, user_id, when):
        with self._database_errors():
            result = self._session.execute(
                update(SessionRow)
                .where(SessionRow.user_id == user_id, SessionRow.revoked_at.is_(None))
                .values(revoked_at=when)
            )
            return result.rowcount

    def list_active_for_user(self, user_id, now):
        with self._database_errors():
            rows = self._session.execute(self._active_statement(user_id, now)).scalars()
            return [_to_session(row) for row in rows]

    def revoke_oldest_beyond(self, user_id, keep, now):
        excess = (
            select(SessionRow.id)
            .where(*self._active_conditions(user_id, now))
            .order_by(SessionRow.created_at.desc(), SessionRow.id.desc())
            .offset(keep)
        )
        with self._database_errors():
            result = self._session.execute(
                update(SessionRow)
                .where(SessionRow.id.in_(excess))
                .values(revoked_at=now)
                .execution_options(synchronize_session=False)
            )
            return result.rowcount

    def delete_finished_before(self, before, limit):
        doomed = (
            select(SessionRow.id)
            .where((SessionRow.expires_at < before) | (SessionRow.revoked_at < before))
            .order_by(SessionRow.created_at, SessionRow.id)
            .limit(limit)
        )
        with self._database_errors():
            result = self._session.execute(
                delete(SessionRow)
                .where(SessionRow.id.in_(doomed))
                .execution_options(synchronize_session=False)
            )
            return result.rowcount

    @staticmethod
    def _active_conditions(user_id, now):
        return (
            SessionRow.user_id == user_id,
            SessionRow.revoked_at.is_(None),
            SessionRow.expires_at > now,
        )

    def _active_statement(self, user_id, now):
        return (
            select(SessionRow)
            .where(*self._active_conditions(user_id, now))
            .order_by(SessionRow.created_at.desc(), SessionRow.id.desc())
        )


class SqlLoginFailureRepository(_SqlRepository):
    def add(self, failure):
        self._insert(
            LoginFailureRow,
            dict(
                id=failure.id,
                email_hash=failure.email_hash,
                client_key=failure.client_key,
                occurred_at=failure.occurred_at,
            ),
        )

    def recent(self, since, email_hash=None, client_key=None, limit=1000):
        statement = select(LoginFailureRow.occurred_at).where(LoginFailureRow.occurred_at >= since)
        if email_hash is not None:
            statement = statement.where(LoginFailureRow.email_hash == email_hash)
        if client_key is not None:
            statement = statement.where(LoginFailureRow.client_key == client_key)
        with self._database_errors():
            return list(
                self._session.execute(
                    statement.order_by(LoginFailureRow.occurred_at).limit(limit)
                ).scalars()
            )

    def delete(self, email_hash, client_key):
        with self._database_errors():
            result = self._session.execute(
                delete(LoginFailureRow)
                .where(
                    LoginFailureRow.email_hash == email_hash,
                    LoginFailureRow.client_key == client_key,
                )
                .execution_options(synchronize_session=False)
            )
            return result.rowcount

    def delete_older_than(self, before, limit):
        doomed = (
            select(LoginFailureRow.id)
            .where(LoginFailureRow.occurred_at < before)
            .order_by(LoginFailureRow.occurred_at, LoginFailureRow.id)
            .limit(limit)
        )
        with self._database_errors():
            result = self._session.execute(
                delete(LoginFailureRow)
                .where(LoginFailureRow.id.in_(doomed))
                .execution_options(synchronize_session=False)
            )
            return result.rowcount


class SqlUnitOfWork:
    """One transaction. Changes are permanent only after `commit()`."""

    def __init__(self, session_factory):
        self._session_factory = session_factory

    def __enter__(self):
        self._session = self._session_factory()
        self.users = SqlUserRepository(self._session)
        self.plans = SqlPlanRepository(self._session)
        self.servers = SqlGameServerRepository(self._session)
        self.sessions = SqlSessionRepository(self._session)
        self.login_failures = SqlLoginFailureRepository(self._session)
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
