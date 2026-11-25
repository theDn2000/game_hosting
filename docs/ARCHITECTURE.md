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
- `app/api/app.py` (Phase 2; reworked in Phase 3 step 4, 2026-11-04): `create_app(registry, api_token, enable_docs=False, lifespan=None)` builds a FastAPI app over a `ServerRegistry`. Servers are addressed by their platform **UUID**; the old container-name addressing is gone (an old-style name now gets 422). Routes are synchronous `def` functions (the Docker SDK and the database drivers block; FastAPI runs them in a thread pool) and contain no business logic. Endpoints: `GET /health` (liveness, touches neither Docker nor the database), `GET /servers` (list, oldest first), `GET /servers/{server_id}`, `POST /servers/{server_id}/start`, `.../stop?timeout=`, `.../restart?timeout=` (200 with the final status) and `GET /servers/{server_id}/logs?tail=` (plain text). Responses show only `id`, `name`, `game` and `status`: **never the container name or the owner's details** (tests assert it on every route). `timeout` is 1-300 (default 30), `tail` 1-1000 (default 100); invalid values and non-UUID ids return 422 before the registry is called, and equivalent UUID spellings (uppercase, without hyphens) reach it as the same id. Error mapping: `ServerNotFoundError` 404, `RuntimeUnavailableError` 503, `DatabaseUnavailableError` 503 ("The platform database is unavailable."), `ServerOperationError` and other `GameHostingError` (including `InvalidDataError` from corrupt stored data) 500, anything unexpected a generic 500. Bodies are generic; the underlying message is logged on `game_hosting.api` (503 as one line, 500 with traceback, 404 at info). **Security (unchanged semantics):** every `/servers` route requires `Authorization: Bearer <token>`; a missing or wrong token gets 401 (generic body, `WWW-Authenticate: Bearer`), compared with `secrets.compare_digest`, and authentication runs before parameter validation. `/health` is open. `create_app` raises `ValueError` if the token is missing or shorter than 16 characters. `/docs`, `/redoc` and `/openapi.json` are disabled unless `enable_docs=True`, and enabling them does not unprotect the routes. Rejected requests are logged at WARNING with the method and the escaped (`%r`) path, never the token. The token is a single shared secret meant to stop hostile web pages from reaching a localhost API; it is **not** user authentication, has no rate limiting, and **acts as the operator: it may act on every registered server**. Ownership is stored in the database but not enforced until users can authenticate (Phase 4). The API must still only be bound to `127.0.0.1`.
- `app/api/entrypoint.py`: `build_app(environ=None)` is the API's composition root. It reads `GAME_HOSTING_API_TOKEN` (required, at least 16 characters), `GAME_HOSTING_DATABASE_URL` (required, a `postgresql+psycopg://` URL), `GAME_HOSTING_API_DOCS` (`1` or `true` enables the docs) and `GAME_HOSTING_LOG_LEVEL`, builds the real services through `app/composition.py` and returns the app. It fails closed with `ConfigurationError` (now defined in `app/errors.py`): without a valid token, or with a missing or unusable database URL, no app is created and the process exits with code 1; messages say which variable is wrong and never echo a rejected value. Neither Docker nor PostgreSQL is contacted at startup (the app starts and answers 503 on routes that need a service that is down). A lifespan hook disposes the database engine on shutdown. It logs the redacted database URL and whether the docs are enabled (a WARNING when they are), never a secret. Launch from `backend/`: `python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1`. Apply the migrations first (`python -m alembic upgrade head`); see `docs/RUNNING.md`.
- `app/composition.py` (2026-11-04): the composition root shared by the API and the CLI. `build_services(database_url)` creates the engine and session factory, the `SqlUnitOfWork` factory, the real `ServerService(DockerClient())`, the `ServerRegistry`, the `PlanCatalog` and the `UserAccounts` service (with the Argon2id hasher), and returns a `Services` object with a `close()` that disposes the engine. Services never import it.
- **Accounts and passwords (Phase 4A.1, 2026-11-25):** `app/domain/passwords.py` is the password policy (NIST SP 800-63B style): 12 to 128 characters after NFKC Unicode normalisation (so the same password typed on different keyboards verifies identically), any printable character including spaces and emoji, no composition rules, no control characters; errors never contain the password. `app/services/security.py` defines the `PasswordHasher` port (`hash`, `verify` that returns `False` for a wrong password or a malformed hash and never raises, and `needs_rehash`). `app/infrastructure/argon2_hasher.py` implements it with `argon2-cffi 25.1.0` (Argon2id, 64 MiB, 3 passes, 4 lanes, random 16-byte salt per hash, about 50 ms; `needs_rehash` will upgrade stored hashes when the parameters are raised). `app/services/user_accounts.py` (`UserAccounts`) creates accounts and changes password, role and active status; it validates before it hashes and always **hashes outside any database transaction** (hashing is slow on purpose and must not hold a connection). A user created by adopting a container has no password. Sign-in, sessions and throttling are the next step (4A.2).
- `app/services/server_registry.py` (2026-11-04): `ServerRegistry(uow_factory, server_service)` links a platform id to its container. `get_server`, `list_servers`, `start_server`, `stop_server(timeout)`, `restart_server(timeout)` and `get_server_logs(tail)` look the id up in the database (`ServerNotFoundError` for an unknown id, without touching Docker), close the database transaction **before** talking to Docker (a slow container operation never holds a connection open), and delegate to `ServerService` by container name. They return `RegisteredServer(server, owner, plan, status)`; a registered server whose container is gone reports status `NOT_FOUND` on reads and `ServerNotFoundError` on operations. `adopt_container(container_name, owner_email, plan_slug, name)` registers an existing managed container: the container must exist and carry the label, the plan must exist, and the owner is created if no user has that e-mail; it all happens in one transaction (a failure keeps nothing, including a would-be owner) and returns an `Adoption` that says whether the owner was created. `name` is required on purpose, so the display name cannot silently default to the internal container name. `app/services/plan_catalog.py`: `PlanCatalog.add_plan(...)` and `list_plans()`.
- `app/main.py`: a throwaway script that runs at import time (no `if __name__ == "__main__"` guard), builds `ServerService(DockerClient())` and prints the status of `minecraft-dev`. Kept on purpose as a simple entry point.
- `cli.py` (container commands 2026-10-15; administration commands 2026-11-04): it has two parts and contains no business logic. **Container commands**, which work on container names and need no database: `python cli.py [--server NAME] status | start | stop [--timeout S] | restart [--timeout S] | logs [--tail N]` (default server `minecraft-dev`; only containers with the managed label). **Operator administration**, which needs `GAME_HOSTING_DATABASE_URL`: `plans add --slug --name --memory-mb --cpu-millicores`, `plans list`, `servers adopt CONTAINER --owner EMAIL --plan SLUG --name NAME` (prints the new platform `id`, and whether the owner was created or already existed) `servers list` (the operator view, with the container name) and the **account commands** `users add --email [--role user|admin] [--no-password | --password-stdin]`, `users set-password`, `users set-role`, `users disable`, `users enable` and `users list`. Passwords are never accepted as arguments (they would end up in the shell history and the process list): they are read from a hidden prompt asked twice, or from standard input with `--password-stdin`; no output, error or listing ever shows a password or a hash. These administration commands are an explicit **bootstrap**, to be replaced piece by piece: users and sign-up in Phase 4, server creation instead of adoption in Phase 5, plan management with billing in Phase 7. Services for the administration commands are built only when one of them is used and are always closed. Exit codes: 0 ok, 1 other application error, 2 invalid usage (argparse), 3 not found (server, plan or user), 4 Docker unavailable, 5 operation failed, 6 already exists, 7 database unavailable, 8 invalid data, 9 configuration. `status` of a missing container prints `not_found` and exits 0. Unexpected exceptions are deliberately not caught.
- Tests (default run, no Docker needed): 588 pytest tests. Added in Phase 4A.1: 52 for the `User` account fields and the password policy, 21 for the Argon2id adapter (right and wrong password, malformed and foreign hashes, salts, rehash flagging, Unicode), 21 for `UserAccounts` (including that hashing never runs inside a transaction), 31 for the `users` commands (including that no password option exists and that no password or hash is ever printed) and 9 more repository contract cases, plus the rewritten areas below. Earlier: 450 tests as described in Phase 3 step 4 (registry, plan catalog, administration CLI, UUID-based API tests, entry point, repositories, portability, container CLI, Docker client and services).
- Integration tests (`pytest -m integration`, deselected by default, need a running Docker Engine): 137 tests. Added in Phase 4A.1: 38 repository contract tests now run on PostgreSQL too; migration `0002` is verified with data that existed before it (users keep working as regular, active accounts without a password), its downgrade keeps users and drops only the new columns, and the nullability of the new columns; the role and password-hash constraints and the safe defaults are exercised against the real database; and 8 end-to-end tests run the real `users` commands as subprocesses against PostgreSQL with real Argon2id (the stored value is an `$argon2id$` hash that verifies, the password appears nowhere in the database or the output, a policy-breaking password stores nothing, role and status changes, duplicate and unknown-user exit codes, and adopting a container creates an owner without a password). The rest as in Phase 3 step 4 (29 contract tests on PostgreSQL, migrations, constraints, SQL repositories, registry and end-to-end HTTP tests). Everything runs on throwaway containers and a real API process; never `minecraft-dev`, `game-hosting-db` or `./data`.

