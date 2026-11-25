"""Contract tests for sessions and login failures (see conftest.py for how each
test runs against both the in-memory and the PostgreSQL implementation)."""

import hashlib
import uuid
from datetime import UTC, datetime, timedelta, timezone

import pytest

from app.domain.auth import LoginFailure, Session
from app.domain.models import User
from app.errors import AlreadyExistsError, UserNotFoundError

T0 = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
WEEK = timedelta(days=7)


def digest(label):
    return hashlib.sha256(label.encode()).hexdigest()


def make_user(email="ana@example.com"):
    return User.new(email, created_at=T0)


def make_session(user, label="token-1", minutes=0, lifetime=WEEK):
    created = T0 + timedelta(minutes=minutes)
    return Session.new(user.id, digest(label), created, lifetime)


@pytest.fixture
def ana(uow_factory):
    user = make_user()
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    return user


@pytest.fixture
def bob(uow_factory):
    user = make_user("bob@example.com")
    with uow_factory() as uow:
        uow.users.add(user)
        uow.commit()
    return user


# --- sessions: storing and finding ---------------------------------------------------


def test_a_session_round_trips_by_its_token_hash(uow_factory, ana):
    session = make_session(ana)
    with uow_factory() as uow:
        uow.sessions.add(session)
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("token-1")) == session
        assert uow.sessions.find_by_token_hash(digest("other")) is None


def test_a_session_for_an_unknown_user_is_rejected(uow_factory):
    with uow_factory() as uow:
        with pytest.raises(UserNotFoundError):
            uow.sessions.add(make_session(make_user("ghost@example.com")))


def test_a_token_hash_can_only_be_stored_once(uow_factory, ana, bob):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana, "same"))
        with pytest.raises(AlreadyExistsError):
            uow.sessions.add(make_session(bob, "same"))


def test_a_rejected_session_does_not_poison_the_unit(uow_factory, ana):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana, "first"))
        with pytest.raises(AlreadyExistsError):
            uow.sessions.add(make_session(ana, "first"))
        uow.sessions.add(make_session(ana, "second"))
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("second")) is not None


def test_session_times_keep_their_instant_across_time_zones_and_precision(uow_factory, ana):
    odd = datetime(2026, 11, 25, 14, 30, 5, 123456, tzinfo=timezone(timedelta(hours=2)))
    session = Session.new(ana.id, digest("t"), odd, WEEK)
    with uow_factory() as uow:
        uow.sessions.add(session)
        uow.commit()
    with uow_factory() as uow:
        stored = uow.sessions.find_by_token_hash(digest("t"))
    assert stored == session and stored.created_at.tzinfo is not None


def test_an_uncommitted_session_is_discarded(uow_factory, ana):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana))
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("token-1")) is None


# --- sessions: activity and revocation --------------------------------------------------


def test_touch_records_activity(uow_factory, ana):
    session = make_session(ana)
    later = T0 + timedelta(minutes=5)
    with uow_factory() as uow:
        uow.sessions.add(session)
        uow.commit()
    with uow_factory() as uow:
        uow.sessions.touch(session.id, later)
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("token-1")).last_seen_at == later


def test_touch_and_revoke_ignore_unknown_sessions(uow_factory):
    with uow_factory() as uow:
        uow.sessions.touch(uuid.uuid4(), T0)
        uow.sessions.revoke(uuid.uuid4(), T0)
        uow.commit()


def test_revoke_marks_the_session_once_and_keeps_the_first_time(uow_factory, ana):
    session = make_session(ana)
    first, second = T0 + timedelta(minutes=1), T0 + timedelta(minutes=9)
    with uow_factory() as uow:
        uow.sessions.add(session)
        uow.sessions.revoke(session.id, first)
        uow.sessions.revoke(session.id, second)
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("token-1")).revoked_at == first


def test_revoke_all_for_user_only_touches_that_users_unrevoked_sessions(uow_factory, ana, bob):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana, "a1"))
        uow.sessions.add(make_session(ana, "a2"))
        done = make_session(ana, "a3")
        uow.sessions.add(done)
        uow.sessions.revoke(done.id, T0)
        uow.sessions.add(make_session(bob, "b1"))
        uow.commit()
    when = T0 + timedelta(hours=1)
    with uow_factory() as uow:
        assert uow.sessions.revoke_all_for_user(ana.id, when) == 2
        assert uow.sessions.revoke_all_for_user(ana.id, when) == 0  # nothing left to revoke
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("a1")).revoked_at == when
        assert uow.sessions.find_by_token_hash(digest("a3")).revoked_at == T0  # kept its own time
        assert uow.sessions.find_by_token_hash(digest("b1")).revoked_at is None


