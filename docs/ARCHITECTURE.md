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
- `ServerService`: still only `get_server_status`, with a temporary shim that maps `ServerNotFoundError` back to the string `"not_found"` so `app/main.py` keeps working. Typed status and lifecycle operations are the next step (Phase 1, step 2).
- `app/main.py`: a throwaway script that runs at import time (no `if __name__ == "__main__"` guard) and prints the status of `minecraft-dev`. Kept on purpose as a simple entry point.
- `cli.py`: empty (0 bytes).
- Tests: 20 pytest unit tests for `DockerClient` in `backend/tests/infrastructure/`, using a fake SDK client (no Docker needed). `backend/pytest.ini` deselects the `integration` marker by default; no integration tests exist yet. Dev dependencies live in `backend/requirements-dev.txt`.
- Docker-unavailable handling is covered only by unit tests; it has not been exercised against a real stopped Docker Desktop.

**Known gaps and risks:**

- `ServerService` has no lifecycle operations and no typed status yet; there is no CLI.
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
| Use FastAPI for the future HTTP API | Target decision; not assumed to be implemented. |
| Use PostgreSQL for platform data | Target decision; introduce when domain persistence is needed. |
| Use React/Next.js for frontend | Target direction; not assumed to be implemented. |
| Keep frontend and Docker separated by backend services | Hard architectural rule. |
| Avoid Kubernetes and unnecessary infrastructure initially | Agreed; introduce only with demonstrated need. |
| Dependency pinning strategy | **Open.** `pywin32` now has a Windows-only marker. Still to decide: keep the full freeze, or list direct dependencies only and add a lock file, before the first Linux deployment. |
| Pin the Minecraft image and server version | Decided 2026-10-14: keep `latest` for both the image and `VERSION` during development; pin them before any production use. |

Update this section when a decision changes. Record consequential new decisions and their rationale.
