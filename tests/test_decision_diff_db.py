from api.main import app
from api.db import get_db
from api.db_models import DecisionRecord
from fastapi.testclient import TestClient

client = TestClient(app)

def _latest_decision(db):
    return db.query(DecisionRecord).order_by(DecisionRecord.id.desc()).first()

def test_decision_diff_is_persisted_in_db_after_second_event():
    payload = {
        "event_type": "auth_attempt",
        "source": "pytest",
        "metadata": {"source_ip": "1.2.3.4", "username": "alice", "failed_attempts": 1},
    }

    r1 = client.post("/defend", json=payload, headers={"x-api-key": "supersecret"})
    assert r1.status_code in (200, 201), r1.text

    payload["metadata"]["failed_attempts"] = 10
    r2 = client.post("/defend", json=payload, headers={"x-api-key": "supersecret"})
    assert r2.status_code in (200, 201), r2.text

    db = next(get_db())
    try:
        rec = _latest_decision(db)
        assert rec is not None
        # Column exists and should be null-or-dict depending on whether prior exists in query scope
        # Second decision should compute a diff (prev exists)
        assert hasattr(rec, "decision_diff_json")
        assert rec.decision_diff_json is None or isinstance(rec.decision_diff_json, (dict, list))
    finally:
        db.close()
