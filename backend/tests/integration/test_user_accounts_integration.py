"""The `users` commands end to end: the real CLI process, real PostgreSQL and real
Argon2id hashing. Checks what is actually stored and what is printed."""

import pytest
from sqlalchemy import text

from app.infrastructure.argon2_hasher import Argon2PasswordHasher
from app.infrastructure.db.database import create_database_engine
from tests.support.migrations import upgrade
from tests.support.processes import output_field, run_cli

pytestmark = pytest.mark.integration

PASSWORD = "correct horse battery staple"


@pytest.fixture
def database_url(database_factory):
    url = database_factory.create()
    engine = create_database_engine(url)
    try:
        upgrade(engine)
    finally:
        engine.dispose()
    return url


def stored_row(database_url, email):
    engine = create_database_engine(database_url)
    try:
        with engine.connect() as connection:
            return connection.execute(
                text("SELECT id, role, password_hash, is_active FROM users WHERE email = :e"),
                {"e": email},
            ).one_or_none()
    finally:
        engine.dispose()


def assert_no_password(result):
    assert PASSWORD not in result.stdout and PASSWORD not in result.stderr


def test_a_password_read_from_stdin_is_stored_only_as_an_argon2id_hash(database_url):
    result = run_cli(
        database_url, "users", "add", "--email", "Ana@Example.com", "--password-stdin",
        input=PASSWORD + "\n",
    )
    assert result.returncode == 0, result.stderr
    assert "can sign in: yes" in result.stdout
    assert_no_password(result)
    row = stored_row(database_url, "ana@example.com")
    assert row.role == "user" and row.is_active is True
    assert row.password_hash.startswith("$argon2id$")
    assert PASSWORD not in row.password_hash
    assert Argon2PasswordHasher().verify(row.password_hash, PASSWORD) is True
    assert Argon2PasswordHasher().verify(row.password_hash, PASSWORD + "x") is False


def test_a_password_that_breaks_the_policy_is_rejected_and_nothing_is_stored(database_url):
    result = run_cli(
        database_url, "users", "add", "--email", "ana@example.com", "--password-stdin",
        input="too short\n",
    )
    assert result.returncode == 8
    assert "between 12 and 128" in result.stderr
    assert "too short" not in result.stdout + result.stderr
    assert stored_row(database_url, "ana@example.com") is None


def test_a_user_can_be_created_without_a_password_and_given_one_later(database_url):
    created = run_cli(database_url, "users", "add", "--email", "ana@example.com", "--no-password")
    assert created.returncode == 0 and "can sign in: no" in created.stdout
    assert stored_row(database_url, "ana@example.com").password_hash is None
    updated = run_cli(
        database_url, "users", "set-password", "--email", "ana@example.com", "--password-stdin",
        input=PASSWORD + "\n",
    )
    assert updated.returncode == 0 and "can sign in: yes" in updated.stdout
    assert_no_password(updated)
    assert stored_row(database_url, "ana@example.com").password_hash.startswith("$argon2id$")


def test_replacing_a_password_changes_the_stored_hash(database_url):
    run_cli(database_url, "users", "add", "--email", "ana@example.com", "--password-stdin", input=PASSWORD + "\n")
    first = stored_row(database_url, "ana@example.com").password_hash
    new_password = "a completely different passphrase"
    run_cli(database_url, "users", "set-password", "--email", "ana@example.com", "--password-stdin", input=new_password + "\n")
    second = stored_row(database_url, "ana@example.com").password_hash
    hasher = Argon2PasswordHasher()
    assert first != second
    assert hasher.verify(second, new_password) and not hasher.verify(second, PASSWORD)


def test_roles_and_status_are_changed_from_the_cli(database_url):
    run_cli(database_url, "users", "add", "--email", "boss@example.com", "--no-password")
    assert run_cli(database_url, "users", "set-role", "--email", "boss@example.com", "--role", "admin").returncode == 0
    assert stored_row(database_url, "boss@example.com").role == "admin"
    assert run_cli(database_url, "users", "disable", "--email", "boss@example.com").returncode == 0
    assert stored_row(database_url, "boss@example.com").is_active is False
    assert run_cli(database_url, "users", "enable", "--email", "boss@example.com").returncode == 0
    assert stored_row(database_url, "boss@example.com").is_active is True


def test_a_duplicate_and_an_unknown_user_have_distinct_exit_codes(database_url):
    run_cli(database_url, "users", "add", "--email", "ana@example.com", "--no-password")
    duplicate = run_cli(database_url, "users", "add", "--email", "ANA@example.com", "--no-password")
    assert duplicate.returncode == 6 and "already exists" in duplicate.stderr
    unknown = run_cli(database_url, "users", "set-role", "--email", "nobody@example.com", "--role", "admin")
    assert unknown.returncode == 3 and "was not found" in unknown.stderr


def test_the_listing_never_shows_a_hash_or_a_password(database_url):
    run_cli(database_url, "users", "add", "--email", "ana@example.com", "--password-stdin", input=PASSWORD + "\n")
    run_cli(database_url, "users", "add", "--email", "bob@example.com", "--no-password")
    listing = run_cli(database_url, "users", "list")
    assert listing.returncode == 0
    lines = listing.stdout.splitlines()
    assert lines[0].split() == ["EMAIL", "ROLE", "ACTIVE", "PASSWORD", "ID"]
    assert lines[1].split()[3] == "set" and lines[2].split()[3:5] == ["not", "set"]
    assert "argon2" not in listing.stdout and PASSWORD not in listing.stdout


def test_an_account_created_by_adopting_a_container_has_no_password(database_url, container_name):
    run_cli(database_url, "plans", "add", "--slug", "dev", "--name", "Dev", "--memory-mb", "512", "--cpu-millicores", "500")
    adopted = run_cli(
        database_url, "servers", "adopt", container_name, "--owner", "owner@example.com",
        "--plan", "dev", "--name", "World",
    )
    assert adopted.returncode == 0, adopted.stderr
    row = stored_row(database_url, "owner@example.com")
    assert row.password_hash is None and row.role == "user" and row.is_active is True
    assert output_field(adopted.stdout, "owner").startswith("owner@example.com")