**Known gaps and risks:**

- Integration tests use an `alpine` throwaway container, not the Minecraft image, so they say nothing about Minecraft itself. That was checked by hand on 2026-10-15 instead: `cli.py stop` on `minecraft-dev` shut it down in about 1 s, the log shows "Stopping server" / "Saving players" / "Saving worlds", and the container exited with code 0.
- Verified on 2026-10-15 with Docker Desktop fully quit: all five CLI commands exit with code 4 and print `error: Cannot reach Docker ...` with no traceback (`app/main.py` still shows a traceback, as it has no error handling). The Docker error text is embedded as-is, so on a localised Windows it appears in the OS language.
- The container commands of the CLI hard-code the default container name `minecraft-dev` (acceptable while Minecraft is the only game); the platform API no longer depends on any container name.
- `ServerService.restart_server` does not check the server's existence first; a missing server surfaces through `DockerClient` as `ServerNotFoundError`, which is the same outcome.
- `backend/requirements.txt` is a pinned `pip freeze` of seven packages that matches the installed environment. It is now UTF-8 and `pywin32==312` carries the marker `sys_platform == "win32"` (it is a Windows-only transitive dependency of `docker`, not accidental tooling), so Linux skips it. A real Linux install was tested on 2026-10-29 (Ubuntu 24.04, Python 3.12.3): it installs cleanly and `pip check` passes. The file still pins transitive dependencies; the long-term strategy is an open decision below.
- **Linux verification (2026-10-29, see `docs/LINUX_CHECKPOINT.md`):** the full suite (215 default and 12 integration tests), the CLI, the API (as an unprivileged user), Compose with the managed label, world persistence, graceful stop and cgroup limits all work on native Linux (Ubuntu 24.04 under WSL2 with its own Docker Engine 29.9.2). Linux facts to design for: the itzg image owns `./data` as uid/gid 1000 and `level.dat` is mode 600 (backups must run as uid 1000, as root or inside the container); membership of the `docker` group is root-equivalent (use a dedicated unprivileged API user and keep the Docker socket off the network). Not verified: a real server (network exposure, performance, provider specifics).
- The root `.gitignore` lists `data/`, `.venv/`, `__pycache__/` and `*.pyc`, and the previously tracked `.pyc` files were removed from the index.
- **Phase 3 step 4 limits:** `GET /servers` and `GET /servers/{id}` need Docker for the live status, so they answer 503 when Docker is down even if the database is up. There are no update or delete operations for users, plans or servers yet (the deletion and change policy belongs to Phases 4, 5 and 7), so a wrong display name cannot be fixed without touching the database. Ownership is stored but not enforced: the shared token may act on every server until Phase 4. Servers are adopted from existing containers only; creating them is Phase 5.
- Adopted containers are linked to their platform id only through the database. The `game-hosting.server-id` container label that was planned for this step is deferred to Phase 5 (a label can only be set when a container is created, and the id is only known at adoption); Phase 5 provisioning will set it on the containers it creates, which will also allow reconciling containers against the database.
- `README.md` (Spanish) keeps the original design notes (some describe the architecture as it was first sketched) and the changelog; `docs/` is the source of truth for the current state.

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
| `User` | `id` UUID, `email`, `created_at`, `role`, `password_hash`, `is_active` | E-mail is stored trimmed and lowercase (checked strictly on construction), at most 254 characters, simple `local@domain.tld` shape. `role` is `Role.USER` or `Role.ADMIN` (`admin` is granted only from the operator CLI). `password_hash` is an opaque printable-ASCII string of at most 255 characters, or `None` for an account that cannot sign in yet; it is the output of a password hasher, never a password, and is left out of `repr` so that logging a user cannot leak it. `is_active=False` blocks sign-in. `can_sign_in` means active and with a password. Instances are immutable: `with_password_hash`, `with_role` and `with_active` return changed copies. |
| `Plan` | `id` UUID, `slug`, `name`, `memory_mb`, `cpu_millicores` | Slug lowercase letters, digits and dashes (at most 63 characters, unique). `memory_mb` and `cpu_millicores` (1000 = one core) are whole numbers in range; `bool` is rejected. Enforcement of the limits is Phase 5. |
| `GameServer` | `id` UUID, `owner_id`, `plan_id`, `game` (`Game` enum), `name`, `container_name`, `created_at` | `id` is the customer-facing identifier. `container_name` is internal, unique and valid as a Docker name; `GameServer.new` generates `gh-<uuid hex>` unless an existing container is adopted by passing its name. Display `name` is 1-64 characters without control characters. Runtime status is not stored (Docker is its source of truth). |

