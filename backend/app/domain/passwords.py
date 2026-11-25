"""Password policy.

The rules follow current guidance (NIST SP 800-63B): length matters, composition
rules (symbols, digits, mixed case) do not, and any printable character is
allowed. Passwords are Unicode-normalised (NFKC) so the same password typed on
different keyboards or devices verifies identically. The maximum length stops
absurdly long inputs from being used to burn CPU in the hasher.

The functions never include the password in an error message.
"""

import unicodedata

from app.errors import InvalidDataError

MIN_PASSWORD_LENGTH = 12
MAX_PASSWORD_LENGTH = 128


def normalize_password(password):
    return unicodedata.normalize("NFKC", password)


def validate_password(password):
    """Return the normalised password, or raise InvalidDataError."""
    if not isinstance(password, str):
        raise InvalidDataError("The password must be text.")
    normalized = normalize_password(password)
    if not MIN_PASSWORD_LENGTH <= len(normalized) <= MAX_PASSWORD_LENGTH:
        raise InvalidDataError(
            f"The password must be between {MIN_PASSWORD_LENGTH} and "
            f"{MAX_PASSWORD_LENGTH} characters long."
        )
    if any(unicodedata.category(char).startswith("C") for char in normalized):
        raise InvalidDataError("The password must not contain control characters.")
    return normalized
