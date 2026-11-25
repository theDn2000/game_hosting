# Roadmap

> Keep this roadmap short, ordered, and evidence-based. A milestone is complete only when its acceptance criteria have been verified. Do not infer completion from code merely existing.

## Big Picture

The product is a stack of layers; each phase adds one. Update this section at the end of every task (one line per piece is enough).

```
 Customer's browser
        |
 [ Web panel ]         Phase 4   IN PROGRESS   what customers click, and where they log in (4A: backend security first)
        |
 [ HTTP API ]          Phase 2   done          the front door, with a lock (token); servers addressed by platform id
        |
 [ ServerRegistry ]    Phase 3   done          "which server is this?": platform id -> container, using the database
      |            |
 [ PostgreSQL ]   [ ServerService ]   Phase 3 / Phase 1   done   memory of users, plans and servers / the rules to start, stop, restart, read logs
                       |
                [ DockerClient ]     Phase 1   done           the translator that talks to Docker
                       |
 Docker Engine  ->  game containers (today: one Minecraft server, registered as a platform server)
```

Still to come: sign-up, login and a web panel, with ownership actually enforced (Phase 4); creating servers automatically and applying plan limits (Phase 5); many servers and nodes, backups and monitoring (Phase 6); payments and subscriptions (Phase 7).

**Where we are:** the platform now has a memory and a working skeleton from the database down to the container. A server has a platform id (a UUID) that customers will see, and its Docker container name stays internal. The real `minecraft-dev` server is registered in the development database (owner and plan recorded), the HTTP API controls it by that id, and an operator command-line tool puts data in. What does not exist yet is anyone other than the operator: there is no sign-up or login, so the owner of each server is stored but not yet enforced, and the shared API token acts as the operator. The API is still localhost-only, and WSL2 is not a real server, so one more check on a VPS is advisable before the first deployment.

## Current Phase

**Phase 4 — Authentication, authorization and web control panel: in progress (plan approved 2026-11-25).** Phase 3 (domain model and persistence) was completed on 2026-11-04.

Phase 4 is split into 4A (backend security: accounts, sessions, ownership enforcement, no UI) and 4B (the web panel), with a review between them; see the Phase 4 section. Two decisions carried over from earlier phases are still open: the dependency strategy (full freeze versus direct dependencies plus a lock file) and pinning the Minecraft image and version before production.


## Phases and Milestones

### Phase 0 — Development environment and Minecraft runtime

**Status: Complete (verified 2026-10-14), except that a real Minecraft client connection has not been tested by me.**

- [x] Windows, WSL2, Docker Desktop, Git, VS Code and Python development environment set up (reported by user). Python 3.14.7 in `backend/.venv` verified.
- [x] Local Minecraft Java container started and connected to at `localhost:25565` (reported by user; not re-verified).
- [x] Persistent world directory configured through `./data:/data` (verified from `docker compose config`).
- [x] Compose configuration verified: service `minecraft`, container `minecraft-dev`, image `itzg/minecraft-server:latest`, port `25565`, `restart: unless-stopped`.
- [x] `data/` is excluded from Git (root `.gitignore`; no file under `data/` is tracked).
- [x] `backend/.venv/` is excluded from Git, but only implicitly through the `.gitignore` (`*`) that `venv` creates inside it. The root `.gitignore` does not list it.
- [x] Server starts and stops with Docker Compose. Verified on 2026-10-14: `docker compose up -d` -> health `healthy`, port 25565 reachable, status `running`; `docker compose stop` -> `exited`.
- [x] Persistent data survives container removal/recreation. Verified on 2026-10-14: `docker compose down` removed the container (status `not_found`), `./data/world` stayed on disk with identical `level.dat` hash, and a new container came up `healthy` on the same world. Not yet verified: a Minecraft client joining the server.

Repository hygiene (found and applied on 2026-10-14, committed):

- [x] Add `.venv/`, `__pycache__/` and `*.pyc` to the root `.gitignore`. Verified with `git check-ignore`.
- [x] Remove the three tracked `__pycache__/*.pyc` files from the index (`git rm --cached`); local files were kept.
- [x] Re-encode `backend/requirements.txt` from UTF-16 LE with BOM to UTF-8. Verified: `pip install --dry-run -r requirements.txt` parses it.
- [x] Make `pywin32` Windows-only with the marker `; sys_platform == "win32"`. Verified that the marker evaluates to false for `linux` and true for `win32`. A real install on Linux has not been tested.
- [ ] Decide the long-term dependency strategy (direct dependencies only plus a lock file, or the current full pin). Deliberately postponed by the user; see the open decision in `docs/ARCHITECTURE.md`.
- [x] Test `pip install -r backend/requirements.txt` on a real Linux environment. Done 2026-10-29 on Ubuntu 24.04 (Python 3.12.3): installs cleanly, `pywin32` is skipped, `pip check` passes. The dependency strategy itself is still open.

