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
- The connection URL, used by the API, the administration CLI and Alembic, is `postgresql+psycopg://<user>:<password>@127.0.0.1:5432/<db>` and is read from the `GAME_HOSTING_DATABASE_URL` environment variable of the process that needs it. Treat it as a secret, because it contains the password.

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

From `backend/` (it also works when called by path from elsewhere). It has two parts.

### Controlling a container by name (no database needed)

```powershell
.\.venv\Scripts\python.exe cli.py status
.\.venv\Scripts\python.exe cli.py start
.\.venv\Scripts\python.exe cli.py stop    --timeout 30
.\.venv\Scripts\python.exe cli.py restart --timeout 30
.\.venv\Scripts\python.exe cli.py logs    --tail 50
.\.venv\Scripts\python.exe cli.py --server other-name status
```

This is a developer tool: it works on container names, and only on containers carrying the `game-hosting.managed=true` label.

### Operator administration (needs `GAME_HOSTING_DATABASE_URL`)

These commands register plans and servers in the database. They are a **bootstrap for the operator**: until users can sign up (Phase 4), servers are created automatically (Phase 5) and plans are managed with billing (Phase 7), this is how data gets in.

```powershell
.\.venv\Scripts\python.exe cli.py plans add --slug dev --name "Dev plan" --memory-mb 4096 --cpu-millicores 2000
.\.venv\Scripts\python.exe cli.py plans list
.\.venv\Scripts\python.exe cli.py servers adopt minecraft-dev --owner you@example.com --plan dev --name "My world"
.\.venv\Scripts\python.exe cli.py servers list
```

- `servers adopt` registers an **existing, labelled container** as a platform server and prints its new platform `id`. The plan must exist; the owner is created if no user has that e-mail (the output says whether it was created or existing). `--name` is the display name customers see and is required on purpose: it must not default to the container name, which stays internal.
- Everything is stored in one transaction: if any step fails, nothing is kept.
- `servers list` is the operator view and shows the container name; the HTTP API never does.
- Set the database URL as described under "Database migrations" (same variable). Apply the migrations first.

### Accounts (`users`)

Each customer will have their own account. For now the operator creates them (open sign-up arrives with billing, Phase 7). Passwords are **never** given as command-line arguments, because they would be stored in the shell history and visible in the process list. The CLI asks for the password with a hidden prompt (twice) or, for scripts, reads one line from standard input.

```powershell
# Interactive: a hidden prompt asks for the password twice (nothing is echoed)
.\.venv\Scripts\python.exe cli.py users add --email ana@example.com
.\.venv\Scripts\python.exe cli.py users add --email boss@example.com --role admin

# Create it without a password and set one afterwards (it cannot sign in until then)
.\.venv\Scripts\python.exe cli.py users add --email ana@example.com --no-password
.\.venv\Scripts\python.exe cli.py users set-password --email ana@example.com

.\.venv\Scripts\python.exe cli.py users set-role --email ana@example.com --role admin
.\.venv\Scripts\python.exe cli.py users disable --email ana@example.com
.\.venv\Scripts\python.exe cli.py users enable --email ana@example.com
.\.venv\Scripts\python.exe cli.py users list
```

- Password rules: 12 to 128 characters, any printable character (spaces and accents are fine), no complexity rules. A long passphrase is better than a short "complex" one. The password is stored only as an Argon2id hash; no command ever prints a password or a hash.
- `--password-stdin` reads the first line of standard input, for example `Get-Content secret.txt | python cli.py users set-password --email ana@example.com --password-stdin`. Do not keep the password in a file you do not delete afterwards.
- There are two roles: `user` (sees and operates only their own servers; enforced by the services since step 4A.3 and reachable over HTTP in 4A.4) and `admin` (all servers). **The `admin` role can only be granted from this CLI.**
- An account created by adopting a container (`servers adopt`) has no password and cannot sign in until you set one.
- The sign-in logic exists (sessions, throttling, sign-out), but nothing exposes it over HTTP yet: the endpoints and the panel arrive in steps 4A.4 and 4B. Until then these accounts exist but cannot sign in from anywhere.
- **Changing a password or disabling an account ends all of that user's sessions** (so a stolen session does not survive). To end every session of an account without changing anything, for example after a suspected leak: `.\.venv\Scripts\python.exe cli.py users sign-out --email ana@example.com`.
- Failed sign-ins are throttled (5 per account and client, 30 per client and 20 per account in 15 minutes); the throttle lifts by itself. After pulling code that adds a migration, run `python -m alembic upgrade head` (the current schema revision is `0003`: it adds the `sessions` and `login_failures` tables).

Exit codes: 0 ok, 1 other application error, 2 invalid usage, 3 not found (server, plan or user), 4 Docker unavailable, 5 operation failed, 6 already exists, 7 database unavailable, 8 invalid data, 9 configuration (for example a missing database URL).

## 4. The HTTP API

Servers are addressed by their **platform id** (a UUID), never by container name. Register a server first (section 3); `servers list` shows the ids.

### Configuration (environment variables)

