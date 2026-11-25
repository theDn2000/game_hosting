import dataclasses
import uuid
from datetime import UTC, datetime, timedelta

import pytest

from app.domain.auth import MAX_CLIENT_KEY_LENGTH, Actor, LoginFailure, Session
from app.domain.models import Role, User
from app.errors import InvalidDataError

NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
DIGEST = "a" * 64
IDLE = timedelta(hours=8)


def make_session(**overrides):
    values = dict(
        id=uuid.uuid4(), user_id=uuid.uuid4(), token_hash=DIGEST,
        created_at=NOW, last_seen_at=NOW, expires_at=NOW + timedelta(days=7),
    )
    return Session(**{**values, **overrides})


# --- Actor ---------------------------------------------------------------------


def test_an_actor_is_built_from_a_user():
    user = User.new("boss@example.com", role=Role.ADMIN)
    actor = Actor.from_user(user)
    assert actor.user_id == user.id and actor.role is Role.ADMIN and actor.is_admin is True


def test_a_regular_actor_is_not_an_admin():
    assert Actor(uuid.uuid4(), Role.USER).is_admin is False


@pytest.mark.parametrize("user_id, role", [("x", Role.USER), (None, Role.USER), (uuid.uuid4(), "admin"), (uuid.uuid4(), None)])
def test_an_actor_needs_a_uuid_and_a_real_role(user_id, role):
    with pytest.raises(InvalidDataError):
        Actor(user_id, role)


def test_an_actor_is_immutable():
    with pytest.raises(dataclasses.FrozenInstanceError):
        Actor(uuid.uuid4(), Role.USER).role = Role.ADMIN


# --- Session ------------------------------------------------------------------


def test_a_new_session_expires_after_its_lifetime():
    session = Session.new(uuid.uuid4(), DIGEST, NOW, timedelta(days=7))
    assert session.created_at == session.last_seen_at == NOW
    assert session.expires_at == NOW + timedelta(days=7)
    assert session.revoked_at is None


@pytest.mark.parametrize("lifetime", [timedelta(0), timedelta(seconds=-1), 3600, None])
def test_the_lifetime_must_be_a_positive_duration(lifetime):
    with pytest.raises(InvalidDataError):
        Session.new(uuid.uuid4(), DIGEST, NOW, lifetime)


@pytest.mark.parametrize("digest", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "z" * 64, None, 5, b"a" * 64])
def test_the_token_hash_must_be_a_lowercase_sha256_digest(digest):
    with pytest.raises(InvalidDataError):
        make_session(token_hash=digest)


def test_the_token_hash_is_not_shown_in_repr():
    assert DIGEST not in repr(make_session())


def test_time_ordering_is_enforced():
    with pytest.raises(InvalidDataError):
        make_session(last_seen_at=NOW - timedelta(seconds=1))
    with pytest.raises(InvalidDataError):
        make_session(expires_at=NOW)
    with pytest.raises(InvalidDataError):
        make_session(expires_at=NOW - timedelta(days=1))


def test_timestamps_must_be_timezone_aware():
    with pytest.raises(InvalidDataError):
        make_session(created_at=datetime(2026, 1, 1), last_seen_at=datetime(2026, 1, 1))
    with pytest.raises(InvalidDataError):
        make_session(revoked_at=datetime(2026, 1, 1))


def test_a_fresh_session_is_valid():
    assert make_session().is_valid_at(NOW, IDLE) is True


def test_a_session_expires_exactly_at_its_absolute_limit():
    session = make_session(expires_at=NOW + timedelta(hours=1))
    assert session.with_last_seen(NOW + timedelta(minutes=59)).is_valid_at(NOW + timedelta(minutes=59, seconds=59), IDLE)
    assert not session.with_last_seen(NOW + timedelta(minutes=59)).is_valid_at(NOW + timedelta(hours=1), IDLE)


def test_a_session_idle_for_too_long_is_invalid_even_before_its_absolute_expiry():
    session = make_session()
    assert session.is_valid_at(NOW + IDLE - timedelta(seconds=1), IDLE) is True
    assert session.is_valid_at(NOW + IDLE, IDLE) is False


def test_activity_extends_the_idle_window_but_not_the_absolute_limit():
    session = make_session(expires_at=NOW + timedelta(hours=20))
    # Without activity it would be idle (and invalid) 8 hours after creation...
    assert session.is_valid_at(NOW + timedelta(hours=9), IDLE) is False
    # ...but activity at hour 7 keeps it valid until hour 15 (7 + 8 idle hours).
    active = session.with_last_seen(NOW + timedelta(hours=7))
    assert active.is_valid_at(NOW + timedelta(hours=14), IDLE) is True
    assert active.is_valid_at(NOW + timedelta(hours=15), IDLE) is False
    # And activity never extends the absolute limit.
    busy = session.with_last_seen(NOW + timedelta(hours=19))
    assert busy.is_valid_at(NOW + timedelta(hours=19, minutes=59), IDLE) is True
    assert busy.is_valid_at(NOW + timedelta(hours=20), IDLE) is False


def test_a_revoked_session_is_never_valid():
    assert make_session().revoked(NOW).is_valid_at(NOW, IDLE) is False


def test_the_with_methods_return_copies():
    session = make_session()
    assert session.revoked(NOW).revoked_at == NOW and session.revoked_at is None
    later = NOW + timedelta(minutes=5)
    assert session.with_last_seen(later).last_seen_at == later and session.last_seen_at == NOW


# --- LoginFailure -------------------------------------------------------------


def test_a_login_failure_records_a_hashed_email_and_a_client_key():
    failure = LoginFailure.new(DIGEST, "203.0.113.7", NOW)
    assert (failure.email_hash, failure.client_key, failure.occurred_at) == (DIGEST, "203.0.113.7", NOW)


@pytest.mark.parametrize("key", ["", " ", "has space", "tab\t", "ñ", "x" * (MAX_CLIENT_KEY_LENGTH + 1), None, 5])
def test_the_client_key_must_be_short_printable_ascii(key):
    with pytest.raises(InvalidDataError):
        LoginFailure.new(DIGEST, key, NOW)


@pytest.mark.parametrize("key", ["203.0.113.7", "2001:db8::1", "fe80::1%eth0", "x" * MAX_CLIENT_KEY_LENGTH])
def test_ipv4_ipv6_and_zone_ids_are_valid_client_keys(key):
    assert LoginFailure.new(DIGEST, key, NOW).client_key == key


def test_a_plain_email_is_not_a_valid_email_hash():
    with pytest.raises(InvalidDataError):
        LoginFailure.new("ana@example.com", "203.0.113.7", NOW)


# --- the local operator ------------------------------------------------------------


def test_the_operator_is_an_administrator_that_is_not_a_database_user():
    from app.domain.auth import OPERATOR

    assert OPERATOR.is_admin
    assert OPERATOR.user_id.int == 0