def test_active_sessions_are_listed_newest_first_and_exclude_revoked_and_expired(uow_factory, ana):
    with uow_factory() as uow:
        for minutes, label in ((0, "oldest"), (10, "middle"), (20, "newest")):
            uow.sessions.add(make_session(ana, label, minutes))
        revoked = make_session(ana, "revoked", 30)
        uow.sessions.add(revoked)
        uow.sessions.revoke(revoked.id, T0 + timedelta(minutes=31))
        uow.sessions.add(make_session(ana, "expired", 40, lifetime=timedelta(minutes=5)))
        uow.commit()
    with uow_factory() as uow:
        active = uow.sessions.list_active_for_user(ana.id, T0 + timedelta(hours=1))
    assert [s.token_hash for s in active] == [digest("newest"), digest("middle"), digest("oldest")]


def test_the_newest_sessions_are_kept_and_the_rest_revoked(uow_factory, ana, bob):
    with uow_factory() as uow:
        for minutes in range(5):
            uow.sessions.add(make_session(ana, f"a{minutes}", minutes))
        uow.sessions.add(make_session(bob, "b0"))
        uow.commit()
    now = T0 + timedelta(hours=1)
    with uow_factory() as uow:
        assert uow.sessions.revoke_oldest_beyond(ana.id, 3, now) == 2
        assert uow.sessions.revoke_oldest_beyond(ana.id, 3, now) == 0
        uow.commit()
    with uow_factory() as uow:
        kept = [s.token_hash for s in uow.sessions.list_active_for_user(ana.id, now)]
        assert kept == [digest("a4"), digest("a3"), digest("a2")]
        assert uow.sessions.find_by_token_hash(digest("a0")).revoked_at == now
        assert uow.sessions.find_by_token_hash(digest("b0")).revoked_at is None  # other user untouched


def test_keeping_zero_revokes_everything_and_keeping_more_than_exist_revokes_nothing(uow_factory, ana):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana, "one"))
        uow.sessions.add(make_session(ana, "two", 1))
        uow.commit()
    now = T0 + timedelta(hours=1)
    with uow_factory() as uow:
        assert uow.sessions.revoke_oldest_beyond(ana.id, 10, now) == 0
        assert uow.sessions.revoke_oldest_beyond(ana.id, 0, now) == 2


# --- sessions: cleanup ----------------------------------------------------------------------


def test_finished_sessions_are_deleted_and_active_ones_kept(uow_factory, ana):
    with uow_factory() as uow:
        uow.sessions.add(make_session(ana, "active"))
        uow.sessions.add(make_session(ana, "expired", lifetime=timedelta(hours=1)))
        revoked = make_session(ana, "revoked")
        uow.sessions.add(revoked)
        uow.sessions.revoke(revoked.id, T0 + timedelta(hours=1))
        recently = make_session(ana, "recently-revoked")
        uow.sessions.add(recently)
        uow.sessions.revoke(recently.id, T0 + timedelta(hours=5))
        uow.commit()
    before = T0 + timedelta(hours=3)
    with uow_factory() as uow:
        assert uow.sessions.delete_finished_before(before, 100) == 2  # expired + revoked early
        assert uow.sessions.delete_finished_before(before, 100) == 0
        uow.commit()
    with uow_factory() as uow:
        assert uow.sessions.find_by_token_hash(digest("active")) is not None
        assert uow.sessions.find_by_token_hash(digest("recently-revoked")) is not None
        assert uow.sessions.find_by_token_hash(digest("expired")) is None
        assert uow.sessions.find_by_token_hash(digest("revoked")) is None


def test_cleanup_deletes_at_most_the_limit(uow_factory, ana):
    with uow_factory() as uow:
        for index in range(5):
            uow.sessions.add(make_session(ana, f"e{index}", index, lifetime=timedelta(minutes=1)))
        uow.commit()
    before = T0 + timedelta(days=1)
    with uow_factory() as uow:
        assert uow.sessions.delete_finished_before(before, 3) == 3
        assert uow.sessions.delete_finished_before(before, 3) == 2
        assert uow.sessions.delete_finished_before(before, 3) == 0


