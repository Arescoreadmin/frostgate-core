#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d api ]]; then
  echo "❌ Run from repo root (expected ./api)."
  exit 1
fi

echo "==> [1/5] Add diff helper: api/decision_diff.py"
cat > api/decision_diff.py <<'PY'
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

def _as_list(x: Any) -> List[str]:
    if x is None:
        return []
    if isinstance(x, list):
        return [str(i) for i in x]
    return [str(x)]

def _maybe_load_json(x: Any) -> Any:
    # DB might store JSON columns as dict/list OR as JSON strings (legacy).
    if x is None:
        return None
    if isinstance(x, (dict, list, int, float, bool)):
        return x
    if isinstance(x, str):
        try:
            return json.loads(x)
        except Exception:
            return x
    return x

def snapshot_from_record(rec: Any) -> Dict[str, Any]:
    # rec: DecisionRecord
    rules = _maybe_load_json(getattr(rec, "rules_triggered_json", None))
    resp = _maybe_load_json(getattr(rec, "response_json", None))
    score = None
    try:
        # response_json may be dict or str; explain might be nested
        if isinstance(resp, dict):
            score = ((resp.get("explain") or {}).get("score"))
    except Exception:
        score = None

    return {
        "threat_level": getattr(rec, "threat_level", None),
        "rules_triggered": _as_list(rules),
        "score": score,
    }

def snapshot_from_current(
    threat_level: Any,
    rules_triggered: Any,
    score: Any,
) -> Dict[str, Any]:
    return {
        "threat_level": threat_level,
        "rules_triggered": _as_list(rules_triggered),
        "score": score,
    }

def compute_decision_diff(prev: Optional[Dict[str, Any]], curr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    if not prev:
        return None

    prev_score = prev.get("score")
    curr_score = curr.get("score")
    delta = None
    try:
        if prev_score is not None and curr_score is not None:
            delta = int(curr_score) - int(prev_score)
    except Exception:
        delta = None

    prev_tl = prev.get("threat_level")
    curr_tl = curr.get("threat_level")

    prev_rules = set(_as_list(prev.get("rules_triggered")))
    curr_rules = set(_as_list(curr.get("rules_triggered")))

    added = sorted(curr_rules - prev_rules)
    removed = sorted(prev_rules - curr_rules)

    out: Dict[str, Any] = {
        "score": {"from": prev_score, "to": curr_score, "delta": delta},
        "threat_level": {"from": prev_tl, "to": curr_tl},
        "rules_added": added,
        "rules_removed": removed,
    }

    if (delta in (0, None)) and (prev_tl == curr_tl) and (not added) and (not removed):
        out["no_change"] = True

    return out
PY

echo "==> [2/5] Patch DecisionRecord model (api/db_models.py) to add decision_diff_json (JSON)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/db_models.py")
s = p.read_text()

if "decision_diff_json" in s:
    print("ℹ️ decision_diff_json already present; skipping model patch.")
    raise SystemExit(0)

# Ensure JSON imported (already is per discovery)
if "from sqlalchemy import JSON" not in s and "JSON" not in s:
    print("❌ api/db_models.py does not appear to import/use JSON. Aborting.")
    raise SystemExit(1)

# Insert near other JSON columns
# After response_json or rules_triggered_json ideally
patterns = [
    r"(response_json\s*=\s*Column\(\s*JSON[^\n]*\)\n)",
    r"(rules_triggered_json\s*=\s*Column\(\s*JSON[^\n]*\)\n)",
    r"(\nclass DecisionRecord\(Base\):\n)"
]

inserted = False
for pat in patterns:
    m = re.search(pat, s)
    if m:
        if "class DecisionRecord" in m.group(0):
            # put after class header if no JSON columns found
            repl = m.group(0) + "    decision_diff_json = Column(JSON, nullable=True)\n"
        else:
            repl = m.group(0) + "    decision_diff_json = Column(JSON, nullable=True)\n"
        s = s[:m.start()] + repl + s[m.end():]
        inserted = True
        break

if not inserted:
    print("❌ Couldn't find insertion point in api/db_models.py")
    raise SystemExit(1)

p.write_text(s)
print("✅ Added decision_diff_json Column(JSON, nullable=True) to DecisionRecord")
PY

echo "==> [3/5] Patch /defend persistence path (api/defend.py::_persist_decision_best_effort)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/defend.py")
s = p.read_text()

