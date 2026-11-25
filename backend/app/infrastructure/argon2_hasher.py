"""Argon2id implementation of the PasswordHasher port (argon2-cffi).

The library's defaults are the RFC 9106 "low memory" profile (Argon2id, 64 MiB,
3 passes, 4 lanes, a random 16-byte salt), which takes a few tens of
milliseconds. When the parameters are raised in the future, `needs_rehash`
tells the sign-in flow to upgrade stored hashes transparently.
"""

from argon2 import PasswordHasher as _Argon2
from argon2.exceptions import InvalidHashError, VerificationError


class Argon2PasswordHasher:
    def __init__(self, hasher=None):
        self._hasher = hasher or _Argon2()

    def hash(self, password):
        return self._hasher.hash(password)

    def verify(self, password_hash, password):
        try:
            return self._hasher.verify(password_hash, password)
        except (VerificationError, InvalidHashError):
            # VerifyMismatchError is a VerificationError. A hash that is not
            # Argon2 at all is not a match either.
            return False

    def needs_rehash(self, password_hash):
        try:
            return self._hasher.check_needs_rehash(password_hash)
        except InvalidHashError:
            return True
