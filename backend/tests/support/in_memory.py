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
        self.sessions = {}
        self.login_failures = {}


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

    def update(self, user):
        if user.id not in self._users:
            raise UserNotFoundError(user.id)
        if any(u.email == user.email and u.id != user.id for u in self._users.values()):
            raise AlreadyExistsError("user", "email")
        self._users[user.id] = user

    def list_all(self):
        return sorted(self._users.values(), key=lambda user: user.email)

    def get_many(self, user_ids):
        return {i: self._users[i] for i in set(user_ids) if i in self._users}


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

    def list_all(self):
        return sorted(self._plans.values(), key=lambda plan: plan.slug)

    def get_many(self, plan_ids):
        return {i: self._plans[i] for i in set(plan_ids) if i in self._plans}


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

    def list_page(self, owner_id, limit, offset):
        chosen = (s for s in self._servers.values() if owner_id is None or s.owner_id == owner_id)
        return _ordered(chosen)[offset : offset + limit]

    def count(self, owner_id):
        return sum(1 for s in self._servers.values() if owner_id is None or s.owner_id == owner_id)


class _Sessions:
    def __init__(self, sessions, users):
        self._sessions = sessions
        self._users = users

    def add(self, session):
        if session.user_id not in self._users:
            raise UserNotFoundError(session.user_id)
        if session.id in self._sessions or any(
            s.token_hash == session.token_hash for s in self._sessions.values()
        ):
            raise AlreadyExistsError("session", "token")
        self._sessions[session.id] = session

    def find_by_token_hash(self, token_hash):
        return next((s for s in self._sessions.values() if s.token_hash == token_hash), None)

    def touch(self, session_id, when):
        session = self._sessions.get(session_id)
        if session is not None:
            self._sessions[session_id] = session.with_last_seen(when)

    def revoke(self, session_id, when):
        session = self._sessions.get(session_id)
        if session is not None and session.revoked_at is None:
            self._sessions[session_id] = session.revoked(when)

    def revoke_all_for_user(self, user_id, when):
        count = 0
        for session_id, session in list(self._sessions.items()):
            if session.user_id == user_id and session.revoked_at is None:
                self._sessions[session_id] = session.revoked(when)
                count += 1
        return count

    def list_active_for_user(self, user_id, now):
        active = [
            s for s in self._sessions.values()
            if s.user_id == user_id and s.revoked_at is None and s.expires_at > now
        ]
        return sorted(active, key=lambda s: (s.created_at, s.id), reverse=True)

    def revoke_oldest_beyond(self, user_id, keep, now):
        excess = self.list_active_for_user(user_id, now)[keep:]
        for session in excess:
            self._sessions[session.id] = session.revoked(now)
        return len(excess)

    def delete_finished_before(self, before, limit):
        finished = [
            s for s in self._sessions.values()
            if s.expires_at < before or (s.revoked_at is not None and s.revoked_at < before)
        ]
        finished.sort(key=lambda s: (s.created_at, s.id))
        for session in finished[:limit]:
            del self._sessions[session.id]
        return min(len(finished), limit)


class _LoginFailures:
    def __init__(self, failures):
        self._failures = failures

    def add(self, failure):
        self._failures[failure.id] = failure

    def recent(self, since, email_hash=None, client_key=None, limit=1000):
        times = sorted(
            f.occurred_at for f in self._failures.values()
            if f.occurred_at >= since
            and (email_hash is None or f.email_hash == email_hash)
            and (client_key is None or f.client_key == client_key)
        )
        return times[:limit]

    def delete(self, email_hash, client_key):
        doomed = [
            f.id for f in self._failures.values()
            if f.email_hash == email_hash and f.client_key == client_key
        ]
        for failure_id in doomed:
            del self._failures[failure_id]
        return len(doomed)

    def delete_older_than(self, before, limit):
        old = sorted(
            (f for f in self._failures.values() if f.occurred_at < before),
            key=lambda f: (f.occurred_at, f.id),
        )[:limit]
        for failure in old:
            del self._failures[failure.id]
        return len(old)


class InMemoryUnitOfWork:
    def __init__(self, store):
        self._store = store

    def __enter__(self):
        self._users = dict(self._store.users)
        self._plans = dict(self._store.plans)
        self._servers = dict(self._store.servers)
        self._sessions = dict(self._store.sessions)
        self._login_failures = dict(self._store.login_failures)
        self.users = _Users(self._users)
        self.plans = _Plans(self._plans)
        self.servers = _Servers(self._servers, self._users, self._plans)
        self.sessions = _Sessions(self._sessions, self._users)
        self.login_failures = _LoginFailures(self._login_failures)
        return self

    def __exit__(self, exc_type, exc, traceback):
        # Never commits implicitly: whatever was not committed is dropped.
        self._users = self._plans = self._servers = None
        self._sessions = self._login_failures = None

    def _working_copies(self):
        return {
            "users": self._users,
            "plans": self._plans,
            "servers": self._servers,
            "sessions": self._sessions,
            "login_failures": self._login_failures,
        }

    def commit(self):
        for name, working in self._working_copies().items():
            setattr(self._store, name, dict(working))

    def rollback(self):
        for name, working in self._working_copies().items():
            working.clear()
            working.update(getattr(self._store, name))
