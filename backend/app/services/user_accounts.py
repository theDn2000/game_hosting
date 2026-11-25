"""User accounts: creating them and managing their credentials and status.

Used by the operator CLI today (accounts are created by the operator; open
sign-up arrives with billing, Phase 7). Passwords go through the policy in
`app.domain.passwords` and are stored only as hashes. Hashing is slow on
purpose, so it always happens before a database transaction is opened.

Anything that should stop an existing session from working (a new password, a
disabled account) revokes that user's sessions in the same transaction, so a
stolen session does not survive a password change.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from app.domain.models import Role, User
from app.domain.passwords import validate_password
from app.errors import UserNotFoundError

if TYPE_CHECKING:
    from app.services.repositories import UnitOfWorkFactory
    from app.services.security import PasswordHasher


def _utc_now():
    return datetime.now(UTC)


class UserAccounts:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        password_hasher: PasswordHasher,
        clock=None,
    ):
        self._uow_factory = uow_factory
        self._hasher = password_hasher
        self._clock = clock or _utc_now

    def create_user(self, email, role=Role.USER, password=None):
        """Create an account. Without a password it cannot sign in until one is
        set. Raises InvalidDataError (e-mail, role or password) or
        AlreadyExistsError, and stores nothing in either case."""
        user = User.new(email, role=role)  # validates before the slow hashing
        if password is not None:
            user = user.with_password_hash(self._hash(password))
        with self._uow_factory() as uow:
            uow.users.add(user)
            uow.commit()
        return user

    def set_password(self, email, password):
        """Replace the password and sign the user out everywhere."""
        new_hash = self._hash(password)
        return self._change(
            email, lambda user: user.with_password_hash(new_hash), revoke_sessions=True
        )

    def set_role(self, email, role):
        # No need to revoke sessions: the role is read from the database on every request.
        return self._change(email, lambda user: user.with_role(role))

    def set_active(self, email, is_active):
        """Disabling an account also signs the user out everywhere."""
        return self._change(
            email, lambda user: user.with_active(is_active), revoke_sessions=not is_active
        )

    def revoke_sessions(self, email):
        """Sign the user out everywhere; returns how many sessions were ended."""
        with self._uow_factory() as uow:
            user = uow.users.find_by_email(email)
            if user is None:
                raise UserNotFoundError(email)
            count = uow.sessions.revoke_all_for_user(user.id, self._clock())
            uow.commit()
        return count

    def list_users(self):
        with self._uow_factory() as uow:
            return uow.users.list_all()

    def _hash(self, password):
        return self._hasher.hash(validate_password(password))

    def _change(self, email, change, revoke_sessions=False):
        with self._uow_factory() as uow:
            user = uow.users.find_by_email(email)
            if user is None:
                raise UserNotFoundError(email)
            changed = change(user)
            uow.users.update(changed)
            if revoke_sessions:
                uow.sessions.revoke_all_for_user(user.id, self._clock())
            uow.commit()
        return changed