**Definition of done**
- The server can be started and stopped using Docker Compose.
- Persistent data survives container removal/recreation.
- Runtime data and local environments are not committed, and the ignore rules are explicit in the root `.gitignore`.
- Dependencies can be installed from `requirements.txt` on the intended deployment platform, or the limitation is documented.

### Phase 1 — Python server controller

**Status: Complete (2026-10-15). All definition-of-done items below are met.**

Goal: implement a small, testable application layer that can control the existing container.

- [x] Demonstrate Python SDK connection to Docker Engine (reported successful).
- [x] Inspect current code and establish baseline (2026-10-14): `DockerClient.get_container`/`get_status` and `ServerService.get_server_status` exist; `app/main.py` is a throwaway script that prints the status of `minecraft-dev`; `cli.py` is empty; no tests.
Approved plan (2026-10-15), four steps, one commit each: (1) errors + `DockerClient` with tests, (2) `ServerService` with tests, (3) CLI, (4) integration check and docs. Decisions: `status` returns `not_found` while other operations raise `ServerNotFoundError`; pytest for tests; `app/main.py` is kept; default stop/restart timeout is 30 s, configurable.

- [x] Step 1 — `DockerClient` container lookup, status, start, stop, restart and logs with lazy connection and error translation (unit-tested, 20 tests; smoke-checked against the real `minecraft-dev` container on 2026-10-15).
- [x] Handle Docker Engine unavailable in `DockerClient` (translated to `RuntimeUnavailableError`; unit-tested only, not yet exercised with Docker Desktop actually stopped).
- [x] Define application-level errors for missing containers and Docker failures (`app/errors.py`).
- [x] Keep Docker SDK details in the infrastructure layer.
- [x] Step 2 — `ServerService` with typed status (`ServerStatus`), idempotent start/stop, restart, logs, default 30 s timeout, and its tests (29 unit tests, 49 in total; mutation-checked; smoke-checked against the real `minecraft-dev` container on 2026-10-15).
- [x] Add automated tests for service behavior and error cases.
- [x] Step 3 — Thin CLI that calls the same service methods (`status | start | stop | restart | logs`, exit codes 0-5, `ServerService` now receives its `DockerClient` from the entry point). 24 CLI unit tests (73 in total, mutation-checked); every command was run against the real `minecraft-dev` container on 2026-10-15, including a missing server (exit 3) and invalid usage (exit 2).
- [x] Step 4 — Run tests and perform integration checks (2026-10-15): 76 default tests and 5 `integration` tests pass (throwaway `alpine` container driven through `ServerService` and the CLI; `minecraft-dev` untouched); the full CLI was run by hand against `minecraft-dev`; with Docker Desktop fully quit every command exits with code 4 and a clear message. Also verified: `cli.py stop` on `minecraft-dev` stops it gracefully in about 1 s (the server logs "Stopping server", "Saving players" and "Saving worlds", and the container exits with code 0). Not verified: anything on Linux.

**Definition of done**
- Supported operations work against the local container.
- A missing container and expected Docker errors produce clear, controlled outcomes rather than an unhandled traceback.
- Automated tests cover important success and failure paths.
- CLI and service share implementation rather than duplicate it.
- `docs/ARCHITECTURE.md` and this roadmap match the verified implementation.

### Phase 2 — HTTP API

**Status: Complete (local work 2026-10-20; Linux checkpoint 2026-10-29).**

Goal: expose the existing application service through FastAPI without rewriting its behavior.

