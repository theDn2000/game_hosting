"""HTTP API.

The API is a thin layer over ServerService: routes validate input, call the
service and return its result. The service is injected by `create_app`, so the
caller (production entry point or tests) decides which runtime is behind it.
"""

from fastapi import FastAPI


def create_app(service):
    app = FastAPI(title="game_hosting API")
    app.state.service = service

    @app.get("/health")
    def health():
        # Liveness only: deliberately does not call Docker.
        return {"status": "ok"}

    return app
