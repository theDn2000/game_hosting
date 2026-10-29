"""Alembic migrations against a real, empty PostgreSQL database."""

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import inspect

from app.infrastructure.db.database import create_database_engine
from app.infrastructure.db.models import Base
from tests.support.migrations import alembic_config, downgrade, upgrade

pytestmark = pytest.mark.integration

APP_TABLES = {"users", "plans", "game_servers"}


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
    assert set(columns["users"]) == {"id", "email", "created_at"}
    assert set(columns["plans"]) == {"id", "slug", "name", "memory_mb", "cpu_millicores"}
    assert set(columns["game_servers"]) == {
        "id", "owner_id", "plan_id", "game", "name", "container_name", "created_at",
    }
    assert all(not column["nullable"] for table in columns.values() for column in table.values())
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
    }
    assert "ix_game_servers_owner_id" in {i["name"] for i in inspector.get_indexes("game_servers")}


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
