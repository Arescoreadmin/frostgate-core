from pathlib import Path
import re

p = Path("api/decision_diff.py")
s = p.read_text()

m = re.search(r"(?ms)^def\s+compute_decision_diff\([^\)]*\)\s*->\s*Optional\[Dict\[str,\s*Any\]\]\s*:\s*\n(.*?)(?=^\S)", s)
if not m:
    raise SystemExit("❌ compute_decision_diff() not found in api/decision_diff.py")

new_func = """def compute_decision_diff(prev: Optional[Dict[str, Any]], curr: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    # Contract:
    # - If prev is None => return None (no baseline)
    # - Otherwise => return object with "changes" always present (possibly empty)
    if prev is None:
        return None

    def _norm(v: Any) -> Any:
        # Normalize for stable comparison (esp lists)
        if isinstance(v, list):
            return list(v)
        return v

    changes: list[dict[str, Any]] = []

    # Compare a stable, MVP-relevant subset (expand later if you want)
    keys = ("threat_level", "score", "rules_triggered")
    for k in keys:
        pv = _norm(prev.get(k))
        cv = _norm(curr.get(k))
        if pv != cv:
            changes.append({"field": k, "from": pv, "to": cv})

    # Optional summary
    if not changes:
        summary_text = "No material change vs previous decision."
    else:
        parts = []
        for c in changes:
            parts.append(f"{c['field']}:{c['from']}→{c['to']}")
        summary_text = "Changed: " + ", ".join(parts)

    return {
        "changes": changes,
        "prev": prev,     # optional but included per contract
        "curr": curr,     # optional but included per contract
        "summary": summary_text,
    }
"""

# Replace the whole function definition block
s2 = s[:m.start()] + new_func + "\n\n" + s[m.end():]
p.write_text(s2)

print("✅ Patched compute_decision_diff() contract + change detection")
