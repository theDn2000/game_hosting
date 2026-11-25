"""Composition root shared by the entry points (HTTP API and CLI).

This is the one place that wires the real infrastructure (PostgreSQL through
SQLAlchemy, Docker) into the application services. The services themselves
never import it, which keeps them testable with fakes.
"""

from dataclasses import dataclass

from sqlalchemy.engine import Engine

from app.infrastructure.argon2_hasher import Argon2PasswordHasher
from app.infrastructure.db.database import create_database_engine, create_session_factory
from app.infrastructure.db.repositories import SqlUnitOfWork
from app.infrastructure.docker_client import DockerClient
from app.services.auth import AuthService
from app.services.plan_catalog import PlanCatalog
from app.services.server_registry import ServerRegistry
from app.services.server_service import ServerService
from app.services.user_accounts import UserAccounts


@dataclass(frozen=True)
class Services:
    engine: Engine
    registry: ServerRegistry
    plans: PlanCatalog
    accounts: UserAccounts
    auth: AuthService

    def close(self):
        """Release the database connections."""
        self.engine.dispose()


def build_services(database_url, docker_client=None):
    """Build the real services. Nothing connects here: the database and Docker
    are contacted on first use. Raises ValueError for an unusable database URL
    (the message never contains the URL)."""
    engine = create_database_engine(database_url)
    session_factory = create_session_factory(engine)

    def uow_factory():
        return SqlUnitOfWork(session_factory)

    server_service = ServerService(docker_client or DockerClient())
    password_hasher = Argon2PasswordHasher()
    return Services(
        engine=engine,
        registry=ServerRegistry(uow_factory, server_service),
        plans=PlanCatalog(uow_factory),
        accounts=UserAccounts(uow_factory, password_hasher),
        auth=AuthService(uow_factory, password_hasher),
    )
