# Architecture

> This document records the verified current architecture and a deliberately lightweight target design. Keep it aligned with the implementation. Label assumptions and plans as such; do not present planned components as existing.

## 1. Product Context

`game_hosting` is intended to become a commercial game-server hosting platform, starting with Minecraft Java Edition. The platform should eventually manage customer accounts, servers, plans/subscriptions, provisioning, server lifecycle, persistent worlds, backups, monitoring, and multiple execution nodes.

The guiding principle is **fast MVP, non-disposable architecture**: introduce useful boundaries early, but do not build infrastructure before it is needed.

## 2. Current Architecture — Verify Against Repository

### Local development environment

- Windows 11 with WSL2.
- Docker Desktop using the WSL2 backend.
- Python 3.14.7 virtual environment at `backend/.venv/` (not at the repository root).
- Python Docker SDK (`docker==7.2.0`) installed in that environment.

### Minecraft runtime

The root `docker-compose.yml` defines a Minecraft Java server (verified with `docker compose config`): image `itzg/minecraft-server:latest`, service name `minecraft`, container name `minecraft-dev`, port `25565:25565`, `EULA=TRUE`, `TYPE=VANILLA`, `VERSION=LATEST`, `MEMORY=4G`, `restart: unless-stopped`, and persistent host directory `./data` mounted at `/data`.

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
- `DockerClient`: `get_status`, `start_container`, `stop_container(timeout)`, `restart_container(timeout)` and `get_logs(tail)`. The Docker connection is created lazily on first use; a fake SDK client can be injected for tests. All Docker SDK and `requests` exceptions are translated into the application errors above and never leave this class. `get_status` returns Docker's raw status string and raises `ServerNotFoundError` for a missing container.
- `ServerService` (implemented 2026-10-15): `get_server_status`, `start_server`, `stop_server(timeout)`, `restart_server(timeout)` and `get_server_logs(tail)`. Status is a `ServerStatus` `StrEnum` (Docker's statuses plus `NOT_FOUND` and `UNKNOWN` for unrecognised values). Querying the status of a missing server returns `NOT_FOUND`; every other operation raises `ServerNotFoundError`. `start` and `stop` are idempotent (no Docker call when already running / not running) and all lifecycle operations return the final status. Defaults: 30 s stop/restart timeout, 100 log lines; `tail < 1` raises `ValueError`. Its `DockerClient` is a required constructor argument: the entry point (CLI, future API) builds the adapter and injects it, so this module imports `DockerClient` only for type checking (`TYPE_CHECKING`), never at runtime. This keeps the service independent of the concrete runtime for a future Linux / multi-node deployment. `server_name` is still the container name until Phase 3.
- `app/api/app.py` (Phase 2, steps 1-2, 2026-10-20): `create_app(service)` builds a FastAPI app with the service injected (`app.state.service`). Routes are synchronous `def` functions (the Docker SDK blocks; FastAPI runs them in a thread pool) and contain no business logic. Endpoints: `GET /health` (liveness, never calls Docker), `GET /servers/{name}` (200 with the status, `not_found` included), `POST /servers/{name}/start`, `POST /servers/{name}/stop?timeout=`, `POST /servers/{name}/restart?timeout=` (200 with the final status) and `GET /servers/{name}/logs?tail=` (plain text). Validation happens before the service is called: `name` must match `^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$`, `timeout` 1-300 (default 30), `tail` 1-1000 (default 100); violations return 422. Error mapping: `ServerNotFoundError` 404, `RuntimeUnavailableError` 503, `ServerOperationError` and other `GameHostingError` 500, anything unexpected a generic 500. Response bodies are generic; the underlying message is logged on the `game_hosting.api` logger (503 as one line, 500 with traceback, 404 at info). The endpoints are `POST` for state changes, so `GET` on them returns 405. There is no production entry point yet (no uvicorn launcher or logging configuration), no authentication and no managed-container scoping; those are Phase 2 steps 3-4. Until then it must only be run bound to `127.0.0.1`. FastAPI serves interactive docs at `/docs` by default; decide in step 3 whether to disable them.
- `app/main.py`: a throwaway script that runs at import time (no `if __name__ == "__main__"` guard), builds `ServerService(DockerClient())` and prints the status of `minecraft-dev`. Kept on purpose as a simple entry point.
- `cli.py` (implemented 2026-10-15): `python cli.py [--server NAME] status | start | stop [--timeout S] | restart [--timeout S] | logs [--tail N]`, default server `minecraft-dev`, run from `backend/` (it also works when invoked by path from elsewhere). It is the composition root: `build_service()` creates `ServerService(DockerClient())`, and `main(argv, service)` accepts an injected service for tests. It contains no business logic; it prints `<server>: <status>` (or the raw logs), and turns application errors into `error: <message>` on stderr plus an exit code: 0 ok, 1 other application error, 2 invalid usage (argparse), 3 server not found, 4 Docker unavailable, 5 operation failed. `status` of a missing server prints `not_found` and exits 0. Unexpected exceptions are deliberately not caught. `--timeout` and `--tail` must be >= 1.
- Tests (default run, no Docker needed): 109 pytest tests (the 76 below plus 33 in `backend/tests/api/`: 2 for `/health` and 31 for the server endpoints, error mapping, logging and validation, using `fastapi.testclient` with `httpx2` and a fake service). 20 for `DockerClient` in `backend/tests/infrastructure/` (fake SDK client), 29 for `ServerService` in `backend/tests/services/` (fake `DockerClient`), 24 for the CLI in `backend/tests/test_cli.py` (fake service), and 3 in `backend/tests/test_docker_unavailable.py` that use the real SDK pointed at a closed port via `DOCKER_HOST=tcp://127.0.0.1:1` (translation to `RuntimeUnavailableError`, in-process CLI exit code 4, and a real `cli.py` subprocess exiting with 4 and no traceback).
- Integration tests (`pytest -m integration`, deselected by default, need a running Docker Engine): 5 tests in `backend/tests/integration/`. They create a throwaway `alpine:3` container with a unique name (`game-hosting-it-<id>`), drive it through the real `ServerService`/CLI (create, start, restart, stop, logs, missing server) and force-remove it afterwards. They never touch `minecraft-dev` or `./data`. Running them without Docker fails loudly. Dev dependencies live in `backend/requirements-dev.txt`.

**Known gaps and risks:**

- Integration tests use an `alpine` throwaway container, not the Minecraft image, so they say nothing about Minecraft itself. That was checked by hand on 2026-10-15 instead: `cli.py stop` on `minecraft-dev` shut it down in about 1 s, the log shows "Stopping server" / "Saving players" / "Saving worlds", and the container exited with code 0.
- Verified on 2026-10-15 with Docker Desktop fully quit: all five CLI commands exit with code 4 and print `error: Cannot reach Docker ...` with no traceback (`app/main.py` still shows a traceback, as it has no error handling). The Docker error text is embedded as-is, so on a localised Windows it appears in the OS language.
- The CLI hard-codes the default server name `minecraft-dev` (acceptable while Minecraft is the only game; revisit in Phase 3).
- `ServerService.restart_server` does not check the server's existence first; a missing server surfaces through `DockerClient` as `ServerNotFoundError`, which is the same outcome.
- `backend/requirements.txt` is a pinned `pip freeze` of seven packages that matches the installed environment. It is now UTF-8 and `pywin32==312` carries the marker `sys_platform == "win32"` (it is a Windows-only transitive dependency of `docker`, not accidental tooling), so Linux is expected to skip it. A real Linux install has not been tested, and the file still pins transitive dependencies; the long-term strategy is an open decision below.
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

## 5. Planned Domain Model (Sketch Only)

This is a conceptual starting point, not an implemented schema. Refine it when persistence is introduced.

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
| Only label-marked containers are manageable | Decided 2026-10-15, **not yet implemented** (Phase 2 step 3): containers need `game-hosting.managed=true`, otherwise they are treated as not found, so the API and CLI cannot stop unrelated containers. Ownership by customer is a separate, later concern stored in the database (see roadmap open questions). |
| Temporary shared token for the API | Decided 2026-10-15, **not yet implemented**: guards a localhost API against hostile web pages; to be replaced by real authentication. |
| Use PostgreSQL for platform data | Target decision; introduce when domain persistence is needed. |
| Use React/Next.js for frontend | Target direction; not assumed to be implemented. |
| Keep frontend and Docker separated by backend services | Hard architectural rule. |
| Avoid Kubernetes and unnecessary infrastructure initially | Agreed; introduce only with demonstrated need. |
| Dependency pinning strategy | **Open.** `pywin32` now has a Windows-only marker. Still to decide: keep the full freeze, or list direct dependencies only and add a lock file, before the first Linux deployment. |
| Pin the Minecraft image and server version | Decided 2026-10-14: keep `latest` for both the image and `VERSION` during development; pin them before any production use. |

Update this section when a decision changes. Record consequential new decisions and their rationale.
