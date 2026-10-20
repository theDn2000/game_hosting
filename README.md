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

- 2026-10-20 · Fase 2, paso 2: endpoints HTTP de estado, start, stop, restart y logs con errores HTTP (404/503/500), validación de entradas y logging; comprobado con uvicorn real contra `minecraft-dev`. 109 pruebas normales.
- 2026-10-15 · Fase 2, paso 1: FastAPI + uvicorn, esqueleto `create_app` y `GET /health`; decisiones de seguridad de la fase (etiqueta de contenedores gestionados, token temporal, solo `127.0.0.1`) registradas en `docs/`. 78 pruebas normales.
- 2026-10-15 · Fase 1, paso 4 y cierre de la Fase 1: pruebas de integración (`pytest -m integration`, contenedor desechable), pruebas de Docker no disponible con el SDK real y comprobación manual con Docker Desktop apagado (código de salida 4); parada limpia de Minecraft verificada. 76 pruebas normales + 5 de integración.
- 2026-10-15 · Fase 1, paso 3: CLI (`python cli.py status|start|stop|restart|logs`) con códigos de salida; `ServerService` recibe su `DockerClient` desde el punto de entrada; 24 pruebas nuevas (73 en total).
- 2026-10-15 · Fase 1, paso 2: `ServerService` con `ServerStatus`, start/stop idempotentes, restart y logs; 29 pruebas nuevas (49 en total).
- 2026-10-15 · Fase 1, paso 1: `DockerClient` con start/stop/restart/logs, conexión perezosa y errores de aplicación (`app/errors.py`); 20 pruebas unitarias con pytest.
- 2026-10-14 · Contexto del proyecto (`CLAUDE.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`) con el estado verificado.
- 2026-10-14 · Higiene del repositorio: `.gitignore` ampliado, `.pyc` fuera de Git, `requirements.txt` en UTF-8 y `pywin32` solo en Windows. Ciclo de Compose verificado (el mundo persiste).