### PostgreSQL implementation (Phase 3 step 3, 2026-10-29)

- **Schema** (`app/infrastructure/db/models.py`, mirrored by the Alembic migration `0001`): `users(id uuid PK, email text unique, created_at timestamptz)`, `plans(id, slug unique, name, memory_mb, cpu_millicores)`, `game_servers(id, owner_id FK, plan_id FK, game text, name, container_name unique, created_at)` with an index on `owner_id`. Foreign keys are `ON DELETE RESTRICT`: a user or plan that still has servers cannot be deleted. CHECK constraints repeat the domain's rules (lowercase and bounded e-mail, slug and container-name formats, name length and no surrounding spaces, memory and CPU ranges), so data written by anything else still cannot break them. The `game` column has no CHECK on purpose: adding a game is a code change, and an unknown stored value is reported as `InvalidDataError` when read.
- **Account columns (migration `0002`, Phase 4A.1, 2026-11-25):** `users.role text NOT NULL DEFAULT 'user'` with `CHECK (role IN ('user','admin'))`, `users.password_hash text NULL` with `CHECK (password_hash IS NULL OR char_length BETWEEN 1 AND 255)`, and `users.is_active boolean NOT NULL DEFAULT true`. Existing users upgrade to regular, active accounts without a password (tested with data present before the migration). The safe default is `user`: a missing value can never produce an administrator. The downgrade drops only these columns and keeps the users. `UserRepository` gained `update(user)` (e-mail, role, hash and active flag, by id; `UserNotFoundError` or `AlreadyExistsError`) and `list_all()` (ordered by e-mail), with the same contract tests on the in-memory and PostgreSQL implementations. The sessions and login-throttling tables arrive with 4A.2 as migration `0003`.
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
| Platform identity vs container name | Decided 2026-10-29, implemented 2026-11-04: every `GameServer` has a UUID id exposed to customers; the container name is an internal, unique, generated value (or, for an adopted container, its existing name) and never appears in API responses or in the default display name. API routes are `/servers/{server_id}` with a UUID (a breaking change from Phase 2, accepted because no client existed). The planned `game-hosting.server-id` label is deferred to Phase 5. |
| Ownership storage and enforcement | Decided 2026-10-29: the database is the source of truth for ownership (not container labels); it is stored in Phase 3 (done) and enforced in Phase 4 when users can authenticate. Until then the shared API token acts as the operator. |
| Operator administration CLI is a bootstrap | Decided 2026-11-04: `plans add/list` and `servers adopt/list` exist only so the operator can put data in until sign-up (Phase 4), server creation (Phase 5) and plan management with billing (Phase 7) exist; each phase replaces its part. |
| Display name is required when adopting | Decided 2026-11-04 after an integration test showed that defaulting it to the container name exposed the internal name through the API; since there is no edit operation yet, the default would have been permanent. |
| Authentication model (Phase 4) | Decided 2026-11-25, account data and password hashing implemented in 4A.1 (2026-11-25), sessions, CSRF and throttling still to come in 4A.2-4A.4: operator-created accounts, e-mail and password hashed with Argon2id, server-side opaque sessions in an `HttpOnly`, `SameSite=Strict` cookie whose hash is stored in PostgreSQL, anti-CSRF token on state-changing routes, throttled generic login errors. The shared API token is removed from the HTTP API. |
| Authorization (Phase 4) | Decided 2026-11-25, not yet implemented: registry operations take an `Actor` and deny by default; users operate only their own servers, `admin` operates all, another user's server answers 404; `admin` is granted only from the CLI. |
| Same origin and `/api` (Phase 4) | Decided 2026-11-25, not yet implemented: the API moves under `/api` and FastAPI serves the built panel, so cookies stay `SameSite=Strict` and no CORS is needed. |
| Web panel (Phase 4) | React, highly customisable and smooth by requirement; the concrete stack is approved at the start of 4B. Node.js is not installed yet. |
| Internationalisation | Decided 2026-11-25: Spanish first, translatable to English and other languages; API errors carry stable codes and the client renders messages; no user-facing text hard-coded; locale-aware formatting. A permanent rule in `CLAUDE.md`. |
| Scalability | Decided 2026-11-25 as a permanent rule in `CLAUDE.md`: stateless API processes (sessions and rate limits in the database), paginated lists, bulk Docker statuses, infrastructure behind interfaces. |
| `Node` table deferred | Decided 2026-10-29: added in Phase 6 (see the deployment topology above); a later migration backfills a "local" node. |
| Interactive API docs disabled by default | Decided and implemented 2026-10-20: `/docs`, `/redoc` and `/openapi.json` are off unless `GAME_HOSTING_API_DOCS=1` (or `true`). |
| Use PostgreSQL for platform data | Target decision; introduce when domain persistence is needed. |
| Use React/Next.js for frontend | Target direction; not assumed to be implemented. |
| Keep frontend and Docker separated by backend services | Hard architectural rule. |
| Avoid Kubernetes and unnecessary infrastructure initially | Agreed; introduce only with demonstrated need. |
| Dependency pinning strategy | **Open.** `pywin32` now has a Windows-only marker. Still to decide: keep the full freeze, or list direct dependencies only and add a lock file, before the first Linux deployment. |
| Pin the Minecraft image and server version | Decided 2026-10-14: keep `latest` for both the image and `VERSION` during development; pin them before any production use. |

Update this section when a decision changes. Record consequential new decisions and their rationale.
