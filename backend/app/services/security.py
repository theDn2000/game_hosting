"""Security interfaces (ports) used by the application services."""

from __future__ import annotations

from typing import Protocol


class PasswordHasher(Protocol):
    """One-way password hashing. Implementations must salt every hash."""

    def hash(self, password: str) -> str:
        """The hash to store. It embeds the algorithm, its parameters and the salt."""

    def verify(self, password_hash: str, password: str) -> bool:
        """True only for a matching password. A wrong password and a malformed
        hash both return False; they never raise."""

    def needs_rehash(self, password_hash: str) -> bool:
        """True if the stored hash uses weaker parameters than the current ones
        (or cannot be understood) and should be recomputed at the next sign-in."""
