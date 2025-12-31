#!/usr/bin/env bash
set -euo pipefail

if [[ ! -d api ]]; then
  echo "❌ Run from repo root (expected ./api)."
  exit 1
fi

echo "==> [1/3] Patch api/decisions.py to include decision_diff"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/decisions.py")
s = p.read_text()

# 1) Add field to DecisionOut model
# Find class DecisionOut(BaseModel): and insert decision_diff field near rules_triggered/explain_summary
if "decision_diff" not in s:
    s = re.sub(
        r"(class\s+DecisionOut\(BaseModel\):\n[\s\S]*?)(\nclass\s+DecisionsPage\(BaseModel\):)",
        lambda m: (
            m.group(1)
            .rstrip()
            + (
                "\n    decision_diff: Optional[Any] = None\n"
                if "decision_diff" not in m.group(1)
                else "\n"
            )
            + m.group(2)
        ),
        s,
        count=1,
    )

# Ensure typing Any/Optional present (decisions.py already imports Optional/Any in your repo, but be safe)
if "from typing import" in s and "Any" not in s.split("from typing import",1)[1].split("\n",1)[0]:
    s = re.sub(r"from typing import ([^\n]+)\n", lambda m: "from typing import " + m.group(1).rstrip() + ", Any\n", s, count=1)
if "from typing import" in s and "Optional" not in s.split("from typing import",1)[1].split("\n",1)[0]:
    s = re.sub(r"from typing import ([^\n]+)\n", lambda m: "from typing import " + m.group(1).rstrip() + ", Optional\n", s, count=1)

# 2) In list_decisions(): add decision_diff from DecisionRecord.decision_diff_json
# Your code already uses _loads_json_text for rules_triggered_json/request/response; reuse it.
if "decision_diff=_loads_json_text(getattr(r, \"decision_diff_json\"" not in s:
    s = re.sub(
        r"(rules_triggered=_loads_json_text\(getattr\(r,\s*\"rules_triggered_json\".*?\)\),\n\s*explain_summary=getattr\(r,\s*\"explain_summary\".*?\),)",
        r"\1\n                decision_diff=_loads_json_text(getattr(r, \"decision_diff_json\", None)),",
        s,
        count=1,
        flags=re.S,
    )

# 3) In get_decision(): add decision_diff
if "decision_diff=_loads_json_text(getattr(r, \"decision_diff_json\"" not in s:
    s = re.sub(
        r"(rules_triggered=_loads_json_text\(getattr\(r,\s*\"rules_triggered_json\".*?\)\),\n\s*explain_summary=getattr\(r,\s*\"explain_summary\".*?\),)",
        r"\1\n            decision_diff=_loads_json_text(getattr(r, \"decision_diff_json\", None)),",
        s,
        count=1,
        flags=re.S,
    )

p.write_text(s)
print("✅ Patched api/decisions.py (DecisionOut + list/get include decision_diff)")
PY

echo "==> [2/3] Patch api/feed.py to include decision_diff on live items"
python - <<'PY'
from pathlib import Path
import re

p = Path("api/feed.py")
s = p.read_text()

# Try to detect the Feed item model name(s). In your repo feed items include decision_id, etc.
# We'll patch any BaseModel in feed.py that has decision_id field: add decision_diff optional.
def add_field_to_model(block: str) -> str:
    if "decision_diff" in block:
        return block
    # Insert after decision_id if present, else at end of class fields
    if re.search(r"\n\s*decision_id\s*:\s*", block):
        return re.sub(
            r"(\n\s*decision_id\s*:\s*[^\n]+\n)",
            r"\1    decision_diff: object | None = None\n",
            block,
            count=1,
        )
    return block.rstrip() + "\n    decision_diff: object | None = None\n"

# Patch the first class in feed.py that looks like the output model.
m = re.search(r"(class\s+\w+\(BaseModel\):\n[\s\S]*?\n)(\nclass|\Z)", s)
if not m:
    raise SystemExit("❌ Could not find any BaseModel class in api/feed.py")

# Prefer the one containing decision_id
models = list(re.finditer(r"(class\s+\w+\(BaseModel\):\n[\s\S]*?\n)(?=\nclass|\Z)", s))
patched = False
for mm in models:
    block = mm.group(1)
    if "decision_id" in block:
        new_block = add_field_to_model(block)
        if new_block != block:
            s = s[:mm.start(1)] + new_block + s[mm.end(1):]
            patched = True
        break

if not patched:
    # fallback: patch first model
    block = models[0].group(1)
    new_block = add_field_to_model(block)
    s = s[:models[0].start(1)] + new_block + s[models[0].end(1):]
    patched = True

# Now add diff into the feed mapping. Your feed code builds items from DecisionRecord rows.
# We’ll inject decision_diff by reading r.decision_diff_json (which is JSON in SQLAlchemy; may already be dict).
if "decision_diff=" not in s:
    # Look for FeedItem(...) construction
    s = re.sub(
        r"(decision_id\s*=\s*decision_id\s*,\n)",
        r"\1                decision_diff=getattr(r, \"decision_diff_json\", None),\n",
        s,
        count=1,
    )

p.write_text(s)
print("✅ Patched api/feed.py (item model + feed_live includes decision_diff)")
PY

echo "==> [3/3] Add tests proving diff appears in feed + decisions"
cat > tests/test_decision_diff_surfaces.py <<'PY'
import pytest
from fastapi.testclient import TestClient

from api.main import app
from tests._mk_test_key import mint_key

client = TestClient(app)

@pytest.mark.smoke
def test_decision_diff_exposed_in_decisions_and_feed():
    # generate two decisions with same (tenant/source/event_type) to create a diff
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

    # decisions list should include decision_diff (scope name may vary; mint_key accepts any scope string)
    key_dec = mint_key("decisions:read")
    dl = client.get("/decisions?limit=5", headers={"X-API-Key": key_dec})
    assert dl.status_code == 200, dl.text
    data = dl.json()
    items = data.get("items") or data.get("results") or []
    assert isinstance(items, list) and len(items) >= 1
    assert "decision_diff" in items[0]

    # feed live should include decision_diff too
    key_feed = mint_key("feed:read")
    fl = client.get("/feed/live?limit=5", headers={"X-API-Key": key_feed})
    assert fl.status_code == 200, fl.text
    fdata = fl.json()
    fitems = fdata.get("items") or fdata.get("results") or []
    assert isinstance(fitems, list) and len(fitems) >= 1
    assert "decision_diff" in fitems[0]
PY

echo "✅ Script complete. Run: ./scripts/test.sh"
