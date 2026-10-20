# Roadmap

> Keep this roadmap short, ordered, and evidence-based. A milestone is complete only when its acceptance criteria have been verified. Do not infer completion from code merely existing.

## Big Picture

The product is a stack of layers; each phase adds one. Update this section at the end of every task (one line per piece is enough).

```
 Customer's browser
        |
 [ Web panel ]        Phase 4   not started    what customers click
        |
 [ HTTP API ]         Phase 2   IN PROGRESS    the front door: lets programs and the panel give orders
        |
 [ ServerService ]    Phase 1   done           the rules: "start / stop / restart / status / logs"
        |
 [ DockerClient ]     Phase 1   done           the translator that talks to Docker
        |
 Docker Engine  ->  game containers (today: one Minecraft server, `minecraft-dev`)
```

Still to come around that stack: database with users, plans and servers (Phase 3); creating servers automatically (Phase 5); many servers, backups and monitoring (Phase 6); accounts, payments and subscriptions (Phase 7).

**Where we are:** the engine room works (a developer can control the Minecraft server from the terminal). We are building the HTTP API, so that something other than a terminal can control it. The piece being built right now is the front door itself. Its orders (status, start, stop, restart, logs) work over HTTP on the local machine, with clear errors, and the door now has a lock: only platform-labelled containers can be controlled, callers must present a token, and the API refuses to start without one. The local part of the phase is finished: an end-to-end HTTP test passes against a real API process, logging is configured, and `docs/RUNNING.md` explains how to start and use everything. What remains is the Linux checkpoint, which only the user can run (guide: `docs/LINUX_CHECKPOINT.md`). It is still a localhost-only API with a temporary shared token, not a public service.

## Current Phase

**Phase 1 — Local server controller: complete (verified 2026-10-15), pending the user's decision to start Phase 2.**

`ServerService` and `DockerClient` support status, start, stop, restart and logs with controlled errors; a CLI exposes them; 76 default tests and 5 integration tests pass. All of it was verified against a live Docker Engine and the real `minecraft-dev` container. Only Windows + Docker Desktop has been tested; nothing has run on Linux.

Phase 2 (HTTP API) is complete locally: the FastAPI app, the server endpoints, the managed-container label, the token, the fail-closed entry point, logging, the HTTP end-to-end test and the startup docs exist. Only the Linux checkpoint (run by the user) remains.

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
- [ ] Test `pip install -r backend/requirements.txt` on a real Linux environment (postponed with the decision above).

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

**Status: Complete locally (2026-10-20); the Linux checkpoint is pending the user.**

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
- [ ] Linux checkpoint — run by the user following `docs/LINUX_CHECKPOINT.md` (needs `git push` first, since the local branch is ahead of GitHub); then record the result here, fix what it exposes and note the Linux-specific production requirements (Docker socket permissions, dedicated non-root user, `./data` ownership).

**Definition of done**
- API endpoints reuse the controller's application logic.
- Error conditions map to appropriate HTTP status codes.
- No public Docker socket/API is exposed.
- Tests cover normal and error responses.
- Unmanaged containers cannot be controlled through the API or the CLI. (Met 2026-10-20; unit and integration tested.)

### Phase 3 — Domain model and persistence

Goal: represent platform concepts independently from Docker container names.

- [ ] Define minimal User, GameServer, Plan, and Node concepts, adding Subscription when needed.
- [ ] Give each platform server a stable internal ID separate from its runtime container name.
- [ ] Introduce PostgreSQL and migrations only when the model and API need persistence.
- [ ] Define repository boundaries where they help keep application logic testable.

**Definition of done**
- Server metadata can be persisted and retrieved.
- The domain model does not use container names as permanent customer-facing IDs.
- Schema changes are reproducible and tested.

### Phase 4 — Web control panel

Goal: build a useful customer-facing interface.

- [ ] Select and configure React/Next.js project structure.
- [ ] Build a server list and server detail/control view.
- [ ] Connect the UI to the backend API.
- [ ] Handle loading, empty, error, and operation-in-progress states.
- [ ] Introduce authentication and authorization before exposing customer-specific operations publicly.

**Definition of done**
- The UI operates through the API only.
- Users cannot access servers they do not own or have permission to manage.
- Core states and errors are handled clearly.

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

1. The user runs the Linux checkpoint (`docs/LINUX_CHECKPOINT.md`) and reports the results. Phase 2 is closed after that, and before Phase 3 starts.
2. Then Phase 3 (domain model and persistence): propose a plan first. It introduces PostgreSQL (a new service and dependency), the first data model, and the server-ownership design noted under Open Questions.
3. Pending decisions carried over from Phase 0: dependency strategy / Linux install test, and pinning the Minecraft image before production.

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
