"""Authentication-related domain objects: who is acting, sessions, failed sign-ins.

Like the other domain objects they are immutable and validate themselves on
construction. Secrets never live here: a `Session` holds only the SHA-256
hash of its token, so a leaked database row cannot be used to impersonate
anyone, and a `LoginFailure` holds a hash of the e-mail that was tried.
"""

import re
import uuid
from dataclasses import dataclass, replace
from dataclasses import field as dataclass_field
from datetime import datetime, timedelta

from app.domain.models import Role, _require_aware_datetime, _require_uuid
from app.errors import InvalidDataError

MAX_CLIENT_KEY_LENGTH = 64

_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _require_sha256_hex(value, field):
    if not isinstance(value, str) or not _SHA256_HEX.match(value):
        raise InvalidDataError(f"{field} must be a lowercase SHA-256 hex digest.")


@dataclass(frozen=True, slots=True)
class Actor:
    """Who is performing an operation. Authorization decisions are made on this,
    never on anything the client claims."""

    user_id: uuid.UUID
    role: Role

    def __post_init__(self):
        _require_uuid(self.user_id, "user_id")
        if not isinstance(self.role, Role):
            raise InvalidDataError("role must be a valid role.")

    @property
    def is_admin(self):
        return self.role is Role.ADMIN

    @classmethod
    def from_user(cls, user):
        return cls(user_id=user.id, role=user.role)


# The local operator (the `cli.py` commands run by someone with access to the
# host). It is not a database user and can never sign in.
OPERATOR = Actor(user_id=uuid.UUID(int=0), role=Role.ADMIN)


@dataclass(frozen=True, slots=True)
class Session:
    id: uuid.UUID
    user_id: uuid.UUID
    token_hash: str = dataclass_field(repr=False)
    created_at: datetime
    last_seen_at: datetime
    expires_at: datetime
    revoked_at: datetime | None = None

    def __post_init__(self):
        _require_uuid(self.id, "id")
        _require_uuid(self.user_id, "user_id")
        _require_sha256_hex(self.token_hash, "token_hash")
        _require_aware_datetime(self.created_at, "created_at")
        _require_aware_datetime(self.last_seen_at, "last_seen_at")
        _require_aware_datetime(self.expires_at, "expires_at")
        if self.revoked_at is not None:
            _require_aware_datetime(self.revoked_at, "revoked_at")
        if self.last_seen_at < self.created_at:
            raise InvalidDataError("last_seen_at cannot be before created_at.")
        if self.expires_at <= self.created_at:
            raise InvalidDataError("expires_at must be after created_at.")

    @classmethod
    def new(cls, user_id, token_hash, now, lifetime):
        if not isinstance(lifetime, timedelta) or lifetime <= timedelta(0):
            raise InvalidDataError("lifetime must be a positive duration.")
        return cls(
            id=uuid.uuid4(),
            user_id=user_id,
            token_hash=token_hash,
            created_at=now,
            last_seen_at=now,
            expires_at=now + lifetime,
        )

    def is_valid_at(self, now, idle_timeout):
        """Not revoked, not past its absolute expiry and not idle for too long."""
        return (
            self.revoked_at is None
            and now < self.expires_at
            and now - self.last_seen_at < idle_timeout
        )

    def with_last_seen(self, when):
        return replace(self, last_seen_at=when)

    def revoked(self, when):
        return replace(self, revoked_at=when)


@dataclass(frozen=True, slots=True)
class LoginFailure:
    """One failed sign-in, kept briefly to throttle repeated attempts.

    `email_hash` is the SHA-256 of the normalised e-mail that was tried (whether
    or not an account exists, so throttling reveals nothing about accounts) and
    `client_key` identifies the caller, normally its IP address.
    """

    id: uuid.UUID
    email_hash: str
    client_key: str
    occurred_at: datetime

    def __post_init__(self):
        _require_uuid(self.id, "id")
        _require_sha256_hex(self.email_hash, "email_hash")
        if (
            not isinstance(self.client_key, str)
            or not 1 <= len(self.client_key) <= MAX_CLIENT_KEY_LENGTH
            or not all(33 <= ord(char) < 127 for char in self.client_key)
        ):
            raise InvalidDataError(
                f"client_key must be 1 to {MAX_CLIENT_KEY_LENGTH} printable ASCII characters."
            )
        _require_aware_datetime(self.occurred_at, "occurred_at")

    @classmethod
    def new(cls, email_hash, client_key, at):
        return cls(
            id=uuid.uuid4(), email_hash=email_hash, client_key=client_key, occurred_at=at
        )
