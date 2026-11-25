"""Sign-in, sessions and brute-force throttling.

Design notes (the reasons matter more than the code):

* Sessions are server-side. The browser holds a random 256-bit token; the
  database holds only its SHA-256 hash, so a leaked row cannot be replayed.
  A session can be revoked at any time, which a signed stand-alone token cannot.
* Everything shared lives in PostgreSQL (sessions and failed attempts), never in
  process memory, so any number of API processes behave consistently.
* A wrong password, an unknown e-mail, an account without a password and a
  disabled account are indistinguishable to the caller: the same error, and the
  same amount of password-hashing work.
* Throttling is checked before any hashing, so it cannot be used to burn CPU.
  Blocked attempts are not recorded, so they cannot extend a lock or grow the
  table. Three limits apply: per (account, client), per client and per account;
  the first keeps one attacker from locking a victim out from everywhere.
* Activity is written at most once per `touch_interval` per session, so
  authenticating a request is almost always a read.
* The CSRF token is derived from the session token with a one-way function: the
  server stores nothing extra, and a page that can read the CSRF token (but not
  the HttpOnly cookie) cannot recover the session token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import re
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from app.domain.auth import MAX_CLIENT_KEY_LENGTH, Actor, LoginFailure, Session
from app.domain.models import normalize_email
from app.domain.passwords import normalize_password
from app.errors import AuthenticationError, TooManyAttemptsError, UserNotFoundError

if TYPE_CHECKING:
    from uuid import UUID

    from app.domain.models import User
    from app.services.repositories import UnitOfWorkFactory
    from app.services.security import PasswordHasher

TOKEN_BYTES = 32
# secrets.token_urlsafe(32) is always 43 URL-safe base64 characters.
_TOKEN_PATTERN = re.compile(r"^[A-Za-z0-9_-]{43}$")
# Inputs longer than this are rejected before any hashing work.
MAX_EMAIL_INPUT = 1024
MAX_PASSWORD_INPUT = 1024
UNKNOWN_CLIENT = "unknown"

_CSRF_CONTEXT = b"game-hosting-csrf-v1:"
_DUMMY_PASSWORD = "password used only to equalise the work done for unknown accounts"


@dataclass(frozen=True)
class AuthSettings:
    session_lifetime: timedelta = timedelta(days=7)  # absolute limit
    idle_timeout: timedelta = timedelta(hours=8)
    touch_interval: timedelta = timedelta(seconds=60)
    max_sessions_per_user: int = 10
    throttle_window: timedelta = timedelta(minutes=15)
    max_failures_per_account_and_client: int = 5
    max_failures_per_client: int = 30
    max_failures_per_account: int = 20
    cleanup_batch: int = 100


@dataclass(frozen=True)
class SignedIn:
    session_token: str  # goes into the HttpOnly cookie; never stored or logged
    csrf_token: str  # readable by the page; must accompany state-changing requests
    user: User
    expires_at: datetime


@dataclass(frozen=True)
class AuthenticatedSession:
    actor: Actor
    user: User
    session_id: UUID
    csrf_token: str
    expires_at: datetime


def hash_token(token):
    """What the database stores in place of a session token."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_email(email):
    return hashlib.sha256(email.encode("utf-8")).hexdigest()


