# Linux checkpoint

A guided check that the project works on Linux, which is the intended production platform. Do it before starting Phase 3. It needs about an hour the first time.

## Result: executed on 2026-10-29 (WSL2, Ubuntu 24.04)

Run by Claude from the VS Code terminal in a native Ubuntu 24.04.5 distribution under WSL2, with its **own Docker Engine 29.9.2** (cgroup v2, systemd driver, overlayfs), Python 3.12.3 and the repository cloned from the local commit `7ebdb22` into the Linux home directory (ext4). Docker Desktop's integration was not involved.

| Check | Result |
|---|---|
| `pip install -r requirements-dev.txt`, `pip check` | OK; `pywin32` correctly **not** installed |
| Default test suite | **215 passed** |
| Integration tests (native Linux Docker Engine) | **12 passed** (throwaway `alpine:3` containers; none left over) |
| `docker compose up -d` with the label | `minecraft-dev` `healthy`; label `game-hosting.managed=true` present |
| CLI: status, stop (twice), start, restart, logs, unknown server | all as expected; unknown server exits with 3 |
| API as an unprivileged user (`dani`, not root) | `/health` 200; no token 401; token 200; stop/start 200; unknown server 404; `/docs` 404; the token is absent from the log |
| API without a token | refuses to start (exit 1) |
| World persistence (`compose down`, then `up`) | identical: 26 files, 2360 KB, 4 region files, loaded normally |
| Graceful stop through the CLI | about 0.9 s, container exit code 0, log shows "Stopping server" and "Saving worlds" |
| cgroup limits (`--memory 64m --cpus 0.5`) | enforced (`memory.max=67108864`, `cpu.max=50000 100000`) |

**Not covered, because WSL2 is not a server:** real network exposure (WSL2 shares one NAT network between distributions, so the Linux test server used port 25566 to avoid a clash with Docker Desktop's 25565), performance, firewalling, and anything about a hosting provider. A VPS check is still advisable before the first real deployment.

### Findings that matter for production

1. **`./data` is owned by uid 1000, whatever user ran Compose.** The itzg image runs the server as uid/gid 1000 and fixes the ownership of `/data` itself. `data/world/level.dat` is mode `600`, so only uid 1000 (or root) can read the world's main file. A backup or restore job running as another user will fail unless it runs as uid 1000, as root, or from inside the container (design this in Phase 6; the image's `UID`/`GID` settings may also help).
2. **Membership of the `docker` group is root-equivalent.** The API ran fine as an ordinary user in that group. In production, use a dedicated unprivileged user for the API, keep the Docker socket off the network, and consider rootless Docker or a socket proxy later.
3. **Docker Desktop and a native engine can coexist in WSL2**, but they share ports and memory; stop one before testing the other if you hit "port already in use".
4. **WSL appends the Windows `PATH` to Linux by default**, which makes Linux find Windows tools (`docker`, `python`). The environment was configured with `appendWindowsPath=false`.

## How the Ubuntu environment was built (reproducible)

Done without administrator rights, because WSL2 was already installed for Docker Desktop:

1. `wsl --install -d Ubuntu-24.04 --no-launch`
2. As root (`wsl -d Ubuntu-24.04 -u root`): create the user `dani` (`useradd -m -s /bin/bash -G sudo dani`) and write `/etc/wsl.conf` with `[boot] systemd=true`, `[user] default=dani` and `[interop] appendWindowsPath=false`; then `wsl --terminate Ubuntu-24.04`.
3. Install `ca-certificates curl gnupg git python3-venv python3-pip`, add Docker's apt repository after **checking the key fingerprint** (`9DC858229FC7DD38854AE2D88D81803C0EBFCD88`), install `docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin`, `usermod -aG docker dani` and `systemctl enable --now docker`.
4. As `dani`: `git clone /mnt/c/Users/Dani/Documents/Repositorios/mmorpg/game_hosting ~/game_hosting` (a local clone of the committed state, so no `git push` was needed), create `backend/.venv`, install `requirements-dev.txt`.

The `dani` account has no password set. To use `sudo` from inside Ubuntu, set one with `wsl -d Ubuntu-24.04 -u root passwd dani`. Open the environment with `wsl -d Ubuntu-24.04` (it starts in the Windows directory; run `cd ~/game_hosting`).

The rest of this document is the original step-by-step guide, still valid for repeating the check on a fresh machine or a VPS.

## What we want to learn

1. The dependencies install on Linux (`pywin32` must be skipped).
2. The full test suite passes, including the integration tests against a real Linux Docker Engine.
3. `minecraft-dev` starts on Linux with its world directory `./data` (file ownership is the usual surprise).
4. The CLI and the API work end to end.
5. Which Linux-specific details need to go into the production setup (Docker socket permissions, running as a non-root user).

Already verified without Linux: the whole suite (215 default and 12 integration tests) passes on **Python 3.12** and **3.14** on Windows. Python 3.12 is the default `python3` of Ubuntu 24.04, so no extra Python is needed.

## 1. Choose where to test