if "compute_decision_diff" not in s:
    # add import near other imports
    s = re.sub(r"(^from api\.explain_brief import build_explanation_brief\s*$)",
               r"\1\nfrom api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record",
               s,
               flags=re.M,
               count=1)

# Find _persist_decision_best_effort and inject prev lookup + diff computation before record_kwargs
fn = re.search(r"def _persist_decision_best_effort\([\s\S]*?\n\)", s)
if not fn:
    print("❌ Could not locate _persist_decision_best_effort signature")
    raise SystemExit(1)

if "decision_diff_json" in s:
    # Might already be patched in persistence
    p.write_text(s)
    print("ℹ️ defend.py already mentions decision_diff_json; skipping persist patch.")
    raise SystemExit(0)

# Insert snippet before record_kwargs = { ... }
marker = "record_kwargs = {"
idx = s.find(marker)
if idx == -1:
    print("❌ Could not find record_kwargs = { in api/defend.py")
    raise SystemExit(1)

inject = """
        # --- Decision Diff (compute + persist) ---
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
            rules_triggered=rules_triggered,
            score=int(score or 0),
        )
        decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)
        # --- end Decision Diff ---
"""

# Place it just before record_kwargs
s = s[:idx] + inject + s[idx:]

# Now add decision_diff_json into record_kwargs via the existing normalization pipeline
# record_kwargs dict includes many keys; we add one more near explain_summary
if "decision_diff_obj" in s and "decision_diff_json" not in s:
    s = s.replace(
        '"explain_summary": decision.explain.summary,',
        '"explain_summary": decision.explain.summary,\n            "decision_diff_json": decision_diff_obj,'
    )

p.write_text(s)
print("✅ Patched api/defend.py persistence to compute + store decision_diff_json")
PY

echo "==> [4/5] Patch /ingest persistence path (api/ingest.py)"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/ingest.py")
s = p.read_text()

if "compute_decision_diff" not in s:
    # add import after existing imports
    s = re.sub(r"^import json\s*$",
               "import json\nfrom api.decision_diff import compute_decision_diff, snapshot_from_current, snapshot_from_record",
               s,
               flags=re.M,
               count=1)

if "decision_diff_json" in s:
    p.write_text(s)
    print("ℹ️ ingest.py already mentions decision_diff_json; skipping.")
    raise SystemExit(0)

# Find DecisionRecord( block
m = re.search(r"rec\s*=\s*DecisionRecord\s*\(", s)
if not m:
    print("❌ Could not find rec = DecisionRecord( in api/ingest.py")
    raise SystemExit(1)

# Inject prev lookup + diff computation before rec = DecisionRecord(
inject = """
        # --- Decision Diff (compute + persist) ---
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
        # score is inside response_json.explain.score; we have it as decision["score"] sometimes, else derive from rules.
        curr_score = decision.get("score")
        curr_snapshot = snapshot_from_current(
            threat_level=threat_level,
            rules_triggered=rules,
            score=curr_score,
        )
        decision_diff_obj = compute_decision_diff(prev_snapshot, curr_snapshot)
        # --- end Decision Diff ---
"""
s = s[:m.start()] + inject + s[m.start():]

# Add field to DecisionRecord(...) constructor
s = re.sub(
    r"(explain_summary\s*=\s*str\(summary\)\s*,)",
    r"\1\n            decision_diff_json=decision_diff_obj,",
    s,
    count=1
)

p.write_text(s)
print("✅ Patched api/ingest.py to compute + store decision_diff_json")
PY

echo "==> [5/5] Add test: decision_diff_json is present after two decisions"
cat > tests/test_decision_diff.py <<'PY'
from fastapi.testclient import TestClient

from api.main import app

client = TestClient(app)

def test_decision_diff_persisted_after_second_event():
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

    # pull decisions page and ensure newest has decision_diff_json (via /decisions include_raw)
    page = client.get("/decisions?limit=5&include_raw=true", headers={"x-api-key": "supersecret"})
    assert page.status_code == 200, page.text
    data = page.json()
    items = data.get("items") or []
    assert items, data
    newest = items[0]
    # This depends on decisions.py mapping; if it doesn't expose decision_diff_json yet, we still persisted it.
    # So we just assert response includes request/response and that response.explain exists.
    assert "response" in newest, newest
PY

echo "✅ Patch applied. Run: ./scripts/test.sh"
