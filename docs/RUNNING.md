# Running game_hosting locally

How to set up the project, start the Minecraft server, use the CLI, run the HTTP API and run the tests. Commands are for Windows PowerShell unless marked otherwise; the Linux equivalents are in `docs/LINUX_CHECKPOINT.md`.

> The API is a **localhost-only development service** protected by a temporary shared token. Do not bind it to a public interface (`0.0.0.0`) and do not expose the Docker socket. See `docs/ARCHITECTURE.md`.

## 1. Prerequisites

- Docker (Docker Desktop on Windows) running.
- Python. Tested on 3.12 and 3.14; the project's virtual environment lives in `backend/.venv`.

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt   # runtime + test dependencies
```

Use `requirements.txt` instead if you only need to run the app (no tests).

## 2. Local configuration (`.env`) and the services

Docker Compose reads a git-ignored `.env` file in the repository root. **It is required**, because the Compose file needs `POSTGRES_PASSWORD` (even to start only Minecraft). Create it once:

```powershell
Copy-Item .env.example .env
# then edit .env and set POSTGRES_PASSWORD to a long random value, for example the output of:
.\backend\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
```

Never commit `.env`. Variables: `POSTGRES_PASSWORD` (required), and optionally `POSTGRES_USER`, `POSTGRES_DB` (default `gamehosting`) and `POSTGRES_PORT` (default `5432`, host port on `127.0.0.1`; change it if it is taken, for example when Docker Desktop and a Docker Engine inside WSL both run on the machine).

### The Minecraft server

From the repository root:

```powershell
docker compose up -d minecraft   # create/start minecraft-dev (world data lives in ./data)
docker compose stop minecraft    # stop it (data is kept)
docker compose down              # remove all containers of this project (data and volumes are kept)
```

The `minecraft` service carries the label `game-hosting.managed: "true"`. **Only labelled containers can be controlled** by the CLI and the API; an unlabelled container is reported as "not found". If you created `minecraft-dev` before the label existed, run `docker compose up -d` again: Compose recreates it (the world in `./data` is untouched).

### The database (PostgreSQL 18)

```powershell
docker compose up -d postgres    # container game-hosting-db, data in the named volume postgres-data
docker compose ps                # it should become "healthy"
```

- It listens only on `127.0.0.1:5432`. It has no `game-hosting.managed` label on purpose, so the CLI and the API can never control it (`cli.py --server game-hosting-db status` reports `not_found`).
- `docker compose down` keeps the data; **`docker compose down -v` deletes the `postgres-data` volume, that is, the whole database.**
- The connection URL, used by the code that talks to the database, is `postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<db>` and is read from the `GAME_HOSTING_DATABASE_URL` environment variable of the process that needs it. The API does not use the database yet (it will in Phase 3 step 4). Treat the URL as a secret, because it contains the password.

### Database migrations (Alembic)

The tables are created and changed only by migrations, never by the application. Run them from `backend/`, with the database up and the URL in the environment of that terminal:

```powershell
cd backend
$pw = (Get-Content ..\.env | Where-Object { $_ -like 'POSTGRES_PASSWORD=*' }) -replace '^POSTGRES_PASSWORD=', ''
$env:GAME_HOSTING_DATABASE_URL = "postgresql+psycopg://gamehosting:$pw@127.0.0.1:5432/gamehosting"

.\.venv\Scripts\python.exe -m alembic current        # which revision the database is at
.\.venv\Scripts\python.exe -m alembic upgrade head   # apply every pending migration
.\.venv\Scripts\python.exe -m alembic check          # the models and the database must not differ
.\.venv\Scripts\python.exe -m alembic history        # list the revisions
```

- `alembic.ini` contains no URL or secret; `GAME_HOSTING_DATABASE_URL` is the only source. Without it Alembic stops with a message saying so.
- Run `upgrade head` after pulling code that adds a revision, and before starting an API version that needs the new schema. The API never migrates on its own.
- **`alembic downgrade base` drops all the tables and therefore all the data.** Use it only on a database you can lose. Downgrades are tested, but they are not a backup.
- To change the schema: edit `app/infrastructure/db/models.py`, then add a new hand-written revision under `backend/migrations/versions/` (copy the style of `0001_initial_schema.py` and use explicit constraint names). Never edit a revision that was already applied anywhere. The tests then check that the models and the migrations agree, and that every revision upgrades and downgrades.

## 3. The CLI

From `backend/` (it also works when called by path from elsewhere):

```powershell
.\.venv\Scripts\python.exe cli.py status
.\.venv\Scripts\python.exe cli.py start
.\.venv\Scripts\python.exe cli.py stop    --timeout 30
.\.venv\Scripts\python.exe cli.py restart --timeout 30
.\.venv\Scripts\python.exe cli.py logs    --tail 50
.\.venv\Scripts\python.exe cli.py --server other-name status
```

Exit codes: 0 ok, 1 other application error, 2 invalid usage, 3 server not found, 4 Docker unavailable, 5 operation failed.

## 4. The HTTP API

### Configuration (environment variables)

| Variable | Required | Meaning |
|---|---|---|
| `GAME_HOSTING_API_TOKEN` | yes | Shared secret, at least 16 characters. The API refuses to start without it. |
| `GAME_HOSTING_API_DOCS` | no | `1` or `true` enables `/docs`, `/redoc` and `/openapi.json`. Default: off. Do not enable in production. |
| `GAME_HOSTING_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Default: `INFO`. |

