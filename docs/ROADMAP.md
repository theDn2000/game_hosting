# Roadmap

> Keep this roadmap short, ordered, and evidence-based. A milestone is complete only when its acceptance criteria have been verified. Do not infer completion from code merely existing.

## Current Phase

**Phase 1 — Local server controller**

The local Minecraft container is defined under Docker Compose, and Python-to-Docker connectivity has been demonstrated (reported by user). The Python controller is being built: only container lookup and status exist so far (verified by inspection on 2026-10-14). There are no automated tests and `backend/cli.py` is empty. Docker Desktop was not running during the last inspection, so nothing was verified against a live container.

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

**Status: In progress.**

Goal: implement a small, testable application layer that can control the existing container.

- [x] Demonstrate Python SDK connection to Docker Engine (reported successful).
- [x] Inspect current code and establish baseline (2026-10-14): `DockerClient.get_container`/`get_status` and `ServerService.get_server_status` exist; `app/main.py` is a throwaway script that prints the status of `minecraft-dev`; `cli.py` is empty; no tests.
Approved plan (2026-10-15), four steps, one commit each: (1) errors + `DockerClient` with tests, (2) `ServerService` with tests, (3) CLI, (4) integration check and docs. Decisions: `status` returns `not_found` while other operations raise `ServerNotFoundError`; pytest for tests; `app/main.py` is kept; default stop/restart timeout is 30 s, configurable.

- [x] Step 1 — `DockerClient` container lookup, status, start, stop, restart and logs with lazy connection and error translation (unit-tested, 20 tests; smoke-checked against the real `minecraft-dev` container on 2026-10-15).
- [x] Handle Docker Engine unavailable in `DockerClient` (translated to `RuntimeUnavailableError`; unit-tested only, not yet exercised with Docker Desktop actually stopped).
- [x] Define application-level errors for missing containers and Docker failures (`app/errors.py`).
- [x] Keep Docker SDK details in the infrastructure layer.
- [ ] Step 2 — `ServerService` with typed status (`ServerStatus`), idempotent start/stop, restart, logs, default 30 s timeout, and its tests.
- [ ] Add automated tests for service behavior and error cases (infrastructure tests done; service tests pending).
- [ ] Provide a thin CLI that calls the same service methods.
- [ ] Run tests and perform a manual integration check against the local container.

**Definition of done**
- Supported operations work against the local container.
- A missing container and expected Docker errors produce clear, controlled outcomes rather than an unhandled traceback.
- Automated tests cover important success and failure paths.
- CLI and service share implementation rather than duplicate it.
- `docs/ARCHITECTURE.md` and this roadmap match the verified implementation.

### Phase 2 — HTTP API

Goal: expose the existing application service through FastAPI without rewriting its behavior.

- [ ] Add a minimal FastAPI application and health endpoint.
- [ ] Expose narrowly scoped server lifecycle/status/log endpoints.
- [ ] Validate request parameters and return consistent HTTP errors.
- [ ] Keep routes thin; call `ServerService`.
- [ ] Add API tests and document local startup.

**Definition of done**
- API endpoints reuse the controller's application logic.
- Error conditions map to appropriate HTTP status codes.
- No public Docker socket/API is exposed.
- Tests cover normal and error responses.

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

1. Phase 1, step 2: `ServerService` with typed status and lifecycle operations, plus tests.
2. Then step 3 (CLI) and step 4 (integration check against the local Compose service; Docker Desktop must be running).
3. Update `docs/ARCHITECTURE.md` and this roadmap based on the resulting code and test evidence.

Do not start Phase 2 until Phase 1's definition of done is met or an explicit scope decision is made.

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

- Which Linux distribution and hosting provider will be used for the first production node?
- What initial Minecraft plans and resource limits are commercially viable?
- How will ports, DNS, and customer connection endpoints be assigned?
- What backup retention, restore objectives, and storage provider are appropriate?
- What authentication provider or implementation will be used?
- When is a separate node agent justified instead of a single-node deployment?
- Which metrics and capacity thresholds should drive placement and alerts?
