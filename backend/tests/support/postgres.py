"""Constants and helpers shared by the tests that use a throwaway PostgreSQL."""

# Same major version as docker-compose.yml.
POSTGRES_IMAGE = "postgres:18"
POSTGRES_USER = "gh_test"
POSTGRES_DB = "gh_test"
POSTGRES_STARTUP_TIMEOUT = 90


def build_postgres_url(password, port, database=POSTGRES_DB):
    return (
        f"postgresql+psycopg://{POSTGRES_USER}:{password}"
        f"@127.0.0.1:{port}/{database}"
    )