### Generate a token and start the API

From `backend/`:

```powershell
$env:GAME_HOSTING_API_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1
```

The API listens on `http://127.0.0.1:8000`. Stop it with Ctrl+C. The variable exists only in that terminal window and disappears when it is closed. Never put the token in the repository, in a `.env` file that is committed, or in a chat; generate a new one whenever you doubt it.

(Linux/macOS shell: `export GAME_HOSTING_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"`.)

### Calling it

Every `/servers` route needs `Authorization: Bearer <token>`. `/health` does not.

```powershell
$auth = @{ Authorization = "Bearer $env:GAME_HOSTING_API_TOKEN" }

Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Headers $auth http://127.0.0.1:8000/servers/minecraft-dev
Invoke-RestMethod -Headers $auth -Method Post http://127.0.0.1:8000/servers/minecraft-dev/start
Invoke-RestMethod -Headers $auth -Method Post "http://127.0.0.1:8000/servers/minecraft-dev/stop?timeout=30"
Invoke-RestMethod -Headers $auth -Method Post "http://127.0.0.1:8000/servers/minecraft-dev/restart?timeout=30"
Invoke-RestMethod -Headers $auth "http://127.0.0.1:8000/servers/minecraft-dev/logs?tail=50"
```

With curl (use `curl.exe` in PowerShell, because `curl` is an alias of `Invoke-WebRequest`):

```powershell
curl.exe -H "Authorization: Bearer $env:GAME_HOSTING_API_TOKEN" http://127.0.0.1:8000/servers/minecraft-dev
```

| Endpoint | Result |
|---|---|
| `GET /health` | `200 {"status":"ok"}` (never calls Docker) |
| `GET /servers/{name}` | `200 {"server":..., "status":...}`; a missing server gives status `not_found` |
| `POST /servers/{name}/start` | `200` with the final status (idempotent) |
| `POST /servers/{name}/stop?timeout=` | `200` with the final status (idempotent; `timeout` 1-300, default 30) |
| `POST /servers/{name}/restart?timeout=` | `200` with the final status |
| `GET /servers/{name}/logs?tail=` | `200` plain text (`tail` 1-1000, default 100) |

Errors: 401 missing/wrong token, 404 server not found (or not managed), 422 invalid input, 503 Docker unavailable, 500 operation failed. Error bodies are generic; the detail is in the API's log.

### Troubleshooting

- **The process exits immediately with `ConfigurationError`:** `GAME_HOSTING_API_TOKEN` is missing or shorter than 16 characters (or `GAME_HOSTING_LOG_LEVEL` is invalid). The message says which.
- **401:** the `Authorization: Bearer ...` header is missing or the token differs from the one the API was started with. A new terminal does not inherit a variable set in another one.
- **404 on a container that exists:** it lacks the `game-hosting.managed=true` label. Recreate it with `docker compose up -d`.
- **503:** Docker is not running or not reachable.
- **Port already in use:** add `--port 8001` to the uvicorn command.

## 5. Tests

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest                 # default suite; needs no Docker
.\.venv\Scripts\python.exe -m pytest -m integration  # needs a running Docker Engine
```

The integration tests create throwaway `alpine:3` containers named `game-hosting-it-<id>` (the image is pulled on first use), start a real API process on a free port, and remove everything afterwards. They never touch `minecraft-dev` or `./data`.
