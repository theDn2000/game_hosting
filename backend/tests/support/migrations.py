"""Run the project's Alembic migrations against a given engine in tests."""

from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = BACKEND_DIR / "alembic.ini"


def alembic_config(engine=None):
    config = Config(str(ALEMBIC_INI))
    if engine is not None:
        config.attributes["engine"] = engine
    return config


def upgrade(engine, revision="head"):
    command.upgrade(alembic_config(engine), revision)


def downgrade(engine, revision="base"):
    command.downgrade(alembic_config(engine), revision)
