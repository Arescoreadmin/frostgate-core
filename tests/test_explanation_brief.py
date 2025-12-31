from fastapi.testclient import TestClient

try:
    from api.main import app
except Exception as e:
    raise RuntimeError("Could not import api.main:app. Adjust import in tests/test_explanation_brief.py") from e

client = TestClient(app)

def test_defend_returns_explanation_brief():
    payload = {
        "event_type": "auth_attempt",
        "source": "pytest",
        "metadata": {
            "source_ip": "1.2.3.4",
            "username": "alice",
            # include anything your brute-force rule expects if you have one
            "failed_attempts": 10,
        },
    }
    r = client.post("/defend", json=payload, headers={"x-api-key": "supersecret"})
    assert r.status_code in (200, 201), r.text
    data = r.json()

    assert "explanation_brief" in data, data
    assert isinstance(data["explanation_brief"], str)
    assert len(data["explanation_brief"]) > 0