def csrf_token_for(session_token):
    digest = hashlib.sha256(_CSRF_CONTEXT + session_token.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def csrf_token_matches(session_token, supplied):
    """Constant-time check of the CSRF token a request carries."""
    if not isinstance(supplied, str) or not _TOKEN_PATTERN.match(session_token or ""):
        return False
    return hmac.compare_digest(
        csrf_token_for(session_token).encode("ascii"), supplied.encode("utf-8", "replace")
    )


def _utc_now():
    return datetime.now(UTC)


def _client_key(value):
    """A storable client key (normally an IP address); never raises."""
    if (
        isinstance(value, str)
        and 1 <= len(value) <= MAX_CLIENT_KEY_LENGTH
        and all(33 <= ord(char) < 127 for char in value)
    ):
        return value
    return UNKNOWN_CLIENT


class AuthService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        settings: AuthSettings | None = None,
        clock=None,
        token_factory=None,
    ):
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._settings = settings or AuthSettings()
        self._clock = clock or _utc_now
        self._new_token = token_factory or (lambda: secrets.token_urlsafe(TOKEN_BYTES))
        self._dummy_hash = None

    # --- sign in -------------------------------------------------------------------

    def sign_in(self, email, password, client_key):
        """Return a new session, or raise AuthenticationError / TooManyAttemptsError."""
        now = self._clock()
        client = _client_key(client_key)
        email_key = (
            normalize_email(email)
            if isinstance(email, str) and len(email) <= MAX_EMAIL_INPUT
            else None
        )
        email_hash = hash_email(email_key if email_key is not None else "")

        with self._uow_factory() as uow:
            retry_after = self._throttle_delay(uow, email_hash, client, now)
        if retry_after:
            raise TooManyAttemptsError(retry_after)

        if (
            email_key is None
            or not isinstance(password, str)
            or len(password) > MAX_PASSWORD_INPUT
        ):
            self._record_failure(email_hash, client, now)
            raise AuthenticationError()

        with self._uow_factory() as uow:
            user = uow.users.find_by_email(email_key)
        stored_hash = user.password_hash if user is not None else None

        # Always do one verification, against a decoy when there is nothing real to
        # check, so timing does not reveal whether the account exists.
        candidate = normalize_password(password)
        verified = self._hasher.verify(stored_hash or self._decoy_hash(), candidate)
        if not (verified and stored_hash is not None and user.is_active):
            self._record_failure(email_hash, client, now)
            raise AuthenticationError()

        upgraded_hash = (
            self._hasher.hash(candidate) if self._hasher.needs_rehash(stored_hash) else None
        )
        session_token = self._new_token()
        with self._uow_factory() as uow:
            # The account may have changed while the (slow) hashing ran.
            try:
                current = uow.users.get(user.id)
            except UserNotFoundError:
                raise AuthenticationError() from None
            if not current.is_active or current.password_hash != stored_hash:
                raise AuthenticationError()
            if upgraded_hash is not None:
                current = current.with_password_hash(upgraded_hash)
                uow.users.update(current)
            uow.login_failures.delete(email_hash, client)
            session = Session.new(
                current.id, hash_token(session_token), now, self._settings.session_lifetime
            )
            uow.sessions.add(session)
            uow.sessions.revoke_oldest_beyond(
                current.id, self._settings.max_sessions_per_user, now
            )
            self._clean_up(uow, now)
            uow.commit()
        return SignedIn(
            session_token=session_token,
            csrf_token=csrf_token_for(session_token),
            user=current,
            expires_at=session.expires_at,
        )

    # --- using a session -------------------------------------------------------------

    def authenticate(self, session_token):
        """The session behind a token, or AuthenticationError."""
        if not isinstance(session_token, str) or not _TOKEN_PATTERN.match(session_token):
            raise AuthenticationError()
        now = self._clock()
        with self._uow_factory() as uow:
            session = uow.sessions.find_by_token_hash(hash_token(session_token))
            if session is None or not session.is_valid_at(now, self._settings.idle_timeout):
                raise AuthenticationError()
            try:
                user = uow.users.get(session.user_id)
            except UserNotFoundError:
                raise AuthenticationError() from None
            if not user.is_active:
                raise AuthenticationError()
            if now - session.last_seen_at >= self._settings.touch_interval:
                uow.sessions.touch(session.id, now)
                uow.commit()
        return AuthenticatedSession(
            actor=Actor.from_user(user),
            user=user,
            session_id=session.id,
            csrf_token=csrf_token_for(session_token),
            expires_at=session.expires_at,
        )

    def sign_out(self, session_token):
        """Revoke the session. Never fails: an unknown or malformed token is
        already as signed out as it can be."""
        if not isinstance(session_token, str) or not _TOKEN_PATTERN.match(session_token):
            return
        now = self._clock()
        with self._uow_factory() as uow:
            session = uow.sessions.find_by_token_hash(hash_token(session_token))
            if session is not None:
                uow.sessions.revoke(session.id, now)
                uow.commit()

    # --- internals ----------------------------------------------------------------------

    def _decoy_hash(self):
        if self._dummy_hash is None:
            self._dummy_hash = self._hasher.hash(_DUMMY_PASSWORD)
        return self._dummy_hash

    def _throttle_delay(self, uow, email_hash, client, now):
        """Seconds to wait if any limit is exceeded, else 0."""
        settings = self._settings
        window = settings.throttle_window
        since = now - window
        waits = []
        for filters, limit in (
            ({"email_hash": email_hash, "client_key": client}, settings.max_failures_per_account_and_client),
            ({"client_key": client}, settings.max_failures_per_client),
            ({"email_hash": email_hash}, settings.max_failures_per_account),
        ):
            # A failure stops counting at the very instant its window is over, which
            # is exactly when the wait announced below ends (the repository's
            # `since` is inclusive, so drop the boundary here).
            times = [
                when
                for when in uow.login_failures.recent(since, limit=limit * 2, **filters)
                if when > since
            ]
            if len(times) >= limit:
                # The block ends when enough old failures have left the window.
                frees_at = times[len(times) - limit] + window
                waits.append(max(1, math.ceil((frees_at - now).total_seconds())))
        return max(waits) if waits else 0

    def _record_failure(self, email_hash, client, now):
        with self._uow_factory() as uow:
            uow.login_failures.add(LoginFailure.new(email_hash, client, now))
            self._clean_up(uow, now)
            uow.commit()

    def _clean_up(self, uow, now):
        """Opportunistic, bounded housekeeping, so no scheduler is needed."""
        batch = self._settings.cleanup_batch
        uow.login_failures.delete_older_than(now - self._settings.throttle_window, batch)
        uow.sessions.delete_finished_before(now - timedelta(days=1), batch)
