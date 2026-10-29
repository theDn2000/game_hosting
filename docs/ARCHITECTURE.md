# Architecture

> This document records the verified current architecture and a deliberately lightweight target design. Keep it aligned with the implementation. Label assumptions and plans as such; do not present planned components as existing.

## 1. Product Context

`game_hosting` is intended to become a commercial game-server hosting platform, starting with Minecraft Java Edition. The platform should eventually manage customer accounts, servers, plans/subscriptions, provisioning, server lifecycle, persistent worlds, backups, monitoring, and multiple execution nodes.

The guiding principle is **fast MVP, non-disposable architecture**: introduce useful boundaries early, but do not build infrastructure before it is needed.

## 2. Current Architecture — Verify Against Repository

### Local development environment

- Windows 11 with WSL2.
- Docker Desktop using the WSL2 backend.
- Python 3.14.7 virtual environment at `backend/.venv/` (not at the repository root). The suite also passes on Python 3.12.15 (checked on Windows with a temporary environment on 2026-10-20), which is the default `python3` of Ubuntu 24.04. A regression test (`tests/test_portability.py`) guards against code that only works on 3.14.
- Python Docker SDK (`docker==7.2.0`) installed in that environment.

### Minecraft runtime

The root `docker-compose.yml` defines a Minecraft Java server (verified with `docker compose config`): image `itzg/minecraft-server:latest`, service name `minecraft`, container name `minecraft-dev`, port `25565:25565`, `EULA=TRUE`, `TYPE=VANILLA`, `VERSION=LATEST`, `MEMORY=4G`, `restart: unless-stopped`, persistent host directory `./data` mounted at `/data`, and the label `game-hosting.managed: "true"` (added 2026-10-20; it marks the container as platform-managed, see `DockerClient`).

- The Compose project name is `game_hosting` (derived from the directory).
- The container is replaceable; the `./data` directory holds persistent server state.
- `data/` and `backend/.venv/` must remain untracked; both are listed in the root `.gitignore` (verified with `git check-ignore`).
- Verify the current Compose file and `.gitignore` before changing them.
- `VERSION=LATEST` with the `latest` image tag is not reproducible; accepted during development, to be pinned before production.
- Compose lifecycle verified on 2026-10-14: up, stop, down and re-up preserve `./data/world`.

### Python controller — implementation in progress

The intended structure is:

```text
backend/
  app/
    main.py
    services/
      server_service.py
    infrastructure/
      docker_client.py
  cli.py
  requirements.txt
```

The intended responsibilities are:

- `app/main.py`: current development entry point; should remain thin and should not become the home for all application logic.
- `app/services/server_service.py`: application-level server operations and orchestration; should not depend on Docker SDK exception types or Docker objects more than necessary.
- `app/infrastructure/docker_client.py`: Docker SDK integration, container lookup and low-level container operations; translates infrastructure errors into outcomes meaningful to the application.
- `cli.py`: development/test interface. It should call application services rather than duplicate business logic.
- `requirements.txt`: reproducible list of Python dependencies; verify it reflects the environment and does not include accidental tooling.

**Implemented as of 2026-10-15 (verified by tests and a real-Docker smoke check):**

