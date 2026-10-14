# game_hosting
Automatic game hosting service

docker_client.py:

    Es nuestra capa de comunicación con Docker.

    Su trabajo es básicamente:

    "Sé hablar con Docker."

    No debería saber nada de usuarios, pagos, planes, Minecraft como producto, etc.

    Por ejemplo:

    DockerClient
    ├── start_container()
    ├── stop_container()
    ├── restart_container()
    ├── get_status()
    └── get_logs()

server_service.py:

    Es nuestra lógica de servidor.

    Su trabajo será:

    "Sé gestionar un servidor."

    Por ejemplo:

    ServerService
    ├── start_server()
    ├── stop_server()
    ├── restart_server()
    ├── get_server_status()
    └── get_server_logs()

    Y utilizará DockerClient.

cli.py:

    Es simplemente nuestra interfaz de pruebas.

    Nos permitirá hacer:

    python cli.py status
    python cli.py start
    python cli.py stop
    python cli.py restart
    python cli.py logs

A dia de 22/09/2026, la idea es llegar a tener:

                 FUTURO
                   │
                   ▼
             React / Web
                   │
                 HTTP
                   │
                   ▼
                FastAPI
                   │
                   ▼
             ServerService
                   │
                   ▼
              DockerClient
                   │
                   ▼
                Docker



A dia 23/09/2026, Ahora la cadena queda:

    ServerService
        │
        ▼
    DockerClient
        │
        ▼
    Docker SDK
        │
        ▼
    Docker Engine
        │
        ▼
    minecraft-dev

Esto es justo la arquitectura que acabamos de decidir.

## Registro de cambios

Registro breve de los commits relevantes, del más reciente al más antiguo. La fuente de verdad del estado del proyecto está en `docs/`.

- 2026-10-14 · Contexto del proyecto (`CLAUDE.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`) con el estado verificado.
- 2026-10-14 · Higiene del repositorio: `.gitignore` ampliado, `.pyc` fuera de Git, `requirements.txt` en UTF-8 y `pywin32` solo en Windows. Ciclo de Compose verificado (el mundo persiste).
