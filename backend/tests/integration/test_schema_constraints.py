"""The database enforces the domain's rules on its own (defence in depth).

Alembic does not compare CHECK constraints, so this file is what proves that the
migration's constraints match app.domain.models. Every statement is run inside a
transaction that is rolled back, so the shared migrated database stays empty.
"""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.domain.models import (
    MAX_CPU_MILLICORES,
    MAX_EMAIL_LENGTH,
    MAX_MEMORY_MB,
    MAX_PASSWORD_HASH_LENGTH,
    MAX_PLAN_NAME_LENGTH,
    MAX_SERVER_NAME_LENGTH,
)
from app.infrastructure.db.database import create_database_engine

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 29, tzinfo=UTC)


@pytest.fixture
def connection(migrated_database_url):
    engine = create_database_engine(migrated_database_url)
    with engine.connect() as connection:
        transaction = connection.begin()
        yield connection
        transaction.rollback()
    engine.dispose()


def insert_user(connection, email="ana@example.com", user_id=None):
    user_id = user_id or uuid.uuid4()
    connection.execute(
        text("INSERT INTO users (id, email, created_at) VALUES (:id, :email, :now)"),
        {"id": user_id, "email": email, "now": NOW},
    )
    return user_id


def insert_plan(connection, slug="basic", name="Basic", memory_mb=2048, cpu=1000):
    plan_id = uuid.uuid4()
    connection.execute(
        text(
            "INSERT INTO plans (id, slug, name, memory_mb, cpu_millicores) "
            "VALUES (:id, :slug, :name, :memory, :cpu)"
        ),
        {"id": plan_id, "slug": slug, "name": name, "memory": memory_mb, "cpu": cpu},
    )
    return plan_id


def insert_server(connection, owner_id, plan_id, name="World", container="gh-1"):
    connection.execute(
        text(
            "INSERT INTO game_servers (id, owner_id, plan_id, game, name, container_name, created_at) "
            "VALUES (:id, :owner, :plan, 'minecraft-java', :name, :container, :now)"
        ),
        {
            "id": uuid.uuid4(),
            "owner": owner_id,
            "plan": plan_id,
            "name": name,
            "container": container,
            "now": NOW,
        },
    )


def violated_constraint(excinfo):
    return excinfo.value.orig.diag.constraint_name


def assert_rejected(excinfo, constraint):
    assert violated_constraint(excinfo) == constraint


# --- users --------------------------------------------------------------------


@pytest.mark.parametrize(
    "email, constraint",
    [
        ("Ana@Example.com", "ck_users_email_lowercase"),
        ("ab", "ck_users_email_length"),
        ("a" * (MAX_EMAIL_LENGTH - 10) + "@example.com" + "x" * 10, "ck_users_email_length"),
    ],
    ids=["uppercase", "too-short", "too-long"],
)
def test_users_reject_invalid_emails(connection, email, constraint):
    with pytest.raises(IntegrityError) as excinfo:
        insert_user(connection, email)
    assert_rejected(excinfo, constraint)


def test_users_accept_the_longest_email_and_reject_duplicates(connection):
    insert_user(connection, "a" * (MAX_EMAIL_LENGTH - 12) + "@example.com")
    insert_user(connection, "ana@example.com")
    with pytest.raises(IntegrityError) as excinfo:
        insert_user(connection, "ana@example.com")
    assert_rejected(excinfo, "uq_users_email")


# --- user accounts ------------------------------------------------------------------


def insert_account(connection, role="user", password_hash=None, is_active=True, email="acct@example.com"):
    connection.execute(
        text(
            "INSERT INTO users (id, email, created_at, role, password_hash, is_active) "
            "VALUES (:id, :email, :now, :role, :hash, :active)"
        ),
        {"id": uuid.uuid4(), "email": email, "now": NOW, "role": role, "hash": password_hash, "active": is_active},
    )


@pytest.mark.parametrize("role", ["superuser", "Admin", "ADMIN", "", "root"])
def test_users_reject_unknown_roles(connection, role):
    with pytest.raises(IntegrityError) as excinfo:
        insert_account(connection, role=role)
    assert_rejected(excinfo, "ck_users_role_valid")


def test_users_accept_both_roles(connection):
    insert_account(connection, role="user", email="u@example.com")
    insert_account(connection, role="admin", email="a@example.com")


@pytest.mark.parametrize("password_hash", ["", "x" * (MAX_PASSWORD_HASH_LENGTH + 1)])
def test_users_reject_empty_or_oversized_password_hashes(connection, password_hash):
    with pytest.raises(IntegrityError) as excinfo:
        insert_account(connection, password_hash=password_hash)
    assert_rejected(excinfo, "ck_users_password_hash_valid")


def test_users_accept_no_hash_and_the_longest_hash(connection):
    insert_account(connection, password_hash=None, email="none@example.com")
    insert_account(connection, password_hash="x" * MAX_PASSWORD_HASH_LENGTH, email="long@example.com")