| Variable | Required | Meaning |
|---|---|---|
| `GAME_HOSTING_API_TOKEN` | yes | Shared secret, at least 16 characters. The API refuses to start without it. |
| `GAME_HOSTING_DATABASE_URL` | yes | `postgresql+psycopg://user:password@host:port/dbname`. The API refuses to start without it (or with an unusable one). The database is only contacted when a request needs it. Treat it as a secret. |
| `GAME_HOSTING_API_DOCS` | no | `1` or `true` enables `/docs`, `/redoc` and `/openapi.json`. Default: off. Do not enable in production. |
| `GAME_HOSTING_LOG_LEVEL` | no | `DEBUG`, `INFO`, `WARNING` or `ERROR`. Default: `INFO`. |

### Generate a token and start the API

From `backend/`, with the database up and migrated, and the URL set as in "Database migrations":

```powershell
$env:GAME_HOSTING_API_TOKEN = .\.venv\Scripts\python.exe -c "import secrets; print(secrets.token_urlsafe(32))"
.\.venv\Scripts\python.exe -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1
```

The API listens on `http://127.0.0.1:8000`. Stop it with Ctrl+C. The variables exist only in that terminal window and disappear when it is closed. Never put the token or the database URL in the repository, in a `.env` file that is committed, or in a chat; generate a new token whenever you doubt it.

(Linux/macOS shell: `export GAME_HOSTING_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"`.)

**Security reminder.** The token is a temporary shared secret that acts as the *operator*: it can act on every registered server. Ownership is stored but not enforced yet; per-customer access arrives with authentication (Phase 4). Keep the API bound to `127.0.0.1`.

### Calling it

Every `/servers` route needs `Authorization: Bearer <token>`. `/health` does not. Use a server id from `cli.py servers list`:

```powershell
$auth = @{ Authorization = "Bearer $env:GAME_HOSTING_API_TOKEN" }
$id = "<the server id>"

Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod -Headers $auth http://127.0.0.1:8000/servers
Invoke-RestMethod -Headers $auth http://127.0.0.1:8000/servers/$id
Invoke-RestMethod -Headers $auth -Method Post http://127.0.0.1:8000/servers/$id/start
Invoke-RestMethod -Headers $auth -Method Post "http://127.0.0.1:8000/servers/$id/stop?timeout=30"
Invoke-RestMethod -Headers $auth -Method Post "http://127.0.0.1:8000/servers/$id/restart?timeout=30"
Invoke-RestMethod -Headers $auth "http://127.0.0.1:8000/servers/$id/logs?tail=50"
```

With curl (use `curl.exe` in PowerShell, because `curl` is an alias of `Invoke-WebRequest`):

```powershell
curl.exe -H "Authorization: Bearer $env:GAME_HOSTING_API_TOKEN" http://127.0.0.1:8000/servers
```

| Endpoint | Result |
|---|---|
| `GET /health` | `200 {"status":"ok"}` (touches neither Docker nor the database) |
| `GET /servers` | `200` list of `{"id","name","game","status"}`, oldest first (needs Docker for the statuses) |
| `GET /servers/{id}` | `200` one server; a registered server whose container is gone has status `not_found` |
| `POST /servers/{id}/start` | `200` with the final status (idempotent) |
| `POST /servers/{id}/stop?timeout=` | `200` with the final status (idempotent; `timeout` 1-300, default 30) |
| `POST /servers/{id}/restart?timeout=` | `200` with the final status |
| `GET /servers/{id}/logs?tail=` | `200` plain text (`tail` 1-1000, default 100) |

Responses never contain the container name or the owner's details. Errors: 401 missing/wrong token, 404 unknown server (or its container is gone), 422 invalid input (including an id that is not a UUID, such as an old container name), 503 Docker or the database unavailable, 500 operation failed. Error bodies are generic; the detail is in the API's log.

### Troubleshooting

- **The process exits immediately with `ConfigurationError`:** `GAME_HOSTING_API_TOKEN` or `GAME_HOSTING_DATABASE_URL` is missing, too short or unusable (or `GAME_HOSTING_LOG_LEVEL` is invalid). The message says which and never shows secrets.
- **401:** the `Authorization: Bearer ...` header is missing or the token differs from the one the API was started with. A new terminal does not inherit a variable set in another one.
- **404 on a server you registered:** its container no longer exists or lost the `game-hosting.managed=true` label (recreate it with `docker compose up -d`).
- **422 on `/servers/minecraft-dev`:** servers are addressed by UUID now; take the id from `cli.py servers list`.
- **503 "database is unavailable":** PostgreSQL is not running or the URL is wrong; run `docker compose up -d postgres`. **503 "runtime is unavailable":** Docker is not running.
- **Port already in use:** add `--port 8001` to the uvicorn command.

## 5. Tests

From `backend/`:

```powershell
.\.venv\Scripts\python.exe -m pytest                 # default suite; needs no Docker
.\.venv\Scripts\python.exe -m pytest -m integration  # needs a running Docker Engine
```

The integration tests create throwaway containers (`alpine:3` named `game-hosting-it-<id>`, and a `postgres:18` with a random password; images are pulled on first use), start real API and CLI processes, and remove everything afterwards. They never touch `minecraft-dev`, `game-hosting-db` or `./data`.
