from fastapi.testclient import TestClient

from app.api.routes import health
from app.database.session import DatabaseStatus


def test_health_reports_uninitialized_database(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "healthy",
        "application": "CredSure",
        "database": "not_initialized",
    }


def test_health_reports_unavailable_database(client: TestClient, monkeypatch) -> None:
    monkeypatch.setattr(health, "get_database_status", lambda: DatabaseStatus.UNAVAILABLE)

    response = client.get("/api/health")

    assert response.status_code == 503
    assert response.json() == {
        "status": "unhealthy",
        "application": "CredSure",
        "database": "unavailable",
    }


def test_unknown_route_uses_error_envelope(client: TestClient) -> None:
    response = client.get("/api/does-not-exist")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "http_404"


def test_cors_allows_vite_dev_origin(client: TestClient) -> None:
    response = client.options(
        "/api/health",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == "http://localhost:5173"