def test_omitted_account_columns_get_safe_defaults(connection):
    user_id = insert_user(connection, "defaults@example.com")
    row = connection.execute(
        text("SELECT role, is_active, password_hash FROM users WHERE id = :id"), {"id": user_id}
    ).one()
    assert (row.role, row.is_active, row.password_hash) == ("user", True, None)


@pytest.mark.parametrize("column", ["role", "is_active"])
def test_role_and_active_flag_cannot_be_null(connection, column):
    insert_account(connection, email="nulls@example.com")
    with pytest.raises(IntegrityError):
        connection.execute(text(f"UPDATE users SET {column} = NULL WHERE email = 'nulls@example.com'"))


# --- sessions and login failures -----------------------------------------------------


def insert_session(connection, user_id, token_hash=None, created_at=NOW, last_seen_at=None,
                   expires_at=None):
    from datetime import timedelta

    connection.execute(
        text(
            "INSERT INTO sessions (id, user_id, token_hash, created_at, last_seen_at, expires_at) "
            "VALUES (:id, :user, :hash, :created, :seen, :expires)"
        ),
        {
            "id": uuid.uuid4(),
            "user": user_id,
            "hash": token_hash if token_hash is not None else uuid.uuid4().hex * 2,
            "created": created_at,
            "seen": last_seen_at or created_at,
            "expires": expires_at or created_at + timedelta(days=7),
        },
    )


@pytest.mark.parametrize("token_hash", ["", "a" * 63, "a" * 65, "A" * 64, "g" * 64, "plain-token"])
def test_sessions_reject_anything_but_a_lowercase_sha256_hex_digest(connection, token_hash):
    user_id = insert_user(connection)
    with pytest.raises(IntegrityError) as excinfo:
        insert_session(connection, user_id, token_hash=token_hash)
    assert_rejected(excinfo, "ck_sessions_token_hash_format")


def test_sessions_accept_a_digest_and_reject_a_repeated_one(connection):
    user_id = insert_user(connection)
    insert_session(connection, user_id, token_hash="a" * 64)
    with pytest.raises(IntegrityError) as excinfo:
        insert_session(connection, user_id, token_hash="a" * 64)
    assert_rejected(excinfo, "uq_sessions_token_hash")


def test_sessions_cannot_be_seen_before_they_were_created(connection):
    from datetime import timedelta

    user_id = insert_user(connection)
    with pytest.raises(IntegrityError) as excinfo:
        insert_session(connection, user_id, last_seen_at=NOW - timedelta(seconds=1))
    assert_rejected(excinfo, "ck_sessions_last_seen_after_created")


@pytest.mark.parametrize("offset_seconds", [0, -60])
def test_sessions_must_expire_after_they_were_created(connection, offset_seconds):
    from datetime import timedelta

    user_id = insert_user(connection)
    with pytest.raises(IntegrityError) as excinfo:
        insert_session(connection, user_id, expires_at=NOW + timedelta(seconds=offset_seconds))
    assert_rejected(excinfo, "ck_sessions_expires_after_created")


def test_sessions_need_an_existing_user(connection):
    with pytest.raises(IntegrityError) as excinfo:
        insert_session(connection, uuid.uuid4())
    assert_rejected(excinfo, "fk_sessions_user_id_users")


def test_deleting_a_user_deletes_their_sessions(connection):
    user_id = insert_user(connection)
    insert_session(connection, user_id)
    insert_session(connection, user_id)
    connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": user_id})
    assert connection.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


def insert_failure(connection, email_hash="a" * 64, client_key="203.0.113.7"):
    connection.execute(
        text(
            "INSERT INTO login_failures (id, email_hash, client_key, occurred_at) "
            "VALUES (:id, :hash, :key, :now)"
        ),
        {"id": uuid.uuid4(), "hash": email_hash, "key": client_key, "now": NOW},
    )


@pytest.mark.parametrize("email_hash", ["", "ana@example.com", "A" * 64, "a" * 63])
def test_login_failures_only_store_a_hashed_email(connection, email_hash):
    with pytest.raises(IntegrityError) as excinfo:
        insert_failure(connection, email_hash=email_hash)
    assert_rejected(excinfo, "ck_login_failures_email_hash_format")


@pytest.mark.parametrize("client_key", ["", "x" * 65])
def test_login_failures_bound_the_client_key_length(connection, client_key):
    with pytest.raises(IntegrityError) as excinfo:
        insert_failure(connection, client_key=client_key)
    assert_rejected(excinfo, "ck_login_failures_client_key_length")


def test_login_failures_accept_the_longest_client_key_and_need_no_account(connection):
    insert_failure(connection, client_key="x" * 64)  # no matching user exists, by design


# --- plans --------------------------------------------------------------------