- `app/errors.py`: application errors `ServerNotFoundError`, `RuntimeUnavailableError` and `ServerOperationError` (base `GameHostingError`). No Docker types.
- `DockerClient`: `get_status`, `start_container`, `stop_container(timeout)`, `restart_container(timeout)` and `get_logs(tail)`. The Docker connection is created lazily on first use; a fake SDK client can be injected for tests. All Docker SDK and `requests` exceptions are translated into the application errors above and never leave this class. `get_status` returns Docker's raw status string and raises `ServerNotFoundError` for a missing container. **Only containers labelled `game-hosting.managed=true` are visible** (since 2026-10-20): any other container (no label, other value) is reported as not found by every operation and is never started, stopped, restarted or read, so neither the API nor the CLI can control containers that do not belong to the platform. The label marks platform management, not ownership; customer ownership will live in the database.
- `ServerService` (implemented 2026-10-15): `get_server_status`, `start_server`, `stop_server(timeout)`, `restart_server(timeout)` and `get_server_logs(tail)`. Status is a `ServerStatus` `StrEnum` (Docker's statuses plus `NOT_FOUND` and `UNKNOWN` for unrecognised values). Querying the status of a missing server returns `NOT_FOUND`; every other operation raises `ServerNotFoundError`. `start` and `stop` are idempotent (no Docker call when already running / not running) and all lifecycle operations return the final status. Defaults: 30 s stop/restart timeout, 100 log lines; `tail < 1` raises `ValueError`. Its `DockerClient` is a required constructor argument: the entry point (CLI, future API) builds the adapter and injects it, so this module imports `DockerClient` only for type checking (`TYPE_CHECKING`), never at runtime. This keeps the service independent of the concrete runtime for a future Linux / multi-node deployment. `server_name` is still the container name until Phase 3.
- **Database infrastructure (Phase 3 step 1, 2026-10-29):** `docker-compose.yml` has a `postgres:18` service (container `game-hosting-db`, named volume `postgres-data` mounted at `/var/lib/postgresql` as PostgreSQL 18 images require, published only on `127.0.0.1:${POSTGRES_PORT:-5432}`, healthcheck with `pg_isready`, `no-new-privileges`). It deliberately has **no** `game-hosting.managed` label, so the API and CLI cannot see or stop it (verified: the CLI reports it as not found and it keeps running). Credentials come from `.env` (git-ignored; `.env.example` documents it); Compose now requires `POSTGRES_PASSWORD`, even to start only Minecraft. `app/infrastructure/db/database.py` is the only module that knows SQLAlchemy and psycopg: `create_database_engine(url, connect_timeout=5)` accepts only `postgresql+psycopg://` URLs, does not connect on creation and never echoes URLs or passwords in errors; `create_session_factory(engine)` (`expire_on_commit=False`); `check_connection(engine)` raises `DatabaseUnavailableError` (new application error) with a redacted URL; `redact_url`. The database URL is read by the process that will use it from `GAME_HOSTING_DATABASE_URL` (wired to the API entry point in step 4; not used by the API yet). Dependencies added: `SQLAlchemy 2.1.6`, `psycopg 3.3.6` with `psycopg-binary` (adequate for now; a source build against libpq is the usual choice for production), and `tzdata` on Windows only. Alembic 1.20.1 (with Mako and MarkupSafe) was added in step 3.
- `app/api/app.py` (Phase 2, steps 1-2, 2026-10-20): `create_app(service)` builds a FastAPI app with the service injected (`app.state.service`). Routes are synchronous `def` functions (the Docker SDK blocks; FastAPI runs them in a thread pool) and contain no business logic. Endpoints: `GET /health` (liveness, never calls Docker), `GET /servers/{name}` (200 with the status, `not_found` included), `POST /servers/{name}/start`, `POST /servers/{name}/stop?timeout=`, `POST /servers/{name}/restart?timeout=` (200 with the final status) and `GET /servers/{name}/logs?tail=` (plain text). Validation happens before the service is called: `name` must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`, `timeout` 1-300 (default 30), `tail` 1-1000 (default 100); violations return 422. Error mapping: `ServerNotFoundError` 404, `RuntimeUnavailableError` 503, `ServerOperationError` and other `GameHostingError` 500, anything unexpected a generic 500. Response bodies are generic; the underlying message is logged on the `game_hosting.api` logger (503 as one line, 500 with traceback, 404 at info). The endpoints are `POST` for state changes, so `GET` on them returns 405. **Security (step 3, 2026-10-20):** every `/servers` route requires `Authorization: Bearer <token>`; a missing or wrong token gets 401 (generic body, `WWW-Authenticate: Bearer`), compared with `secrets.compare_digest`. Authentication runs before parameter validation, so unauthenticated callers learn nothing. `/health` is open. `create_app(service, api_token, enable_docs=False)` raises `ValueError` if the token is missing or shorter than 16 characters. `/docs`, `/redoc` and `/openapi.json` are disabled unless `enable_docs=True`, and enabling them does not unprotect the routes. Rejected requests are logged at WARNING with the method and the escaped (`%r`) path, never the token (Starlette already strips newline, carriage return and tab from paths; `%r` covers other control characters such as ANSI escapes). The token is a single shared secret meant to stop hostile web pages from reaching a localhost API (a custom header forces a CORS preflight that the API never grants). It is **not** user authentication, has no rate limiting or lockout, and is to be replaced by real authentication. The API must still only be bound to `127.0.0.1`.
- `app/api/entrypoint.py` (2026-10-20): `build_app(environ=None)` is the API's composition root. It reads `GAME_HOSTING_API_TOKEN` (required, at least 16 characters) and `GAME_HOSTING_API_DOCS` (`1` or `true` enables the docs), builds `ServerService(DockerClient())` (Docker is not needed at startup) and returns the app. It fails closed: without a valid token it raises `ConfigurationError` (the message explains how to generate a token and never echoes the rejected value) and the process exits with code 1. Launch from `backend/`: `python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1`. Verified with real uvicorn processes. It also configures logging (`configure_logging`, idempotent): `game_hosting.*` loggers write to stderr as `time level logger: message`; `GAME_HOSTING_LOG_LEVEL` (`DEBUG`, `INFO`, `WARNING`, `ERROR`, default `INFO`) sets the level and an invalid value fails closed with `ConfigurationError`. At startup it logs whether the docs are enabled, with a WARNING when they are. The token is never logged. Startup and usage instructions are in `docs/RUNNING.md`.
- `app/main.py`: a throwaway script that runs at import time (no `if __name__ == "__main__"` guard), builds `ServerService(DockerClient())` and prints the status of `minecraft-dev`. Kept on purpose as a simple entry point.
- `cli.py` (implemented 2026-10-15): `python cli.py [--server NAME] status | start | stop [--timeout S] | restart [--timeout S] | logs [--tail N]`, default server `minecraft-dev`, run from `backend/` (it also works when invoked by path from elsewhere). It is the composition root: `build_service()` creates `ServerService(DockerClient())`, and `main(argv, service)` accepts an injected service for tests. It contains no business logic; it prints `<server>: <status>` (or the raw logs), and turns application errors into `error: <message>` on stderr plus an exit code: 0 ok, 1 other application error, 2 invalid usage (argparse), 3 server not found, 4 Docker unavailable, 5 operation failed. `status` of a missing server prints `not_found` and exits 0. Unexpected exceptions are deliberately not caught. `--timeout` and `--tail` must be >= 1.
- Tests (default run, no Docker needed): 354 pytest tests. 73 for the domain models (`backend/tests/domain/`); 28 repository contract tests run against the in-memory implementation (`backend/tests/repositories/`); 13 for connection failures of the SQL repositories (instantaneous, using an engine whose connections are refused); 6 static checks of the Alembic setup (single head, linear history, every revision has a real downgrade, no URL or secret in `alembic.ini` or `env.py`, clear failure without the URL); 12 portability checks; plus the 230 from earlier phases (API, CLI, services, Docker client, database connection layer).
- Integration tests (`pytest -m integration`, deselected by default, need a running Docker Engine): 95 tests. 28 are the repository contract tests run against PostgreSQL (the same tests as the in-memory run); 7 for the migrations (fresh database, upgrade, downgrade, repeatability, idempotence, columns/keys/indexes, and models-versus-migrations equality through `compare_metadata`); 38 for the schema constraints (every CHECK, unique, foreign key and RESTRICT rule, with boundary values taken from the domain constants, since Alembic does not compare CHECK constraints); 5 specific to the SQL repositories; 5 for the database connection; and the 12 Docker/CLI/HTTP tests from before: those use throwaway `alpine:3` containers and a real uvicorn subprocess against a unique name that is force-removed, never `minecraft-dev` or `./data`. Everything PostgreSQL-related runs on a throwaway `postgres:18` container (random password, random localhost port, removed with its volume) holding one empty database per test or one shared migrated database that tests empty or roll back. Running them without Docker fails loudly. Dev dependencies live in `backend/requirements-dev.txt`.

**Known gaps and risks:**

- Integration tests use an `alpine` throwaway container, not the Minecraft image, so they say nothing about Minecraft itself. That was checked by hand on 2026-10-15 instead: `cli.py stop` on `minecraft-dev` shut it down in about 1 s, the log shows "Stopping server" / "Saving players" / "Saving worlds", and the container exited with code 0.
- Verified on 2026-10-15 with Docker Desktop fully quit: all five CLI commands exit with code 4 and print `error: Cannot reach Docker ...` with no traceback (`app/main.py` still shows a traceback, as it has no error handling). The Docker error text is embedded as-is, so on a localised Windows it appears in the OS language.
- The CLI hard-codes the default server name `minecraft-dev` (acceptable while Minecraft is the only game; revisit in Phase 3).
- `ServerService.restart_server` does not check the server's existence first; a missing server surfaces through `DockerClient` as `ServerNotFoundError`, which is the same outcome.
- `backend/requirements.txt` is a pinned `pip freeze` of seven packages that matches the installed environment. It is now UTF-8 and `pywin32==312` carries the marker `sys_platform == "win32"` (it is a Windows-only transitive dependency of `docker`, not accidental tooling), so Linux skips it. A real Linux install was tested on 2026-10-29 (Ubuntu 24.04, Python 3.12.3): it installs cleanly and `pip check` passes. The file still pins transitive dependencies; the long-term strategy is an open decision below.
- **Linux verification (2026-10-29, see `docs/LINUX_CHECKPOINT.md`):** the full suite (215 default and 12 integration tests), the CLI, the API (as an unprivileged user), Compose with the managed label, world persistence, graceful stop and cgroup limits all work on native Linux (Ubuntu 24.04 under WSL2 with its own Docker Engine 29.9.2). Linux facts to design for: the itzg image owns `./data` as uid/gid 1000 and `level.dat` is mode 600 (backups must run as uid 1000, as root or inside the container); membership of the `docker` group is root-equivalent (use a dedicated unprivileged API user and keep the Docker socket off the network). Not verified: a real server (network exposure, performance, provider specifics).
- The root `.gitignore` lists `data/`, `.venv/`, `__pycache__/` and `*.pyc`, and the previously tracked `.pyc` files were removed from the index.
- `README.md` (Spanish) describes `DockerClient` and `ServerService` with start/stop/restart/logs methods and a CLI; those are planned, not implemented.

**Important:** The controller has been under active development. Inspect actual files before assuming which lifecycle methods, error handling, or tests are present.

## 3. Dependency Rules

Target dependency direction:

```text
CLI / FastAPI routes
        |
        v
Application services (e.g. ServerService)
        |
        v
Infrastructure interfaces/adapters (e.g. DockerClient)
        |
        v
Docker SDK / Docker Engine
```

Rules:

- Interfaces (CLI or future HTTP API) call application services.
- Application services express application behavior and should not expose Docker SDK objects as public return types.
- Docker-specific implementation details and exceptions belong in the infrastructure layer, unless deliberately translated into application-level errors.
- Keep entry points thin.
- Avoid introducing abstractions with no present use. Add a provider interface when it materially protects application logic from infrastructure details, not merely for hypothetical flexibility.
- The frontend must call the backend API; it must never access Docker directly.

## 4. Target Architecture — Incremental, Not Yet Implemented

```text
Browser
  |
  | HTTPS
  v
Frontend (React / Next.js)
  |
  | authenticated API
  v
Backend API (Python / FastAPI)
  |
  v
Application services
  |----------------------|
  v                      v
Persistence adapter   Compute/provider adapter
  |                      |
  v                      v
PostgreSQL          Local Docker initially
                         |
                         v
                  Minecraft containers
```

Later, if operational needs justify it, the compute adapter can communicate with a controlled node agent on remote hosts. Do not expose Docker Engine directly to the internet. Any future node-agent communication must be authenticated, authorized, encrypted in transit, and restricted to the required operations.

### Deployment topology (decision record, 2026-10-29)

Two roles exist, and today they share one machine:

```text
 CONTROL PLANE (the "brain")                          NODES (the "workers")
 Web panel, API, PostgreSQL,                          Node 1: Docker + game containers
 server/user registry, billing, scheduler   ----->    Node 2: Docker + game containers
 one instance, one machine                            Node N: as many as needed
```

- **Now / MVP:** everything runs on one machine (API, PostgreSQL, game containers). This is fine for the first customers; the limit is RAM and CPU (a Minecraft server needs roughly 2-4 GB).
- **Growing:** add nodes. The control plane stays single. `ServerService` already works against an injected `DockerClient`, so a future remote-node client replaces the local one without changing the service. Needed in Phase 6: a `Node` table, a scheduler that places servers on nodes with free capacity (no oversubscription), and an authenticated **node agent** on each node that exposes only the required operations (never Docker's own API on the network). Server world data lives on its node's disk, so moving a server between nodes needs backup and restore.
- **Database:** central; nodes never connect to it.
- **When to split control plane and nodes:** before the machine saturates, and before opening to paying customers: a database or API failure should not take games down (and the reverse), and the control plane (credentials, payment data) should not share a machine with the game ports that attract attacks. Suggested launch shape: two machines, a small one for the control plane and a powerful one for games. Decide in Phases 6-7.
- **Scope of "management":** the platform manages the whole server lifecycle (provisioning, start/stop/restart, monitoring, backups and restore, plans and billing, suspension), not just deployment. Provisioning the machines themselves (installing Linux, Docker, firewall) is manual and documented for the first node (see `docs/LINUX_CHECKPOINT.md`); infrastructure-as-code is worth considering once there are more than two or three nodes and is not on the roadmap yet.

## 5. Domain Model

### Implemented (Phase 3 step 2, 2026-10-29)

`app/domain/models.py` holds immutable (`frozen`, `slots`) dataclasses that validate themselves on construction and raise `InvalidDataError`, so an invalid entity cannot exist whether created by the application or loaded from storage. Create them with the `new` factories (UUID v4 ids, timezone-aware UTC timestamps, normalised values). They know nothing about Docker, HTTP or the database.

| Entity | Fields | Rules |
|---|---|---|
| `User` | `id` UUID, `email`, `created_at` | E-mail is stored trimmed and lowercase (checked strictly on construction), at most 254 characters, simple `local@domain.tld` shape. No credentials: authentication is Phase 4. |
| `Plan` | `id` UUID, `slug`, `name`, `memory_mb`, `cpu_millicores` | Slug lowercase letters, digits and dashes (at most 63 characters, unique). `memory_mb` and `cpu_millicores` (1000 = one core) are whole numbers in range; `bool` is rejected. Enforcement of the limits is Phase 5. |
| `GameServer` | `id` UUID, `owner_id`, `plan_id`, `game` (`Game` enum), `name`, `container_name`, `created_at` | `id` is the customer-facing identifier. `container_name` is internal, unique and valid as a Docker name; `GameServer.new` generates `gh-<uuid hex>` unless an existing container is adopted by passing its name. Display `name` is 1-64 characters without control characters. Runtime status is not stored (Docker is its source of truth). |

### PostgreSQL implementation (Phase 3 step 3, 2026-10-29)

- **Schema** (`app/infrastructure/db/models.py`, mirrored by the Alembic migration `0001`): `users(id uuid PK, email text unique, created_at timestamptz)`, `plans(id, slug unique, name, memory_mb, cpu_millicores)`, `game_servers(id, owner_id FK, plan_id FK, game text, name, container_name unique, created_at)` with an index on `owner_id`. Foreign keys are `ON DELETE RESTRICT`: a user or plan that still has servers cannot be deleted. CHECK constraints repeat the domain's rules (lowercase and bounded e-mail, slug and container-name formats, name length and no surrounding spaces, memory and CPU ranges), so data written by anything else still cannot break them. The `game` column has no CHECK on purpose: adding a game is a code change, and an unknown stored value is reported as `InvalidDataError` when read.
- **Naming convention** (`ix_`, `uq_`, `ck_`, `fk_`, `pk_` prefixes plus table and column names) is fixed in the metadata, because the repositories translate violations by constraint name and migrations must produce identical names.
- **Repositories** (`app/infrastructure/db/repositories.py`): rows are converted to and from the domain dataclasses and nothing SQLAlchemy-specific leaves the package. Inserts use Core `INSERT` inside a SAVEPOINT (`begin_nested`), so a rejected insert leaves the surrounding transaction usable, as in the in-memory reference. Errors: unique violation -> `AlreadyExistsError` (entity and field taken from the constraint name), foreign-key violation -> `UserNotFoundError` / `PlanNotFoundError`, CHECK violation -> `InvalidDataError` (naming the constraint, never the values), connection failure -> `DatabaseUnavailableError` with a redacted URL; an unrecognised integrity error is re-raised unchanged. `SqlUnitOfWork` opens a session on `__enter__`, commits only on an explicit `commit()`, rolls back and closes on exit (and never lets a dead connection hide the original exception). There are deliberately no update or delete operations yet (deletion policy belongs to Phases 5 and 7).
- **Migrations** (`backend/migrations/`, `backend/alembic.ini`): the URL comes only from `GAME_HOSTING_DATABASE_URL` (the ini contains none; `env.py` fails with a clear message if it is unset; offline `--sql` mode is not supported). Migrations are hand-written for explicit names and constraints, are applied explicitly (`python -m alembic upgrade head` from `backend/`) and never on API startup, and an applied revision is never edited: add a new one. `alembic check` reports no difference between the models and the database. Alembic does not compare CHECK constraints, which is why `tests/integration/test_schema_constraints.py` exercises each one.

Persistence interfaces live in `app/services/repositories.py` as `typing.Protocol`s: `UserRepository`, `PlanRepository`, `GameServerRepository` and `UnitOfWork` (a context manager exposing the three repositories; changes become permanent only on `commit()`, and leaving the block without committing, or with an exception, discards them). Error contract: missing entities raise `UserNotFoundError`, `PlanNotFoundError` or `ServerNotFoundError` (platform server id); a duplicate e-mail, plan slug or container name raises `AlreadyExistsError`; adding a server with an unknown owner or plan raises the corresponding not-found error; list operations are ordered by creation time, then id. `tests/support/in_memory.py` is the reference implementation, and `tests/repositories/test_repository_contract.py` is written against the interfaces, so the PostgreSQL implementation (step 3) passes the very same tests (every contract test runs against both). The registry service that resolves a platform server id to its container, and the API changes that expose platform ids, do not exist yet (step 4).

### Still to come (sketch)

This remainder is a conceptual starting point, not an implemented schema.

- **User**: customer identity and account state. Authentication details should be handled safely; never store plaintext passwords.
- **GameServer**: platform-owned stable ID, owner/user ID, server type/version, lifecycle state, assigned node, resource allocation, connection endpoint, and references to persistent data. Do not make a Docker container name the platform's permanent business identifier.
- **Plan**: product tier and allowed resources/features.
- **Subscription**: billing provider identifiers, plan, status, and relevant billing lifecycle metadata. Do not store payment card details.
- **Node**: execution host identity, capacity, health, and scheduling/availability metadata. Avoid storing secrets in ordinary fields or logs.
- **ServerOperation / AuditEvent** (later, if needed): record important actions, outcomes, and actor identity for troubleshooting and accountability.
- **Backup** (later): server ID, storage location reference, timestamps, size, and verification/retention state.

Use stable internal IDs and explicit relationships. Introduce only the entities and fields required by the next phase; avoid speculative full schemas.

## 6. Security and Operational Constraints

- Never expose the Docker socket or unauthenticated Docker API publicly.
- Treat Docker control as a privileged capability. Future API endpoints must authenticate the user and authorize ownership/access before operating on a server.
- Validate user-supplied server identifiers and configuration. Do not interpolate untrusted input into shell commands.
- Apply container resource limits, port allocation controls, filesystem boundaries, and network policies when provisioning is introduced.
- Keep persistent world data separate from container lifecycle. Routine start/stop/recreate operations must not delete customer data.
- Keep credentials and secrets out of source control, API responses, and logs.
- Use least privilege for runtime processes and node communication where practical.
- Plan for backups and restore verification before claiming customer data is protected.
- Consider hostile traffic and DDoS exposure before opening a service to the public internet.

## 7. Extension Points

Potential extension points, introduced only when needed:

- `ServerService`: application operations shared by CLI and API.
- Docker infrastructure adapter: isolates Docker SDK details from application behavior.
- Compute provider / node client: later seam for local Docker versus remote node-agent execution.
- Persistence repository: later seam for PostgreSQL-backed server/user/plan records.
- Server configuration/provisioning layer: later separation between validated desired configuration and container creation.

Do not create empty interfaces or layers solely to match this sketch.

## 8. Decisions and Status

| Decision | Status / rationale |
|---|---|
| Start with Minecraft Java | Agreed; limits the initial product scope. |
| Use Python for backend and automation | Agreed; suitable for service logic and infrastructure integration. |
| Use Docker for initial execution | Agreed; local Compose setup is the first runtime environment. |
| Keep persistent server data outside the container | Agreed and represented by the `./data:/data` bind mount. |
| Use FastAPI for the HTTP API | Agreed; skeleton and `/health` implemented 2026-10-15 (`fastapi==0.143.2`, `uvicorn==0.54.0`, plain `uvicorn` without extras). Test client: `httpx2` (Starlette deprecated `httpx`). |
| Only label-marked containers are manageable | Decided 2026-10-15, implemented 2026-10-20: containers need `game-hosting.managed=true`, otherwise they are treated as not found, so the API and CLI cannot stop unrelated containers. `minecraft-dev` was recreated with the label (`./data` untouched). Ownership by customer is a separate, later concern stored in the database (see roadmap open questions). |
| Temporary shared token for the API | Decided 2026-10-15, implemented 2026-10-20: bearer token from `GAME_HOSTING_API_TOKEN`, and the API refuses to start without it. Guards a localhost API against hostile web pages; to be replaced by real authentication. |
| Persistence stack | Decided 2026-10-29, implemented in Phase 3 steps 1-3: PostgreSQL 18 (latest major with published images, pinned by major), SQLAlchemy 2.1 + psycopg 3, and Alembic 1.20 for migrations (run explicitly with the CLI, never on API startup; `alembic.ini` holds no URL). Domain objects are plain dataclasses separate from the ORM models; services depend on repository interfaces, never on SQLAlchemy. |
| Platform identity vs container name | Decided 2026-10-29: every `GameServer` has a UUID id exposed to customers; the container name is an internal, unique, generated value. API routes move to `/servers/{server_id}` in Phase 3 step 4. Containers will carry a `game-hosting.server-id` label. |
| Ownership storage and enforcement | Decided 2026-10-29: the database is the source of truth for ownership (not container labels); it is stored in Phase 3 and enforced in Phase 4 when users can authenticate. Until then the shared API token acts as the operator. |
| `Node` table deferred | Decided 2026-10-29: added in Phase 6 (see the deployment topology above); a later migration backfills a "local" node. |
| Interactive API docs disabled by default | Decided and implemented 2026-10-20: `/docs`, `/redoc` and `/openapi.json` are off unless `GAME_HOSTING_API_DOCS=1` (or `true`). |
| Use PostgreSQL for platform data | Target decision; introduce when domain persistence is needed. |
| Use React/Next.js for frontend | Target direction; not assumed to be implemented. |
| Keep frontend and Docker separated by backend services | Hard architectural rule. |
| Avoid Kubernetes and unnecessary infrastructure initially | Agreed; introduce only with demonstrated need. |
| Dependency pinning strategy | **Open.** `pywin32` now has a Windows-only marker. Still to decide: keep the full freeze, or list direct dependencies only and add a lock file, before the first Linux deployment. |
| Pin the Minecraft image and server version | Decided 2026-10-14: keep `latest` for both the image and `VERSION` during development; pin them before any production use. |

Update this section when a decision changes. Record consequential new decisions and their rationale.
