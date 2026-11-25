"""Alembic migrations against a real, empty PostgreSQL database."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from app.infrastructure.db.database import create_database_engine
from app.infrastructure.db.models import Base
from tests.support.migrations import alembic_config, downgrade, upgrade

pytestmark = pytest.mark.integration

APP_TABLES = {"users", "plans", "game_servers", "sessions", "login_failures"}
TABLES_BEFORE_0003 = {"users", "plans", "game_servers"}


@pytest.fixture
def engine(fresh_database_url):
    engine = create_database_engine(fresh_database_url)
    yield engine
    engine.dispose()


def table_names(engine):
    return set(inspect(engine).get_table_names()) - {"alembic_version"}


def current_revision(engine):
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


def head_revision():
    return ScriptDirectory.from_config(alembic_config()).get_current_head()


def test_a_fresh_database_has_no_application_tables(engine):
    assert table_names(engine) == set()
    assert current_revision(engine) is None


def test_upgrade_creates_the_schema_and_records_the_revision(engine):
    upgrade(engine)
    assert table_names(engine) == APP_TABLES
    assert current_revision(engine) == head_revision()


def test_upgrade_creates_the_expected_columns_keys_and_indexes(engine):
    upgrade(engine)
    inspector = inspect(engine)
    columns = {t: {c["name"]: c for c in inspector.get_columns(t)} for t in APP_TABLES}
    assert set(columns["users"]) == {
        "id", "email", "created_at", "role", "password_hash", "is_active",
    }
    assert set(columns["plans"]) == {"id", "slug", "name", "memory_mb", "cpu_millicores"}
    assert set(columns["game_servers"]) == {
        "id", "owner_id", "plan_id", "game", "name", "container_name", "created_at",
    }
    assert set(columns["sessions"]) == {
        "id", "user_id", "token_hash", "created_at", "last_seen_at", "expires_at", "revoked_at",
    }
    assert set(columns["login_failures"]) == {"id", "email_hash", "client_key", "occurred_at"}
    nullable = {
        (table, name) for table, cols in columns.items() for name, c in cols.items() if c["nullable"]
    }
    # The only optional columns: no password yet, and "not revoked".
    assert nullable == {("users", "password_hash"), ("sessions", "revoked_at")}
    assert inspector.get_pk_constraint("game_servers")["name"] == "pk_game_servers"
    foreign_keys = {fk["name"]: fk for fk in inspector.get_foreign_keys("game_servers")}
    assert set(foreign_keys) == {
        "fk_game_servers_owner_id_users",
        "fk_game_servers_plan_id_plans",
    }
    assert all(fk["options"].get("ondelete") == "RESTRICT" for fk in foreign_keys.values())
    uniques = {
        t: {u["name"] for u in inspector.get_unique_constraints(t)} for t in APP_TABLES
    }
    assert uniques == {
        "users": {"uq_users_email"},
        "plans": {"uq_plans_slug"},
        "game_servers": {"uq_game_servers_container_name"},
        "sessions": {"uq_sessions_token_hash"},
        "login_failures": set(),
    }
    assert "ix_game_servers_owner_id" in {i["name"] for i in inspector.get_indexes("game_servers")}
    # A unique constraint is backed by a unique index; list only the plain indexes.
    assert {i["name"] for i in inspector.get_indexes("sessions") if not i["unique"]} == {
        "ix_sessions_user_id_created_at", "ix_sessions_expires_at", "ix_sessions_revoked_at",
    }
    assert {i["name"] for i in inspector.get_indexes("login_failures")} == {
        "ix_login_failures_email_hash_client_key_occurred_at",
        "ix_login_failures_client_key_occurred_at",
        "ix_login_failures_occurred_at",
    }
    session_fk = inspector.get_foreign_keys("sessions")[0]
    assert session_fk["name"] == "fk_sessions_user_id_users"
    assert session_fk["options"].get("ondelete") == "CASCADE"
    assert {u["name"] for u in inspector.get_unique_constraints("sessions")} == {"uq_sessions_token_hash"}


def test_models_and_migrations_describe_the_same_schema(engine):
    upgrade(engine)
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        differences = compare_metadata(context, Base.metadata)
    assert differences == [], (
        "The SQLAlchemy models and the Alembic migrations disagree; add a new "
        f"migration revision: {differences}"
    )


def test_downgrade_removes_everything_the_upgrade_created(engine):
    upgrade(engine)
    downgrade(engine)
    assert table_names(engine) == set()
    assert current_revision(engine) is None


def test_upgrade_is_repeatable_after_a_downgrade(engine):
    upgrade(engine)
    downgrade(engine)
    upgrade(engine)
    assert table_names(engine) == APP_TABLES
    assert current_revision(engine) == head_revision()


def test_running_upgrade_twice_changes_nothing(engine):
    upgrade(engine)
    upgrade(engine)
    assert table_names(engine) == APP_TABLES
    assert current_revision(engine) == head_revision()


def test_the_users_account_columns_have_the_expected_nullability(engine):
    upgrade(engine)
    columns = {c["name"]: c for c in inspect(engine).get_columns("users")}
    assert columns["role"]["nullable"] is False
    assert columns["is_active"]["nullable"] is False
    assert columns["password_hash"]["nullable"] is True  # no password yet is a valid state


def test_upgrading_from_the_first_revision_keeps_existing_users_usable(engine):
    upgrade(engine, "0001")
    assert current_revision(engine) == "0001"
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, created_at) VALUES (gen_random_uuid(), 'old@example.com', now())")
        )
    upgrade(engine)
    with engine.connect() as connection:
        row = connection.execute(
            text("SELECT role, is_active, password_hash FROM users WHERE email = 'old@example.com'")
        ).one()
    assert (row.role, row.is_active, row.password_hash) == ("user", True, None)


def test_downgrading_to_the_first_revision_drops_only_the_account_columns(engine):
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, created_at, role) VALUES (gen_random_uuid(), 'boss@example.com', now(), 'admin')")
        )
    downgrade(engine, "0001")
    assert current_revision(engine) == "0001"
    columns = {c["name"] for c in inspect(engine).get_columns("users")}
    assert columns == {"id", "email", "created_at"}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    upgrade(engine)  # and it can be applied again
    assert current_revision(engine) == head_revision()


def test_the_users_unique_constraints_list_is_unchanged_by_the_later_revisions(engine):
    upgrade(engine)
    uniques = {u["name"] for u in inspect(engine).get_unique_constraints("users")}
    assert uniques == {"uq_users_email"}


def test_upgrading_from_the_second_revision_adds_only_the_new_tables_and_keeps_data(engine):
    upgrade(engine, "0002")
    assert current_revision(engine) == "0002"
    assert table_names(engine) == TABLES_BEFORE_0003
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, created_at, role) VALUES (gen_random_uuid(), 'boss@example.com', now(), 'admin')")
        )
    upgrade(engine)
    assert table_names(engine) == APP_TABLES
    with engine.connect() as connection:
        row = connection.execute(text("SELECT role FROM users")).one()
        assert row.role == "admin"
        assert connection.execute(text("SELECT count(*) FROM sessions")).scalar_one() == 0


def test_downgrading_to_the_second_revision_drops_only_the_new_tables(engine):
    upgrade(engine)
    with engine.begin() as connection:
        connection.execute(
            text("INSERT INTO users (id, email, created_at) VALUES (gen_random_uuid(), 'ana@example.com', now())")
        )
    downgrade(engine, "0002")
    assert current_revision(engine) == "0002"
    assert table_names(engine) == TABLES_BEFORE_0003
    with engine.connect() as connection:
        assert connection.execute(text("SELECT count(*) FROM users")).scalar_one() == 1
    upgrade(engine)
    assert current_revision(engine) == head_revision()