@pytest.mark.parametrize("slug", ["Pro", "-x", "a_b", "a b", "", "a" * 64])
def test_plans_reject_invalid_slugs(connection, slug):
    with pytest.raises(IntegrityError) as excinfo:
        insert_plan(connection, slug=slug)
    assert_rejected(excinfo, "ck_plans_slug_format")


def test_plans_accept_the_longest_slug_and_reject_duplicates(connection):
    insert_plan(connection, slug="a" * 63)
    insert_plan(connection, slug="basic")
    with pytest.raises(IntegrityError) as excinfo:
        insert_plan(connection, slug="basic")
    assert_rejected(excinfo, "uq_plans_slug")


@pytest.mark.parametrize("name", ["", " x", "x ", "x" * (MAX_PLAN_NAME_LENGTH + 1)])
def test_plans_reject_invalid_names(connection, name):
    with pytest.raises(IntegrityError) as excinfo:
        insert_plan(connection, name=name)
    assert_rejected(excinfo, "ck_plans_name_valid")


@pytest.mark.parametrize("memory", [0, -1, MAX_MEMORY_MB + 1])
def test_plans_reject_invalid_memory(connection, memory):
    with pytest.raises(IntegrityError) as excinfo:
        insert_plan(connection, memory_mb=memory)
    assert_rejected(excinfo, "ck_plans_memory_mb_range")


@pytest.mark.parametrize("cpu", [0, -1, MAX_CPU_MILLICORES + 1])
def test_plans_reject_invalid_cpu(connection, cpu):
    with pytest.raises(IntegrityError) as excinfo:
        insert_plan(connection, cpu=cpu)
    assert_rejected(excinfo, "ck_plans_cpu_millicores_range")


def test_plans_accept_the_limits(connection):
    insert_plan(connection, slug="min", name="N" * MAX_PLAN_NAME_LENGTH, memory_mb=1, cpu=1)
    insert_plan(connection, slug="max", memory_mb=MAX_MEMORY_MB, cpu=MAX_CPU_MILLICORES)


# --- servers ------------------------------------------------------------------


@pytest.fixture
def owner_and_plan(connection):
    return insert_user(connection), insert_plan(connection)


@pytest.mark.parametrize("name", ["", " x", "x ", "x" * (MAX_SERVER_NAME_LENGTH + 1)])
def test_servers_reject_invalid_display_names(connection, owner_and_plan, name):
    owner, plan = owner_and_plan
    with pytest.raises(IntegrityError) as excinfo:
        insert_server(connection, owner, plan, name=name)
    assert_rejected(excinfo, "ck_game_servers_name_valid")


@pytest.mark.parametrize("container", ["-x", ".x", "a b", "a;b", "a/b", "", "a" * 65])
def test_servers_reject_invalid_container_names(connection, owner_and_plan, container):
    owner, plan = owner_and_plan
    with pytest.raises(IntegrityError) as excinfo:
        insert_server(connection, owner, plan, container=container)
    assert_rejected(excinfo, "ck_game_servers_container_name_format")


def test_servers_accept_the_longest_values_and_reject_duplicate_containers(
    connection, owner_and_plan
):
    owner, plan = owner_and_plan
    insert_server(connection, owner, plan, name="S" * MAX_SERVER_NAME_LENGTH, container="c" * 64)
    insert_server(connection, owner, plan, container="minecraft-dev")
    with pytest.raises(IntegrityError) as excinfo:
        insert_server(connection, owner, plan, container="minecraft-dev")
    assert_rejected(excinfo, "uq_game_servers_container_name")


def test_servers_need_an_existing_owner_and_plan(connection, owner_and_plan):
    owner, plan = owner_and_plan
    with pytest.raises(IntegrityError) as excinfo:
        insert_server(connection, uuid.uuid4(), plan)
    assert_rejected(excinfo, "fk_game_servers_owner_id_users")


def test_servers_need_an_existing_plan(connection, owner_and_plan):
    owner, _ = owner_and_plan
    with pytest.raises(IntegrityError) as excinfo:
        insert_server(connection, owner, uuid.uuid4())
    assert_rejected(excinfo, "fk_game_servers_plan_id_plans")


def test_a_user_with_servers_cannot_be_deleted(connection, owner_and_plan):
    owner, plan = owner_and_plan
    insert_server(connection, owner, plan)
    with pytest.raises(IntegrityError) as excinfo:
        connection.execute(text("DELETE FROM users WHERE id = :id"), {"id": owner})
    assert_rejected(excinfo, "fk_game_servers_owner_id_users")


def test_a_plan_with_servers_cannot_be_deleted(connection, owner_and_plan):
    owner, plan = owner_and_plan
    insert_server(connection, owner, plan)
    with pytest.raises(IntegrityError) as excinfo:
        connection.execute(text("DELETE FROM plans WHERE id = :id"), {"id": plan})
    assert_rejected(excinfo, "fk_game_servers_plan_id_plans")
