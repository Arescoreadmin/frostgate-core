#!/usr/bin/env bash
set -euo pipefail

echo "== FrostGate Diff Discovery =="

echo
echo "## 1) Where are decisions persisted?"
rg -n "DecisionRecord\(|persist|insert|commit\(|add\(" api | head -n 120 || true

echo
echo "## 2) Where is the decisions table/model defined?"
rg -n "class .*Decision|__tablename__|CREATE TABLE|decisions" api | head -n 200 || true

echo
echo "## 3) What fields exist on TelemetryInput / DefendResponse?"
rg -n "class TelemetryInput|class DefendResponse|event_type|tenant_id|source|timestamp" api/defend.py api/schemas.py 2>/dev/null || true

echo
echo "## 4) Where are rules/score/threat_level computed?"
rg -n "def evaluate\(|rules_triggered|threat_level|score" api/defend.py api/engine.py api/rules.py 2>/dev/null || true

echo
echo "## 5) Any existing JSON-ish columns we can reuse?"
rg -n "JSON|json|Text\(|decision_|rules_triggered|explain" api | head -n 200 || true

echo
echo "## 6) DB engine and schema init paths"
rg -n "DB_ENGINE|SQLITE_PATH|create_engine|Base\.metadata|metadata\.create_all|sqlite" api | head -n 200 || true

echo
echo "## 7) Quick: where is ingest.py building records?"
if [[ -f api/ingest.py ]]; then
  echo "-- api/ingest.py: DecisionRecord usage --"
  rg -n "DecisionRecord\(|commit\(|flush\(|add\(" api/ingest.py || true
  echo
  echo "-- api/ingest.py: surrounding snippet --"
  python - <<'PY'
from pathlib import Path
p = Path("api/ingest.py")
s = p.read_text().splitlines()
hits = [i for i,l in enumerate(s) if "DecisionRecord(" in l]
print(f"Found {len(hits)} DecisionRecord( occurrences")
for idx in hits[:3]:
    for j in range(max(0, idx-12), min(len(s), idx+24)):
        print(f"{j+1:4d}: {s[j]}")
    print()
PY
else
  echo "⚠️ api/ingest.py not found."
fi

echo
echo "== Done. Paste this output if the next script refuses to apply =="
