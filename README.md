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

Registro breve de los commits relevantes, del más reciente al más antiguo. La fuente de verdad del estado del proyecto está en `docs/`. Cómo arrancar todo: `docs/RUNNING.md`. Guía del checkpoint de Linux: `docs/LINUX_CHECKPOINT.md`.

- 2026-10-29 · Fase 3, paso 3: persistencia real. Tablas `users`, `plans` y `game_servers` en PostgreSQL con restricciones (también en la propia base de datos), migraciones con Alembic (`0001`, aplicada ya a tu base de datos de desarrollo), repositorios y unidad de trabajo para PostgreSQL con las mismas 28 pruebas de contrato que la versión en memoria, y errores de conexión sin contraseñas. 354 pruebas normales + 95 de integración, en Windows y en Ubuntu/Python 3.12. Cómo migrar: `docs/RUNNING.md`. Siguiente: `ServerRegistry` y rutas por UUID (paso 4).
- 2026-10-29 · Fase 3, paso 2: modelo de dominio (`User`, `Plan`, `GameServer`: objetos inmutables que se validan solos, con id UUID propio de la plataforma y nombre de contenedor interno), interfaces de repositorio y de unidad de trabajo, una implementación en memoria y pruebas de contrato reutilizables para PostgreSQL. 327 pruebas normales (Windows y Linux/Python 3.12); 17 de integración también pasan en Windows. Siguiente: tablas y migraciones reales (paso 3).
- 2026-10-29 · Fase 3, paso 1: infraestructura de base de datos. PostgreSQL 18 en `docker-compose.yml` (volumen con nombre, solo en `127.0.0.1`, sin etiqueta de gestionado), `.env`/`.env.example` para la contraseña, SQLAlchemy 2.1 + psycopg 3.3 y capa de conexión con errores sin contraseñas. 230 pruebas normales (Windows y Linux) + 17 de integración (Linux). Decisiones de la fase y topología de despliegue en `docs/`. Pendiente: integración en Windows cuando Docker Desktop esté encendido.
- 2026-10-29 · Checkpoint de Linux ejecutado en Ubuntu 24.04 (WSL2, con su propio Docker Engine 29.9.2): 215 pruebas normales + 12 de integración pasan, Minecraft, CLI y API funcionan, el mundo persiste y los límites de recursos (cgroups) se aplican. Hallazgos para producción en `docs/LINUX_CHECKPOINT.md`. Fase 2 cerrada.
- 2026-10-20 · Fase 2, paso 4 (parte local): logging configurable (`GAME_HOSTING_LOG_LEVEL`), pruebas de integración HTTP de principio a fin con un proceso uvicorn real, `docs/RUNNING.md` y `docs/LINUX_CHECKPOINT.md`. Corregido un fallo de portabilidad (la aplicación no arrancaba en Python 3.12 por una anotación de tipo), con prueba de regresión. 215 pruebas normales + 12 de integración, también en Python 3.12. Pendiente: que ejecutes el checkpoint de Linux.
- 2026-10-20 · Fase 2, paso 3: etiqueta `game-hosting.managed` (solo se gestionan contenedores etiquetados; `minecraft-dev` recreado con ella, `data/` intacta), token Bearer temporal con `GAME_HOSTING_API_TOKEN` (la API no arranca sin él), `/docs` desactivado por defecto y `app/api/entrypoint.py`. 199 pruebas normales + 6 de integración.
- 2026-10-20 · Fase 2, paso 2: endpoints HTTP de estado, start, stop, restart y logs con errores HTTP (404/503/500), validación de entradas y logging; comprobado con uvicorn real contra `minecraft-dev`. 109 pruebas normales.
- 2026-10-15 · Fase 2, paso 1: FastAPI + uvicorn, esqueleto `create_app` y `GET /health`; decisiones de seguridad de la fase (etiqueta de contenedores gestionados, token temporal, solo `127.0.0.1`) registradas en `docs/`. 78 pruebas normales.
- 2026-10-15 · Fase 1, paso 4 y cierre de la Fase 1: pruebas de integración (`pytest -m integration`, contenedor desechable), pruebas de Docker no disponible con el SDK real y comprobación manual con Docker Desktop apagado (código de salida 4); parada limpia de Minecraft verificada. 76 pruebas normales + 5 de integración.
- 2026-10-15 · Fase 1, paso 3: CLI (`python cli.py status|start|stop|restart|logs`) con códigos de salida; `ServerService` recibe su `DockerClient` desde el punto de entrada; 24 pruebas nuevas (73 en total).
- 2026-10-15 · Fase 1, paso 2: `ServerService` con `ServerStatus`, start/stop idempotentes, restart y logs; 29 pruebas nuevas (49 en total).
- 2026-10-15 · Fase 1, paso 1: `DockerClient` con start/stop/restart/logs, conexión perezosa y errores de aplicación (`app/errors.py`); 20 pruebas unitarias con pytest.
- 2026-10-14 · Contexto del proyecto (`CLAUDE.md`, `docs/ROADMAP.md`, `docs/ARCHITECTURE.md`) con el estado verificado.
- 2026-10-14 · Higiene del repositorio: `.gitignore` ampliado, `.pyc` fuera de Git, `requirements.txt` en UTF-8 y `pywin32` solo en Windows. Ciclo de Compose verificado (el mundo persiste).
