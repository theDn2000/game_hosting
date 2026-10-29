"""In-memory repositories and unit of work, for tests.

They follow the same contract as the PostgreSQL implementation (step 3 of
Phase 3) and are checked by the same contract tests. Entities are immutable,
so sharing them between the store and a unit of work is safe.
"""

from app.domain.models import normalize_email
from app.errors import (
    AlreadyExistsError,
    PlanNotFoundError,
    ServerNotFoundError,
    UserNotFoundError,
)


def _ordered(servers):
    return sorted(servers, key=lambda server: (server.created_at, server.id))


class InMemoryStore:
    """The committed state shared by all units of work of a test."""

    def __init__(self):
        self.users = {}
        self.plans = {}
        self.servers = {}


class _Users:
    def __init__(self, users):
        self._users = users

    def add(self, user):
        if user.id in self._users:
            raise AlreadyExistsError("user", "id")
        if any(existing.email == user.email for existing in self._users.values()):
            raise AlreadyExistsError("user", "email")
        self._users[user.id] = user

    def get(self, user_id):
        try:
            return self._users[user_id]
        except KeyError:
            raise UserNotFoundError(user_id) from None

    def find_by_email(self, email):
        email = normalize_email(email)
        return next((u for u in self._users.values() if u.email == email), None)


class _Plans:
    def __init__(self, plans):
        self._plans = plans

    def add(self, plan):
        if plan.id in self._plans:
            raise AlreadyExistsError("plan", "id")
        if any(existing.slug == plan.slug for existing in self._plans.values()):
            raise AlreadyExistsError("plan", "slug")
        self._plans[plan.id] = plan

    def get(self, plan_id):
        try:
            return self._plans[plan_id]
        except KeyError:
            raise PlanNotFoundError(plan_id) from None

    def find_by_slug(self, slug):
        return next((p for p in self._plans.values() if p.slug == slug), None)


class _Servers:
    def __init__(self, servers, users, plans):
        self._servers = servers
        self._users = users
        self._plans = plans

    def add(self, server):
        if server.owner_id not in self._users:
            raise UserNotFoundError(server.owner_id)
        if server.plan_id not in self._plans:
            raise PlanNotFoundError(server.plan_id)
        if server.id in self._servers:
            raise AlreadyExistsError("server", "id")
        if any(s.container_name == server.container_name for s in self._servers.values()):
            raise AlreadyExistsError("server", "container name")
        self._servers[server.id] = server

    def get(self, server_id):
        try:
            return self._servers[server_id]
        except KeyError:
            raise ServerNotFoundError(server_id) from None

    def find_by_container_name(self, container_name):
        return next(
            (s for s in self._servers.values() if s.container_name == container_name),
            None,
        )

    def list_by_owner(self, owner_id):
        return _ordered(s for s in self._servers.values() if s.owner_id == owner_id)

    def list_all(self):
        return _ordered(self._servers.values())


class InMemoryUnitOfWork:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        self._users = dict(self._store.users)
        self._plans = dict(self._store.plans)
        self._servers = dict(self._store.servers)
        self.users = _Users(self._users)
        self.plans = _Plans(self._plans)
        self.servers = _Servers(self._servers, self._users, self._plans)
        return self

    def __exit__(self, exc_type, exc, traceback):
        # Never commits implicitly: whatever was not committed is dropped.
        self._users = self._plans = self._servers = None

    def commit(self):
        self._store.users = dict(self._users)
        self._store.plans = dict(self._plans)
        self._store.servers = dict(self._servers)

    def rollback(self):
        self._users.clear()
        self._users.update(self._store.users)
        self._plans.clear()
        self._plans.update(self._store.plans)
        self._servers.clear()
        self._servers.update(self._store.servers)
