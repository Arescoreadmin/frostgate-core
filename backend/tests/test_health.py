import pytest
from fastapi.testclient import TestClient

@pytest.mark.parametrize("auth_enabled", [False, True])
def test_health_reflects_auth_enabled(build_app, auth_enabled: bool):
    app = build_app(auth_enabled)
    c = TestClient(app)
    data = c.get("/health").json()
    assert data["status"] == "ok"
    assert data["auth_enabled"] is auth_enabled