# --- login failures -------------------------------------------------------------------------


def failure(email="ana@example.com", client="203.0.113.7", minutes=0):
    return LoginFailure.new(digest(email), client, T0 + timedelta(minutes=minutes))


def test_recent_failures_are_listed_oldest_first_and_filtered(uow_factory):
    with uow_factory() as uow:
        uow.login_failures.add(failure("ana@example.com", "1.1.1.1", 3))
        uow.login_failures.add(failure("ana@example.com", "1.1.1.1", 1))
        uow.login_failures.add(failure("ana@example.com", "2.2.2.2", 2))
        uow.login_failures.add(failure("bob@example.com", "1.1.1.1", 4))
        uow.commit()
    since = T0
    t = lambda m: T0 + timedelta(minutes=m)  # noqa: E731
    with uow_factory() as uow:
        repo = uow.login_failures
        assert repo.recent(since) == [t(1), t(2), t(3), t(4)]
        assert repo.recent(since, email_hash=digest("ana@example.com")) == [t(1), t(2), t(3)]
        assert repo.recent(since, client_key="1.1.1.1") == [t(1), t(3), t(4)]
        assert repo.recent(since, digest("ana@example.com"), "1.1.1.1") == [t(1), t(3)]
        assert repo.recent(since, digest("nobody@example.com")) == []


def test_recent_includes_the_boundary_and_excludes_older_failures(uow_factory):
    with uow_factory() as uow:
        uow.login_failures.add(failure(minutes=-10))
        uow.login_failures.add(failure(minutes=0))
        uow.login_failures.add(failure(minutes=10))
        uow.commit()
    with uow_factory() as uow:
        assert uow.login_failures.recent(T0) == [T0, T0 + timedelta(minutes=10)]


def test_recent_honours_the_limit_and_keeps_the_oldest(uow_factory):
    with uow_factory() as uow:
        for minutes in range(6):
            uow.login_failures.add(failure(minutes=minutes))
        uow.commit()
    with uow_factory() as uow:
        assert uow.login_failures.recent(T0, limit=3) == [T0 + timedelta(minutes=m) for m in range(3)]


def test_deleting_a_pair_forgets_only_that_pair(uow_factory):
    with uow_factory() as uow:
        uow.login_failures.add(failure("ana@example.com", "1.1.1.1", 1))
        uow.login_failures.add(failure("ana@example.com", "1.1.1.1", 2))
        uow.login_failures.add(failure("ana@example.com", "2.2.2.2", 3))
        uow.login_failures.add(failure("bob@example.com", "1.1.1.1", 4))
        uow.commit()
    with uow_factory() as uow:
        assert uow.login_failures.delete(digest("ana@example.com"), "1.1.1.1") == 2
        assert uow.login_failures.delete(digest("ana@example.com"), "1.1.1.1") == 0
        uow.commit()
    with uow_factory() as uow:
        assert len(uow.login_failures.recent(T0)) == 2


def test_old_failures_are_cleaned_up_within_the_limit(uow_factory):
    with uow_factory() as uow:
        for minutes in range(5):
            uow.login_failures.add(failure(minutes=minutes))
        uow.login_failures.add(failure(minutes=60))
        uow.commit()
    cutoff = T0 + timedelta(minutes=30)
    with uow_factory() as uow:
        assert uow.login_failures.delete_older_than(cutoff, 3) == 3
        assert uow.login_failures.delete_older_than(cutoff, 3) == 2
        assert uow.login_failures.delete_older_than(cutoff, 3) == 0
        uow.commit()
    with uow_factory() as uow:
        assert uow.login_failures.recent(T0 - timedelta(days=1)) == [T0 + timedelta(minutes=60)]


def test_uncommitted_failures_are_discarded(uow_factory):
    with uow_factory() as uow:
        uow.login_failures.add(failure())
    with uow_factory() as uow:
        assert uow.login_failures.recent(T0 - timedelta(days=1)) == []


def test_a_failure_survives_without_any_account(uow_factory):
    # Failures are keyed by a hash of whatever e-mail was tried; no user row is needed.
    with uow_factory() as uow:
        uow.login_failures.add(failure("never-registered@example.com"))
        uow.commit()
    with uow_factory() as uow:
        assert len(uow.login_failures.recent(T0)) == 1