Approved plan, four steps, one commit each: (1) dependencies, `create_app` skeleton and `/health`; (2) status/start/stop/restart/logs endpoints, error mapping, validation and logging; (3) security measures; (4) HTTP integration test, Linux checkpoint, docs. Decisions (2026-10-15):
- Only containers labelled `game-hosting.managed=true` are manageable; any other container is treated as not found. This also applies to the CLI, so `docker-compose.yml` gets the label and `minecraft-dev` is recreated (`./data` untouched).
- A temporary shared token, read from an environment variable and sent in a header, is required on API calls; the API refuses to start without it. It exists to stop hostile web pages from reaching a localhost API (a cross-origin "simple" POST needs no preflight) and is not the user authentication of Phase 7/4. It is replaced by real authentication later.
- The API binds to `127.0.0.1` by default. No API versioning until a real client exists.
- No create/delete endpoints in this phase: only operate existing, labelled containers.
- Linux verification is a checkpoint at the end of Phase 2, before Phase 3 (the user asked for an environment setup guide at that point).
- Error mapping: not found 404, runtime unavailable 503, operation failed 500; raw Docker messages are logged server-side and never returned to clients. Input limits: server name restricted to valid Docker names, `tail` 1-1000, `timeout` 1-300.

- [x] Step 1 — Dependencies (`fastapi`, `uvicorn`; dev: `httpx2` instead of the deprecated `httpx`, as Starlette recommends), `create_app(service)` skeleton and `GET /health` (liveness only, never calls Docker). 2 tests; smoke-checked with a real uvicorn on `127.0.0.1`.
- [x] Step 2 — Server endpoints (`GET /servers/{name}`, `POST .../start|stop|restart`, `GET .../logs`), HTTP error mapping, input validation and logging (2026-10-20). 31 new API tests (109 default tests in total), mutation-checked (5 mutations detected). Verified by hand with a real uvicorn on `127.0.0.1` against `minecraft-dev`: status, logs, stop, start, restart (200), unknown server (`GET` 200 `not_found`, `POST start` 404), invalid name and `timeout=0` (422). With Docker stopped, the real stack answers 503 with a generic body and `/health` stays 200. Not yet in place: managed-container scoping and the token (step 3), so the API still must only run on `127.0.0.1`.
- [x] Step 3 — Managed-container label (`DockerClient` + Compose + CLI), temporary bearer token, docs off by default, and `app/api/entrypoint.py` (fail-closed configuration from environment variables) (2026-10-20). 199 default tests and 6 integration tests pass; 8 mutations were detected (label check, token check, an unprotected route, docs switch, minimum token length, fail-closed entry point, log escaping). `minecraft-dev` was recreated with the label and `./data` was intact (same size, 12 region files, loaded normally). Verified with real uvicorn processes: no token or a short token refuses to start (exit 1); with a token, `/health` 200, no or wrong token 401, correct token 200 for status/stop/start/logs, unknown server 404, `/docs` and `/openapi.json` 404, `/docs` 200 only with `GAME_HOSTING_API_DOCS=1` (its routes still 401), and the token never appears in the server log.
- [x] Step 4 (local part, 2026-10-20) — Logging configuration (`GAME_HOSTING_LOG_LEVEL`), HTTP end-to-end integration tests (a real uvicorn subprocess driven over HTTP against throwaway containers; 6 tests), startup and usage documentation (`docs/RUNNING.md`, commands checked in PowerShell) and the Linux checkpoint guide (`docs/LINUX_CHECKPOINT.md`). 215 default and 12 integration tests pass on Python 3.14 and, in a temporary environment, on Python 3.12.15. Mutation checks detected the changes to logging, token handling and the unprotected-route cases (the HTTP tests only probe `start` without a token; the unit suite covers every route).
- [x] Portability fix found while preparing Linux: `ServerService` raised `NameError` on Python < 3.14 because `DockerClient` was imported only under `TYPE_CHECKING` (introduced in Phase 1 step 3, invisible on 3.14). Fixed with `from __future__ import annotations` (commit `e0d445e`) and guarded by `tests/test_portability.py`.
- [x] Linux checkpoint (2026-10-29) — run by Claude in a native Ubuntu 24.04.5 under WSL2 with its own Docker Engine 29.9.2 (cgroup v2), Python 3.12.3, the repository cloned from the local commit. 215 default tests and 12 integration tests pass; `minecraft-dev` starts healthy with the label; CLI and API work (the API as an unprivileged user); the world survives `compose down` and `up` (26 files, 2360 KB, 4 region files, identical); `cli.py stop` is graceful (about 0.9 s, exit code 0, worlds saved); cgroup memory and CPU limits are enforced. Full table, findings and the reproducible setup are in `docs/LINUX_CHECKPOINT.md`. Not covered because WSL2 is not a server: real network exposure, performance and provider specifics, so a VPS check is still advisable before the first deployment.
- [ ] Linux findings to design for later phases: (1) the itzg image owns `./data` as uid/gid 1000 and `level.dat` is mode 600, so Phase 6 backups must run as uid 1000, as root, or from inside the container; (2) the `docker` group is root-equivalent, so production needs a dedicated unprivileged API user and a Docker socket that never reaches the network; (3) pin the Minecraft image/version before production.