| Option | Pros | Cons |
|---|---|---|
| **A. Ubuntu 24.04 in WSL2 with its own Docker Engine** (recommended to start) | Free, quick, a real Linux kernel and filesystem | Shares hardware with Windows; networking and performance are not representative of a server |
| **B. A cheap Ubuntu 24.04 VPS** | The closest thing to production; also tells you what a provider offers | Costs a few euros; needs SSH and firewall care |

Both give the same result for this checkpoint. The key rule for option A: **install Docker Engine inside Ubuntu** and make sure Docker Desktop's WSL integration is **off** for that distribution (Docker Desktop, Settings, Resources, WSL integration). Otherwise Ubuntu would talk to the Windows engine and you would not be testing native Linux behaviour.

## 2. Prepare Ubuntu

### Option A only: install Ubuntu in WSL2 (PowerShell, as administrator)

```powershell
wsl --install -d Ubuntu-24.04
```

Open Ubuntu, create your user, then enable systemd (needed by Docker's service):

```bash
sudo tee /etc/wsl.conf > /dev/null <<'EOF'
[boot]
systemd=true
EOF
```

Back in PowerShell: `wsl --shutdown`, then open Ubuntu again.

### Install Docker Engine (both options)

Follow the official instructions for Ubuntu at <https://docs.docker.com/engine/install/ubuntu/> (they change over time, so prefer them to copying commands from here). In short: add Docker's apt repository, then install `docker-ce`, `docker-ce-cli`, `containerd.io`, `docker-buildx-plugin` and `docker-compose-plugin`.

Then let your user use Docker without `sudo`:

```bash
sudo usermod -aG docker "$USER"
# log out and back in (or run: newgrp docker)
docker run --rm hello-world
```

> **Security note.** Membership of the `docker` group is effectively root access to the machine. On a real server, the API must run as a dedicated unprivileged user, and the Docker socket must never be reachable from the network. Writing that setup is a later task; for this checkpoint use your normal user.

### Install Python tooling and Git

```bash
sudo apt-get update
sudo apt-get install -y git python3-venv python3-pip
python3 --version      # expect 3.12.x
```

## 3. Get the code

On Windows, first publish your latest commits (the local branch is ahead of GitHub): `git push` from the repository. Then, in Ubuntu, clone it **inside the Linux filesystem** (your home directory), not under `/mnt/c`, because that path is slow and its file permissions are not those of a real Linux disk:

```bash
cd ~
git clone https://github.com/theDn2000/game_hosting.git
cd game_hosting
git log --oneline -3
```

## 4. Install and test

```bash
cd ~/game_hosting/backend
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
.venv/bin/python -m pip list | grep -i pywin32 || echo "pywin32 not installed (correct)"
.venv/bin/python -m pip check

.venv/bin/python -m pytest                  # default suite, no Docker needed
.venv/bin/python -m pytest -m integration   # needs Docker; pulls alpine:3 the first time
```

Expected: 215 default tests and 12 integration tests pass. Copy the last lines of both runs.

## 5. The Minecraft server on Linux

```bash
cd ~/game_hosting
# Compose requires a .env file (Phase 3 added a database service that needs POSTGRES_PASSWORD):
cp .env.example .env
sed -i "s|^POSTGRES_PASSWORD=.*|POSTGRES_PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')|" .env
docker compose up -d minecraft
docker compose ps
docker logs --tail 20 minecraft-dev
ls -ln data | head          # who owns the files the server created?
```

(The results table above was produced before the database service existed, so it started only Minecraft. To check the database as well, run `docker compose up -d postgres` and see `docs/RUNNING.md`; that was verified on Linux on 2026-10-29, see the roadmap.)

The first start downloads the server and creates `./data`. Expected: the container becomes `healthy` within a minute or two. If it does not, send me `docker logs minecraft-dev` and the output of `ls -ln data`; the most likely cause is file ownership of the bind-mounted directory.

## 6. CLI and API

```bash
cd ~/game_hosting/backend
.venv/bin/python cli.py status
.venv/bin/python cli.py stop
.venv/bin/python cli.py start
.venv/bin/python cli.py logs --tail 5

export GAME_HOSTING_API_TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
.venv/bin/python -m uvicorn --factory app.api.entrypoint:build_app --host 127.0.0.1
```

In a second Ubuntu terminal (the variable is per terminal, so set it there too, or paste the same value):

```bash
curl -s http://127.0.0.1:8000/health
curl -s -H "Authorization: Bearer $GAME_HOSTING_API_TOKEN" http://127.0.0.1:8000/servers/minecraft-dev
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8000/servers/minecraft-dev   # expect 401
```

## 7. What to report back

Send me (or paste into the conversation), without the token:

- `python3 --version` and `docker version --format '{{.Server.Version}}'`.
- The last lines of both pytest runs.
- `docker compose ps` and `ls -ln data | head`.
- The output of the `cli.py` and `curl` commands above.
- Anything that failed or looked strange.

With that I will mark the Linux checkpoint in `docs/ROADMAP.md`, fix whatever it exposed, and write down the Linux-specific production notes.

## Cleanup

```bash
docker compose down        # removes the container; ./data is kept
rm -rf ~/game_hosting      # optional: removes the clone and its test world
```
