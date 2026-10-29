"""Static checks of the migration setup; no database needed."""

import re

import pytest
from alembic.script import ScriptDirectory

from tests.support.migrations import ALEMBIC_INI, BACKEND_DIR, alembic_config


@pytest.fixture(scope="module")
def script_directory():
    return ScriptDirectory.from_config(alembic_config())


def test_there_is_exactly_one_head(script_directory):
    assert len(script_directory.get_heads()) == 1


def test_the_history_is_linear_and_starts_at_the_base(script_directory):
    revisions = list(script_directory.walk_revisions())
    assert len(revisions) >= 1
    assert sum(1 for r in revisions if r.down_revision is None) == 1
    assert all(not isinstance(r.down_revision, tuple) for r in revisions)  # no merges


def test_every_revision_can_be_downgraded(script_directory):
    for revision in script_directory.walk_revisions():
        module_source = open(revision.path, encoding="utf-8").read()
        assert re.search(r"^def downgrade\(\):\n(?!\s*pass\n)", module_source, re.M), revision.path


def test_the_ini_file_contains_no_database_url_or_secret():
    content = ALEMBIC_INI.read_text(encoding="utf-8").lower()
    for forbidden in ("postgresql", "password", "sqlalchemy.url", "://"):
        assert forbidden not in content, forbidden


def test_the_migration_environment_reads_the_url_from_the_environment_only():
    env = (BACKEND_DIR / "migrations" / "env.py").read_text(encoding="utf-8")
    assert "DATABASE_URL_VARIABLE" in env  # the name of the environment variable
    assert "os.environ" in env
    # No URL with credentials is hard-coded (the only URL text is a placeholder example).
    credentials = re.findall(r"postgresql\+psycopg://\S+@", env)
    assert credentials == ["postgresql+psycopg://user:password@"]


def test_the_migration_environment_fails_clearly_without_a_url(monkeypatch):
    from alembic import command

    monkeypatch.delenv("GAME_HOSTING_DATABASE_URL", raising=False)
    with pytest.raises(RuntimeError) as excinfo:
        command.upgrade(alembic_config(), "head")
    assert "GAME_HOSTING_DATABASE_URL" in str(excinfo.value)
