from fastapi.testclient import TestClient

from app.api.app import create_app


class ExplodingService:
    """Fails the test if the API touches the service."""

    def __getattr__(self, name):
        raise AssertionError(f"service.{name} must not be used by /health")


def test_health_returns_ok_without_using_the_service():
    client = TestClient(create_app(ExplodingService()))
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_unknown_route_is_404():
    client = TestClient(create_app(ExplodingService()))
    assert client.get("/nope").status_code == 404
