"""Sign-in and sessions on real PostgreSQL with real Argon2id.

Two independent sets of services are built on purpose: they stand for two API
processes. Everything they share must live in the database, not in memory.
"""

import statistics
import time
from datetime import UTC, datetime, timedelta

import pytest
from argon2 import PasswordHasher
from sqlalchemy import text

from app.composition import build_services
from app.domain.models import Role
from app.errors import AuthenticationError, TooManyAttemptsError
from app.infrastructure.argon2_hasher import Argon2PasswordHasher
from app.infrastructure.db.database import (
    create_database_engine,
    create_session_factory,
)
from app.infrastructure.db.repositories import SqlUnitOfWork
from app.services.auth import AuthService, hash_token

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"
EMAIL = "ana@example.com"
CLIENT = "203.0.113.7"


@pytest.fixture
def database_url(migrated_database_url):
    engine = create_database_engine(migrated_database_url)
    with engine.begin() as connection:
        connection.execute(
            text("TRUNCATE sessions, login_failures, game_servers, plans, users")
        )
    engine.dispose()
    return migrated_database_url


@pytest.fixture
def process_a(database_url):
    services = build_services(database_url)
    yield services
    services.close()


@pytest.fixture
def process_b(database_url):
    services = build_services(database_url)
    yield services
    services.close()


def rows(services, table):
    with services.engine.connect() as connection:
        return connection.execute(text(f"SELECT * FROM {table}")).mappings().all()


def test_a_full_session_lifecycle_with_the_real_hasher(process_a):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    signed_in = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert process_a.auth.authenticate(signed_in.session_token).user.email == EMAIL
    process_a.auth.sign_out(signed_in.session_token)
    with pytest.raises(AuthenticationError):
        process_a.auth.authenticate(signed_in.session_token)


def test_neither_the_token_nor_the_password_is_stored_anywhere(process_a):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    signed_in = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    with pytest.raises(AuthenticationError):
        process_a.auth.sign_in(EMAIL, "definitely the wrong password", CLIENT)
    everything = repr(
        [rows(process_a, t) for t in ("users", "sessions", "login_failures")]
    )
    assert signed_in.session_token not in everything
    assert signed_in.csrf_token not in everything
    assert PASSWORD not in everything and "definitely the wrong password" not in everything
    (session,) = rows(process_a, "sessions")
    assert session["token_hash"] == hash_token(signed_in.session_token)
    (failure,) = rows(process_a, "login_failures")
    assert "@" not in failure["email_hash"] and len(failure["email_hash"]) == 64


def test_a_session_created_by_one_process_is_valid_in_another(process_a, process_b):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    token = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    assert process_b.auth.authenticate(token).user.email == EMAIL
    process_b.auth.sign_out(token)
    with pytest.raises(AuthenticationError):
        process_a.auth.authenticate(token)


def test_failed_attempts_are_counted_across_processes(process_a, process_b):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    for service in (process_a, process_b, process_a, process_b, process_a):
        with pytest.raises(AuthenticationError):
            service.auth.sign_in(EMAIL, "wrong password for this account", CLIENT)
    with pytest.raises(TooManyAttemptsError):
        process_b.auth.sign_in(EMAIL, PASSWORD, CLIENT)  # blocked by failures made in A and B


def test_the_throttle_blocks_after_five_real_failures_and_not_other_clients(process_a):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    for _ in range(5):
        with pytest.raises(AuthenticationError):
            process_a.auth.sign_in(EMAIL, "wrong password for this account", CLIENT)
    with pytest.raises(TooManyAttemptsError) as excinfo:
        process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert 1 <= excinfo.value.retry_after <= 15 * 60
    assert process_a.auth.sign_in(EMAIL, PASSWORD, "198.51.100.9")  # another client is fine
    assert len(rows(process_a, "login_failures")) == 5  # the blocked attempt left no trace


def test_changing_the_password_ends_the_sessions_of_every_process(process_a, process_b):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    token = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    process_b.accounts.set_password(EMAIL, "a brand new long password")
    with pytest.raises(AuthenticationError):
        process_a.auth.authenticate(token)
    assert process_a.auth.sign_in(EMAIL, "a brand new long password", CLIENT)


def test_disabling_an_account_ends_its_sessions_and_blocks_new_ones(process_a):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    token = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    process_a.accounts.set_active(EMAIL, False)
    with pytest.raises(AuthenticationError):
        process_a.auth.authenticate(token)
    with pytest.raises(AuthenticationError):
        process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    process_a.accounts.set_active(EMAIL, True)
    assert process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)


def test_the_role_is_read_from_the_database_on_every_request(process_a, process_b):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    token = process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT).session_token
    assert process_a.auth.authenticate(token).actor.is_admin is False
    process_b.accounts.set_role(EMAIL, Role.ADMIN)
    assert process_a.auth.authenticate(token).actor.is_admin is True


def test_an_old_hash_is_transparently_upgraded_at_sign_in(process_a, database_url):
    weak = Argon2PasswordHasher(PasswordHasher(time_cost=1, memory_cost=8, parallelism=1))
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    # Replace the stored hash with one made with much weaker parameters.
    with process_a.engine.begin() as connection:
        connection.execute(
            text("UPDATE users SET password_hash = :h WHERE email = :e"),
            {"h": weak.hash(PASSWORD), "e": EMAIL},
        )
    assert "m=8,t=1,p=1" in rows(process_a, "users")[0]["password_hash"]
    process_a.auth.sign_in(EMAIL, PASSWORD, CLIENT)
    upgraded = rows(process_a, "users")[0]["password_hash"]
    assert "m=65536,t=3,p=4" in upgraded
    assert Argon2PasswordHasher().verify(upgraded, PASSWORD)


def test_expired_sessions_and_old_failures_are_cleaned_up_by_signing_in(process_a, database_url):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    clock = {"now": datetime.now(UTC)}
    factory = create_session_factory(process_a.engine)
    auth = AuthService(
        lambda: SqlUnitOfWork(factory),
        Argon2PasswordHasher(),
        clock=lambda: clock["now"],
    )
    auth.sign_in(EMAIL, PASSWORD, CLIENT)
    with pytest.raises(AuthenticationError):
        auth.sign_in(EMAIL, "wrong password for this account", CLIENT)
    assert len(rows(process_a, "sessions")) == 1 and len(rows(process_a, "login_failures")) == 1
    clock["now"] += timedelta(days=9)  # past the 7-day lifetime plus a day of grace
    auth.sign_in(EMAIL, PASSWORD, CLIENT)
    assert len(rows(process_a, "sessions")) == 1  # the old one was deleted, the new one remains
    assert rows(process_a, "login_failures") == []


def test_an_unknown_account_costs_about_as_much_time_as_a_wrong_password(process_a):
    process_a.accounts.create_user(EMAIL, password=PASSWORD)
    process_a.auth.sign_in(EMAIL, PASSWORD, "198.51.100.200")  # warm up the hasher and the decoy

    def timed(email, client):
        start = time.perf_counter()
        with pytest.raises(AuthenticationError):
            process_a.auth.sign_in(email, "wrong password for this account", client)
        return time.perf_counter() - start

    wrong = statistics.median(timed(EMAIL, f"198.51.100.{n}") for n in range(1, 5))
    unknown = statistics.median(timed("nobody@example.com", f"198.51.100.{n}") for n in range(10, 14))
    assert unknown > 0.4 * wrong, (unknown, wrong)  # it really does the hashing work
    assert wrong > 0.4 * unknown, (unknown, wrong)
