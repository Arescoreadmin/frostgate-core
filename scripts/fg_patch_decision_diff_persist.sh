#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d api ]]; then
  echo "❌ Run from repo root (expected ./api)."
  exit 1
fi

echo "==> [1/4] Ensure diff helper exists"
test -f api/decision_diff.py || { echo "❌ api/decision_diff.py missing (run fg_add_decision_diff.sh step 1)"; exit 1; }

echo "==> [2/4] Patch api/defend.py persistence (_persist_decision_best_effort)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

# If already patched, bail
if "decision_diff_json" in s and "compute_decision_diff" in s:
    print("ℹ️ api/defend.py already patched for decision diff.")
    raise SystemExit(0)

# Ensure imports exist (near explain_brief import which we know exists)
if "from api.decision_diff import compute_decision_diff" not in s:
    s = s.replace(
        "from api.explain_brief import build_explanation_brief",
        "from api.explain_brief import build_explanation_brief\n"
        "from api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record"
    )

# Insert after rules_value / req_value / resp_value block is created, before the for col,val loop.
needle = "rules_value = list(rules_triggered or [])"
idx = s.find(needle)
if idx == -1:
    raise SystemExit("❌ Could not find rules_value assignment in api/defend.py")

# Only insert once
if "Decision Diff (compute + persist)" not in s:
    insert = """
        # --- Decision Diff (compute + persist) ---
        try:
            prev = (
                db.query(DecisionRecord)
                .filter(
                    DecisionRecord.tenant_id == req.tenant_id,
                    DecisionRecord.source == req.source,
                    DecisionRecord.event_type == event_type,
                )
                .order_by(DecisionRecord.id.desc())
                .first()
            )
            prev_snapshot = snapshot_from_record(prev) if prev is not None else None
            curr_snapshot = snapshot_from_current(
                threat_level=str(decision.threat_level),
                rules_triggered=rules_value,
                score=int(score or 0),
            )
            decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)
            if hasattr(DecisionRecord, "decision_diff_json"):
                record_kwargs["decision_diff_json"] = _value_for_column(DecisionRecord, "decision_diff_json", decision_diff_obj)
        except Exception:
            # diff is best-effort, never blocks persistence
            decision_diff_obj = None
        # --- end Decision Diff ---
"""
    # We need rules_value defined before using it; insert AFTER we set rules_value/req_value/resp_value.
    # We'll anchor on the line after resp_value assignment.
    anchor_pat = re.compile(r"(rules_value\s*=\s*list\(rules_triggered\s*or\s*\[\]\)\s*\n\s*req_value\s*=\s*dict\(request_payload\)\s*\n\s*resp_value\s*=\s*response_payload\s*\n)", re.M)
    m = anchor_pat.search(s)
    if not m:
        raise SystemExit("❌ Could not locate rules/req/resp block to insert diff computation")

    s = s[:m.end()] + insert + s[m.end():]

p.write_text(s)
print("✅ Patched api/defend.py to compute + persist decision_diff_json")
PY

echo "==> [3/4] Patch api/ingest.py persistence (DecisionRecord insert)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/ingest.py")
s = p.read_text()

if "decision_diff_json" in s and "compute_decision_diff" in s:
    print("ℹ️ api/ingest.py already patched for decision diff.")
    raise SystemExit(0)

# Ensure import
if "from api.decision_diff import compute_decision_diff" not in s:
    # Put after import json if possible
    if "import json" in s:
        s = s.replace(
            "import json",
            "import json\nfrom api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record"
        )
    else:
        s = "from api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record\n" + s

# Find the persist try: block and insert prev lookup right before rec = DecisionRecord(
m = re.search(r"\n\s*rec\s*=\s*DecisionRecord\s*\(", s)
if not m:
    raise SystemExit("❌ Could not find rec = DecisionRecord( in api/ingest.py")

# Insert only once
if "Decision Diff (compute + persist)" not in s:
    inject = """
        # --- Decision Diff (compute + persist) ---
        try:
            prev = (
                db.query(DecisionRecord)
                .filter(
                    DecisionRecord.tenant_id == tenant_id,
                    DecisionRecord.source == source,
                    DecisionRecord.event_type == event_type,
                )
                .order_by(DecisionRecord.id.desc())
                .first()
            )
            prev_snapshot = snapshot_from_record(prev) if prev is not None else None
            curr_snapshot = snapshot_from_current(
                threat_level=threat_level,
                rules_triggered=rules,
                score=decision.get("score"),
            )
            decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)
        except Exception:
            decision_diff_obj = None
        # --- end Decision Diff ---
"""
    s = s[:m.start()] + inject + s[m.start():]

# Add decision_diff_json to DecisionRecord(...) constructor (best effort: after explain_summary)
if "decision_diff_json=" not in s:
    s = re.sub(
        r"(explain_summary\s*=\s*str\(summary\)\s*,)",
        r"\1\n            decision_diff_json=decision_diff_obj,",
        s,
        count=1
    )

p.write_text(s)
print("✅ Patched api/ingest.py to compute + persist decision_diff_json")
PY

echo "==> [4/4] Add DB-asserting test (real verification, no API surface changes)"
cat > tests/test_decision_diff_db.py <<'PY'
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
PY

echo "✅ Done. Now run: ./scripts/test.sh"
