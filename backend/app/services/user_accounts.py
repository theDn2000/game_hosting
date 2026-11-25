"""User accounts: creating them and managing their credentials and status.

Used by the operator CLI today (accounts are created by the operator; open
sign-up arrives with billing, Phase 7). Passwords go through the policy in
`app.domain.passwords` and are stored only as hashes. Hashing is slow on
purpose, so it always happens before a database transaction is opened.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.domain.models import Role, User
from app.domain.passwords import validate_password
from app.errors import UserNotFoundError

if TYPE_CHECKING:
    from app.services.repositories import UnitOfWorkFactory
    from app.services.security import PasswordHasher


class UserAccounts:
    def __init__(self, uow_factory: UnitOfWorkFactory, password_hasher: PasswordHasher):
        self._uow_factory = uow_factory
        self._hasher = password_hasher

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
        new_hash = self._hash(password)
        return self._change(email, lambda user: user.with_password_hash(new_hash))

    def set_role(self, email, role):
        return self._change(email, lambda user: user.with_role(role))

    def set_active(self, email, is_active):
        return self._change(email, lambda user: user.with_active(is_active))

    def list_users(self):
        with self._uow_factory() as uow:
            return uow.users.list_all()

    def _hash(self, password):
        return self._hasher.hash(validate_password(password))

    def _change(self, email, change):
        with self._uow_factory() as uow:
            user = uow.users.find_by_email(email)
            if user is None:
                raise UserNotFoundError(email)
            changed = change(user)
            uow.users.update(changed)
            uow.commit()
        return changed