**Definition of done**
- API endpoints reuse the controller's application logic.
- Error conditions map to appropriate HTTP status codes.
- No public Docker socket/API is exposed.
- Tests cover normal and error responses.
- Unmanaged containers cannot be controlled through the API or the CLI. (Met 2026-10-20; unit and integration tested.)

### Phase 3 — Domain model and persistence

**Status: Complete (2026-10-29 to 2026-11-04).**

Goal: represent platform concepts independently from Docker container names.

Approved plan, five steps, one commit each: (1) database infrastructure; (2) domain model, repository interfaces, in-memory fakes and unit tests; (3) SQLAlchemy models, Alembic baseline migration, PostgreSQL repositories and integration tests (including migration up/down and a models-versus-migrations check); (4) `ServerRegistry`, id-based routes, the `game-hosting.server-id` label and a CLI command that adopts the existing `minecraft-dev` container; (5) end-to-end HTTP test, Linux verification and docs.

Decisions (2026-10-29):
- Stack: SQLAlchemy 2 + Alembic + psycopg 3 on PostgreSQL 18 (the latest major with published images; pinned by major). Alembic is added in step 3, when it is first used.
- Plain domain dataclasses, separate from the SQLAlchemy models; services depend on repository interfaces, never on SQLAlchemy.
- Entities: `User` (id, email, created_at; no credentials, authentication is Phase 4), `Plan` (slug, name, memory_mb, cpu_limit; enforcement is Phase 5), `GameServer` (UUID id, owner, plan, game, display name, internal unique `container_name`). The runtime status is not stored: Docker remains its source of truth.
- `Node` is deferred to Phase 6 (a single node exists today; adding it later is a simple migration backfilled with a "local" node). `Subscription` is Phase 7.
- API routes become `/servers/{server_id}` with a UUID instead of the container name (a breaking change accepted because no client exists yet). The CLI keeps operating by container name as a developer tool.
- Ownership is stored in this phase but enforced in Phase 4, when users can authenticate. Until then the shared API token acts as the operator and may act on every server.
- The database is the single source of truth for ownership (not container labels), is central, and is never accessed by nodes directly.
- Records are created in this phase only by adopting an existing container with a CLI command; creating servers from scratch is Phase 5.

