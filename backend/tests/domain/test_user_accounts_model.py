"""The account-related parts of the User model (Phase 4A.1)."""

import dataclasses
import uuid
from datetime import UTC, datetime

import pytest

from app.domain.models import MAX_PASSWORD_HASH_LENGTH, Role, User
from app.errors import InvalidDataError

NOW = datetime(2026, 11, 25, tzinfo=UTC)
ARGON2_STYLE_HASH = "$argon2id$v=19$m=65536,t=3,p=4$c29tZXNhbHQ$aGFzaGhhc2hoYXNo"


def make_user(**overrides):
    values = dict(id=uuid.uuid4(), email="ana@example.com", created_at=NOW)
    return User(**{**values, **overrides})


def test_a_new_user_is_a_regular_active_account_that_cannot_sign_in_yet():
    user = User.new("ana@example.com")
    assert user.role is Role.USER
    assert user.is_active is True
    assert user.password_hash is None
    assert user.can_sign_in is False


def test_new_accepts_a_role():
    assert User.new("boss@example.com", role=Role.ADMIN).role is Role.ADMIN


@pytest.mark.parametrize("role", ["user", "admin", "superuser", None, 1])
def test_the_role_must_be_a_role_value_not_a_string(role):
    with pytest.raises(InvalidDataError):
        make_user(role=role)


def test_roles_have_stable_text_values_for_storage():
    assert [role.value for role in Role] == ["user", "admin"]
    assert Role("admin") is Role.ADMIN


@pytest.mark.parametrize(
    "password_hash",
    [ARGON2_STYLE_HASH, "x", "x" * MAX_PASSWORD_HASH_LENGTH, None],
)
def test_opaque_printable_hashes_are_accepted(password_hash):
    assert make_user(password_hash=password_hash).password_hash == password_hash


@pytest.mark.parametrize(
    "password_hash",
    ["", " ", "has space", "tab\there", "new\nline", "ñ", "x" * (MAX_PASSWORD_HASH_LENGTH + 1), 5, b"bytes"],
)
def test_malformed_hashes_are_rejected(password_hash):
    with pytest.raises(InvalidDataError):
        make_user(password_hash=password_hash)


def test_the_hash_never_appears_in_repr_or_str():
    user = make_user(password_hash=ARGON2_STYLE_HASH)
    assert ARGON2_STYLE_HASH not in repr(user)
    assert "argon2" not in repr(user) and "argon2" not in str(user)
    assert "password" not in repr(user).lower()


def test_the_hash_still_takes_part_in_equality():
    a = make_user(password_hash=ARGON2_STYLE_HASH)
    assert a == dataclasses.replace(a)
    assert a != dataclasses.replace(a, password_hash="$other")


@pytest.mark.parametrize("active", [1, 0, "yes", None])
def test_is_active_must_be_a_real_boolean(active):
    with pytest.raises(InvalidDataError):
        make_user(is_active=active)


@pytest.mark.parametrize(
    "is_active, password_hash, expected",
    [(True, ARGON2_STYLE_HASH, True), (True, None, False), (False, ARGON2_STYLE_HASH, False), (False, None, False)],
)
def test_can_sign_in_needs_an_active_account_with_a_password(is_active, password_hash, expected):
    assert make_user(is_active=is_active, password_hash=password_hash).can_sign_in is expected


def test_the_with_methods_return_changed_copies_and_keep_the_original():
    user = make_user()
    with_hash = user.with_password_hash(ARGON2_STYLE_HASH)
    admin = user.with_role(Role.ADMIN)
    disabled = user.with_active(False)
    assert (user.password_hash, user.role, user.is_active) == (None, Role.USER, True)
    assert with_hash.password_hash == ARGON2_STYLE_HASH and with_hash.id == user.id
    assert admin.role is Role.ADMIN and admin.email == user.email
    assert disabled.is_active is False


def test_the_with_methods_validate_their_input():
    user = make_user()
    with pytest.raises(InvalidDataError):
        user.with_password_hash("has space")
    with pytest.raises(InvalidDataError):
        user.with_role("admin")
    with pytest.raises(InvalidDataError):
        user.with_active("no")
