# tests/test_ingest_persists.py
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.main import app
from api.db import get_db

@pytest.fixture()
def client():
    return TestClient(app)
def _create_api_key(db):
    """
    Create an API key row compatible with current ApiKey ORM/table schema.
    """
    import uuid
    from api.db_models import ApiKey, hash_api_key

    raw = f"TEST_{uuid.uuid4().hex}"
    cols = set(ApiKey.__table__.columns.keys())

    kwargs = {}
    if "key_hash" in cols:
        kwargs["key_hash"] = hash_api_key(raw)
    if "prefix" in cols:
        kwargs["prefix"] = raw.split("_", 1)[0] + "_" if "_" in raw else (raw[:8] + "_")
    if "scopes_csv" in cols:
        kwargs["scopes_csv"] = "ingest:write"
    if "enabled" in cols:
        kwargs["enabled"] = True
    if "is_active" in cols:
        kwargs["is_active"] = True

    row = ApiKey(**kwargs)
    db.add(row)
    db.commit()
    return raw


    raw = "TEST_" + uuid.uuid4().hex
    hashed = hash_api_key(raw)

    obj = ApiKey()
    obj.prefix = raw[:16]
    obj.key_hash = hashed
    obj.scopes_csv = scopes
    obj.enabled = True

    db.add(obj)
    db.commit()
    return raw