- [x] Step 1 — Database infrastructure (2026-10-29): `postgres:18` service in `docker-compose.yml` (container `game-hosting-db`, named volume, published only on `127.0.0.1`, no `game-hosting.managed` label so the API and CLI cannot touch it, credentials from the git-ignored `.env`, see `.env.example`); `SQLAlchemy 2.1.6` and `psycopg 3.3.6` added; connection layer in `app/infrastructure/db/database.py` (`create_database_engine`, `create_session_factory`, `check_connection`, `redact_url`) with a `DatabaseUnavailableError` application error and passwords never shown. 230 default tests pass on Windows and on Linux; 17 integration tests (5 new, against a throwaway PostgreSQL container) pass on Ubuntu 24.04; mutation-checked (6 mutations detected, one initially survived and its test was strengthened). The Compose service was verified on Linux: healthy, bound to `127.0.0.1` only, data survives removing and recreating the container, and the CLI reports it as not found and cannot stop it. Not yet verified: the integration tests with Docker Desktop on Windows (it was off).
- [x] Step 2 — Domain model, repository interfaces and in-memory reference implementation (2026-10-29). `app/domain/models.py`: immutable, self-validating `User`, `Plan` and `GameServer` dataclasses with `new` factories (UUID v4 ids, UTC timestamps, normalised e-mail; generated container name `gh-<uuid hex>` unless an existing one is adopted). `app/services/repositories.py`: `UserRepository`, `PlanRepository`, `GameServerRepository` and `UnitOfWork` protocols (changes are permanent only on `commit()`). New application errors: `InvalidDataError`, `UserNotFoundError`, `PlanNotFoundError`, `AlreadyExistsError`. `tests/support/in_memory.py` implements the contract; `tests/repositories/test_repository_contract.py` is written against the interfaces so the PostgreSQL implementation (step 3) must pass the same tests. 97 new tests (327 in total on Windows and on Ubuntu with Python 3.12; the contract tests were repeated 30 times without flakiness); 17 mutations detected. Refinement of the agreed design: the plan's CPU allowance is stored as `cpu_millicores` (integer, 1000 = one core) instead of `cpu_limit`, which avoids floating-point values and converts exactly to Docker's CPU quota. Integration tests on Windows with Docker Desktop also passed (17).
- [x] Step 3 — Real persistence (2026-10-29). `app/infrastructure/db/models.py` (tables `users`, `plans`, `game_servers` with UUID keys, `timestamptz` times, CHECK constraints that repeat the domain's rules, unique constraints, foreign keys with `ON DELETE RESTRICT`, an index on the owner, and a fixed constraint-naming convention); Alembic 1.20.1 with the hand-written baseline migration `0001` (`backend/migrations/`, `backend/alembic.ini` without any URL, the URL comes from `GAME_HOSTING_DATABASE_URL`); `app/infrastructure/db/repositories.py` (`SqlUserRepository`, `SqlPlanRepository`, `SqlGameServerRepository`, `SqlUnitOfWork`). Each `add` runs in a SAVEPOINT so a rejected insert does not poison the transaction; constraint violations become `AlreadyExistsError`, `UserNotFoundError`, `PlanNotFoundError` or `InvalidDataError` by constraint name; connection failures become `DatabaseUnavailableError`. The 28 repository contract tests pass against both the in-memory and the PostgreSQL implementation; migrations are verified up, down, repeated and idempotent, and models equal migrations. 354 default tests and 95 integration tests pass on Windows (Docker Desktop) and on Ubuntu 24.04 with Python 3.12; 15 mutations on the repositories, migration and models were detected. Verified by hand with the CLI on the Compose database: `alembic upgrade head` gives `0001 (head)`, `alembic check` reports no differences, and the password never appears in output. Not yet in place: no data is stored yet, and the API does not use the database (step 4).
- [x] Step 4 — Connecting everything (2026-11-04). `ServerRegistry` resolves a platform UUID to its container through the database (closing the database transaction before any Docker call) and delegates to `ServerService`; `PlanCatalog` creates and lists plans; `app/composition.py` is the composition root shared by the API and the CLI. The API now addresses servers as `/servers/{server_id}` (UUID) and adds `GET /servers`; responses contain only `id`, `name`, `game` and `status` (never the container name or the owner); a database outage answers 503; `GAME_HOSTING_DATABASE_URL` is required at startup (fails closed, never echoed, redacted in the log) and the engine is released on shutdown. The CLI gained the operator administration commands `plans add/list` and `servers adopt/list` (new exit codes 6-9). 450 default tests and 105 integration tests pass on Windows (Docker Desktop) and on Ubuntu 24.04 with Python 3.12; 21 mutations were detected (two initially survived and their tests were strengthened). Verified without creating data on the real development stack: the API starts against `game-hosting-db`, answers 401 without a token, `[]` with it, 422 for an old container-name id and 404 for `/docs`, no secret appears in its log, and it refuses to start without the database URL. An integration test found that the default display name (the container name) exposed the internal name through the API; `--name` is therefore required when adopting. **Refinements of the agreed plan:** (1) the `game-hosting.server-id` container label is deferred to Phase 5, because a label can only be set when a container is created and the id is only known at adoption (the database already holds the mapping); (2) the administration CLI is an explicit bootstrap, to be replaced piece by piece by Phases 4, 5 and 7. Pending in this step: adopting the real `minecraft-dev` server into the database (the owner's e-mail is the user's choice).
- [x] Real server adopted (2026-11-04). With the Compose stack up and the database at revision `0001`, `plans add` created the plan `dev` (4096 MB, 2000 millicores) and `servers adopt minecraft-dev` registered the real development server under the platform id shown by `cli.py servers list`, with the operator's account e-mail as owner (created) and the display name "Mundo de desarrollo". Repeating the adoption was rejected without duplicating (exit code 6). Through the API with a fresh token: `GET /servers` and `GET /servers/{id}` return the server as `running`, `POST .../start` is an idempotent no-op that leaves the running server untouched, `GET .../logs` returns the real log, no token gives 401, the old container-name address gives 422, no response contains the container name or the e-mail, and no secret appears in the API's log. Read-only and idempotent calls were used on purpose, so the running server was not disturbed.
- [x] Step 5 — Closing checks (2026-11-04). The end-to-end HTTP tests, the Linux verification (Ubuntu 24.04, Python 3.12) and the documentation were completed as part of step 4; the Phase 3 definition of done was reviewed point by point against the evidence below.
- [x] Give each platform server a stable internal ID separate from its runtime container name. (Modelled in step 2, exposed through the API in step 4: customers see only the UUID.)
- [x] Introduce PostgreSQL and migrations (step 1: PostgreSQL service; step 3: Alembic baseline migration `0001`, applied to the development database on 2026-10-29 and reported by `alembic current` as `0001 (head)`).
- [x] Define repository boundaries where they help keep application logic testable. (Step 2 interfaces; step 3 PostgreSQL implementation passing the same contract tests.)

**Definition of done**
- Server metadata can be persisted and retrieved.
- The domain model does not use container names as permanent customer-facing IDs.
- Schema changes are reproducible and tested.

Definition-of-done evidence (2026-11-04): (1) *Server metadata can be persisted and retrieved*: 29 repository contract tests pass identically on the in-memory and the PostgreSQL implementations, and the real server round-trips through `servers adopt` and the API. (2) *The domain model does not use container names as permanent customer-facing IDs*: the customer-facing id is a UUID, the container name is internal, and tests assert that no API response contains it. (3) *Schema changes are reproducible and tested*: Alembic migration `0001` is verified up, down, repeated and idempotent on a fresh database, `alembic check` reports no difference between models and database, every CHECK, unique, foreign-key and RESTRICT rule is exercised against a real PostgreSQL, and all of it passes on Windows and on Ubuntu 24.04 (450 default and 105 integration tests).

### Phase 4 — Authentication, authorization and web control panel

**Status: In progress (plan approved 2026-11-25).**

Goal: every customer has their own account and manages only their own server(s) through a web panel that is smooth, satisfying and highly customisable to use.

Approved decisions (2026-11-25):
- **Accounts:** one per customer, created by the operator for now (no open sign-up). Self-service sign-up with e-mail verification, password recovery and similar account-lifecycle features arrive with billing (Phase 7). E-mail and password, hashed with Argon2id (`argon2-cffi`), minimum 12 and maximum 128 characters, no composition rules, never logged or returned; the CLI reads passwords with a hidden prompt or from standard input, never from an argument.
- **Sessions:** server-side, opaque 256-bit token in an `HttpOnly`, `SameSite=Strict` cookie (`Secure` when served over HTTPS); the database stores only its hash; revocable; idle and absolute expiry; renewed on login. State-changing routes also require an anti-CSRF token. Login errors are generic, response time does not reveal whether an account exists, and failed attempts are throttled per account and per IP.
- **Authorization:** the shared API token is removed from the HTTP API (the operator uses the CLI, which talks to the database directly, and an `admin` account). Every registry operation takes an `Actor` and denies by default: a `user` sees and operates only their own servers, an `admin` sees and operates all of them, and another user's server answers 404 (not 403) so its existence is not revealed. The `admin` role can only be granted from the CLI.
- **Panel:** React. It must allow deep visual customisation (a themeable design system rather than a stock look), fluid interaction and a very smooth feel. The concrete stack (build tool, component and styling approach, animation, data fetching, internationalisation library) is proposed and approved at the start of 4B; the choice between a static single-page app served by FastAPI and Next.js stays reversible because components and the design system are portable between them.
- **Same origin:** the API moves under `/api` and FastAPI serves the built panel, so no CORS is needed.
- **Phase split:** 4A (backend security, no UI) and 4B (panel), with a review between them.

Cross-cutting requirements recorded from the user (2026-11-25), now also permanent rules in `CLAUDE.md`:
- **Translatable:** Spanish first, then English and other languages. No user-facing text hard-coded in components or handlers; the API returns stable machine-readable error codes (`code`) and the client renders the localised message; locale-aware formatting of dates and numbers; logical (direction-agnostic) CSS so right-to-left languages are possible; a per-user language preference (added with the first UI, a small migration).
- **Always scalable:** stateless API processes (sessions and login throttling live in PostgreSQL, not in memory), paginated list endpoints with a maximum page size, Docker statuses fetched in bulk instead of one call per server, and infrastructure behind interfaces.

Plan (one commit per step, with a report):
- [x] 4A.1 — Account data and passwords (2026-11-25): migration `0002` (`users.role`, `users.password_hash`, `users.is_active`, safe defaults and CHECK constraints), `Role`, the self-validating `User` with the hash kept out of `repr`, the password policy (12-128 characters, NFKC-normalised, no composition rules), the `PasswordHasher` port with an Argon2id adapter (`argon2-cffi 25.1.0`, about 50 ms per hash), `UserAccounts` (validates before hashing and hashes outside any transaction), `UserRepository.update` and `list_all` on both implementations, and the operator commands `users add/set-password/set-role/disable/enable/list` (hidden prompt or `--password-stdin`; no password option exists). 588 default and 137 integration tests pass on Windows and on Ubuntu 24.04 with Python 3.12; 23 mutations detected (including a default role of `admin` in the migration, a hash leaking through `repr` or the user listing, a skipped password policy, hashing inside a transaction and missing commits). Migration `0002` was applied to the development database after a backup (`0001` -> `0002 (head)`, `alembic check` clean, all data intact), and the operator account was promoted to `admin`; it still has no password, which the user sets with the hidden prompt (the sign-in endpoint comes in 4A.4). New dependencies: `argon2-cffi 25.1.0`, `argon2-cffi-bindings 26.2.0`, `cffi 2.1.1`, `pycparser 3.12`.
- [x] 4A.2 — `AuthService` (2026-11-25): `sign_in`, `authenticate` and `sign_out` with server-side sessions (only the SHA-256 of a 256-bit token stored; 7-day absolute lifetime, 8-hour idle timeout, activity written at most once a minute, at most 10 active sessions per user), generic and indistinguishable sign-in failures that each cost exactly one password verification (decoy hash for unknown and passwordless accounts), database-backed throttling with three limits (per account and client, per client, per account; checked before any hashing; blocked attempts not recorded; wait computed from the failure that must expire), a race guard between verification and session creation, transparent hash upgrades, bounded housekeeping, and a one-way CSRF token derived from the session token. Migration `0003` (`sessions`, `login_failures`) with CHECK constraints and indexes; sessions and login-failure repositories on both implementations. `UserAccounts` now ends a user's sessions when the password changes or the account is disabled, and `cli.py users sign-out` ends them on demand. 736 default and 192 integration tests pass on Windows and on Ubuntu 24.04 with Python 3.12; 41 mutations were detected (three survived at first and led to stronger tests: attempts on disabled accounts must also be throttled, and a session of an account disabled behind the services' back must stop working). Two independent service sets on PostgreSQL prove that sessions and failure counts are shared (stateless processes). A bug found by the tests: a failure still counted at the exact instant its window ended; fixed. Migration `0003` was applied to the development database after a backup (`0002` -> `0003 (head)`, `alembic check` clean, all data intact). Not wired to HTTP yet (4A.4).
- [x] 4A.3 — Authorization in the registry (2026-11-25): every `ServerRegistry` operation takes an `Actor`; customers act only on their own servers, administrators on all; another customer's server answers exactly like a missing one (`ServerNotFoundError`, checked before Docker is touched); adoption needs an administrator; a caller that is not an `Actor` is refused (deny by default). The CLI and, until 4A.4, the shared API token act as the constant `OPERATOR` administrator. `list_servers(actor, limit, offset)` returns a `Page` (limit 1 to 100) with three database queries (page, count, owners and plans in bulk) and one Docker call (`DockerClient.get_statuses`) whatever the page size. 814 default and 196 integration tests pass on Windows (Python 3.14) and on Ubuntu 24.04 (Python 3.12), including an ownership matrix over every operation and actor kind and a real PostgreSQL + Docker test; 32 mutations were checked and the one survivor (listing with a non-`Actor`) now has a test. `GET /servers` accepts `limit` and `offset`; its response shape changes in 4A.4.
- [ ] 4A.4 — API: `/api` prefix, `/api/auth/login|logout|me`, cookie and CSRF protection, removal of the shared token, machine-readable error codes, pagination.
- [ ] 4A.5 — Security review, Linux verification and documentation. **Stop for the user's review before 4B.**
- [ ] 4B.1 — Frontend stack proposal and approval; toolchain (Node.js is not installed yet and needs the user's explicit permission), scaffold, typed API client, internationalisation and theming foundations, tests.
- [ ] 4B.2 — Screens: sign-in, server list, server detail with start/stop/restart and logs; loading, empty, error and operation-in-progress states.
- [ ] 4B.3 — Serve the panel from FastAPI, security headers (strict CSP), end-to-end verification.
- [ ] 4B.4 — Documentation, Linux verification and closing review.

**Definition of done**
- The UI operates through the API only.
- Users cannot access servers they do not own or have permission to manage (proven by an ownership matrix over every route and operation).
- Core states and errors are handled clearly.
- Passwords and sessions meet the rules above, with tests for expiry, revocation, CSRF, throttling and enumeration resistance.
- Text is translatable and the API exposes error codes.

Out of scope: open sign-up, e-mail verification and password recovery, second factor, public exposure with HTTPS (documented, done at deployment), customers creating servers (Phase 5) and billing (Phase 7).


### Phase 5 — Automated provisioning on a development node

Goal: create and configure new game servers through the platform.

- [ ] Define validated server configuration.
- [ ] Create containers through a controlled provider layer.
- [ ] Allocate ports safely and avoid collisions.
- [ ] Apply CPU, memory, storage, and other required limits.
- [ ] Manage persistent volumes/directories safely.
- [ ] Add cleanup and failure-recovery behavior.

**Definition of done**
- A server can be provisioned, started, stopped, and removed through the application.
- Resource limits and port allocation are enforced.
- Removing a container does not silently delete persistent customer data.

### Phase 6 — Multi-server and operational foundations

Goal: prepare for multiple customers and eventually multiple nodes.

- [ ] Run multiple isolated servers on one development node.
- [ ] Track capacity and prevent oversubscription.
- [ ] Add logs, health monitoring, backups, and restore verification.
- [ ] Design a secure node-agent boundary before managing remote hosts.
- [ ] Test failure cases and operational recovery.

**Definition of done**
- Multiple servers can coexist with controlled resource and port allocation.
- Backup/restore behavior has been tested.
- Remote node control, if implemented, does not expose Docker directly.

### Phase 7 — Commercial readiness

Goal: progressively add the controls required for a real hosting business.

- [ ] Authentication, authorization, account lifecycle, and auditability.
- [ ] Plans and enforceable resource entitlements.
- [ ] Stripe subscriptions and billing lifecycle handling.
- [ ] Customer-facing usage, status, and support diagnostics.
- [ ] Security review, monitoring, incident procedures, and cost/capacity validation.

**Definition of done**
- Paid access and entitlements are enforced server-side.
- Billing events are handled safely and idempotently.
- Operational and data-protection claims are supported by tested procedures.

## Immediate Next Step

1. The user sets a password for the operator account with the hidden prompt (`python cli.py users set-password --email <your e-mail>`); it is needed to try sign-in once the endpoints exist (4A.4).
2. Phase 4A.4 — HTTP: the `/api` prefix, `/api/auth/login|logout|me` with an HttpOnly `SameSite=Strict` cookie and a CSRF header, the `Actor` built from the session on every request, removal of the shared token, machine-readable error codes, paginated list responses with totals, and a trusted source for the client IP used by the login throttle. Then 4A.5 (security review and Linux checks; stop for the user's review).
3. A stop for the user's review before the panel (4B).
4. Pending decisions carried over from earlier phases: the dependency strategy and pinning the Minecraft image and version before production.
5. Carried-over technical debt, all documented in `docs/ARCHITECTURE.md`: no edit or delete operations for users, plans or servers; `GET /servers` still needs Docker for live statuses (one call per page since 4A.3, but a stopped Docker still answers 503); the `game-hosting.server-id` container label arrives with Phase 5 provisioning; the operator CLI is a bootstrap to be replaced by Phases 4, 5 and 7.


## Out of Scope for Now

- Kubernetes or a general-purpose orchestration platform.
- Public exposure of Docker Engine.
- Multi-cloud scheduling or advanced distributed control plane.
- Production billing before server identity, persistence, and access control exist.
- Native mobile applications.
- AI diagnostics beyond a later, evidence-based troubleshooting feature.
- Premature microservices, queues, caches, or complex observability stacks.

## Open Questions

Resolve these when the relevant phase approaches; do not block the current controller work on them.

- Server ownership and lifecycle (Phases 3, 5, 7): who may operate a container, and who decides when it is created and destroyed? Working position (not decided): ownership lives in the database, not in container labels, and the API checks it before calling `ServerService`; a server is provisioned automatically when a subscription becomes active; the customer can start, stop and restart it; deletion is a separate guarded action with a grace period, never immediate.
- Which Linux distribution and hosting provider will be used for the first production node?
- What initial Minecraft plans and resource limits are commercially viable?
- How will ports, DNS, and customer connection endpoints be assigned?
- What backup retention, restore objectives, and storage provider are appropriate?
- What authentication provider or implementation will be used?
- When is a separate node agent justified instead of a single-node deployment?
- Which metrics and capacity thresholds should drive placement and alerts?
