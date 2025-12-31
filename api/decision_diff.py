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
    """
    Contract:
      - If prev is None/empty => return None
      - Otherwise return:
        {
          "changes": [ ... ],     # list (possibly empty)
          "prev": prev,           # optional
          "curr": curr,           # optional
          "summary": "..."        # optional
        }
    """
    if not prev:
        return None

    changes: list[dict[str, Any]] = []

    # score delta
    prev_score = prev.get("score")
    curr_score = curr.get("score")
    delta = None
    try:
        if prev_score is not None and curr_score is not None:
            delta = int(curr_score) - int(prev_score)
    except Exception:
        delta = None

    if (prev_score != curr_score) or (delta not in (0, None)):
        changes.append({
            "field": "score",
            "from": prev_score,
            "to": curr_score,
            "delta": delta,
        })

    # threat level change
    prev_tl = prev.get("threat_level")
    curr_tl = curr.get("threat_level")
    if prev_tl != curr_tl:
        changes.append({
            "field": "threat_level",
            "from": prev_tl,
            "to": curr_tl,
        })

    # rules added/removed
    prev_rules = set(_as_list(prev.get("rules_triggered")))
    curr_rules = set(_as_list(curr.get("rules_triggered")))
    added = sorted(curr_rules - prev_rules)
    removed = sorted(prev_rules - curr_rules)

    if added:
        changes.append({
            "field": "rules_triggered",
            "op": "added",
            "values": added,
        })
    if removed:
        changes.append({
            "field": "rules_triggered",
            "op": "removed",
            "values": removed,
        })

    # optional summary (keep it short, not poetic)
    if not changes:
        summary = "No material change vs previous decision."
    else:
        bits = []
        if prev_tl != curr_tl:
            bits.append(f"threat {prev_tl}→{curr_tl}")
        if added:
            bits.append(f"+{len(added)} rule(s)")
        if removed:
            bits.append(f"-{len(removed)} rule(s)")
        if delta not in (0, None):
            bits.append(f"score Δ{delta}")
        summary = ", ".join(bits) if bits else "Decision changed."

    return {
        "changes": changes,
        "prev": prev,
        "curr": curr,
        "summary": summary,
    }

    changes: list[dict[str, Any]] = []

    # score change
    prev_score = prev.get("score")
    curr_score = curr.get("score")
    delta = None
    try:
        if prev_score is not None and curr_score is not None:
            delta = int(curr_score) - int(prev_score)
    except Exception:
        delta = None

    if prev_score != curr_score:
        ch = {"field": "score", "from": prev_score, "to": curr_score}
        if delta is not None:
            ch["delta"] = delta
        changes.append(ch)

    # threat level change
    prev_tl = prev.get("threat_level")
    curr_tl = curr.get("threat_level")
    if prev_tl != curr_tl:
        changes.append({"field": "threat_level", "from": prev_tl, "to": curr_tl})

    # rules diff
    prev_rules = set(_as_list(prev.get("rules_triggered")))
    curr_rules = set(_as_list(curr.get("rules_triggered")))
    added = sorted(curr_rules - prev_rules)
    removed = sorted(prev_rules - curr_rules)
    if added or removed:
        changes.append({"field": "rules_triggered", "added": added, "removed": removed})

    # optional summary
    parts = []
    if prev_tl != curr_tl:
        parts.append(f"threat_level {prev_tl}->{curr_tl}")
    if prev_score != curr_score:
        if delta is None:
            parts.append(f"score {prev_score}->{curr_score}")
        else:
            parts.append(f"score {prev_score}->{curr_score} ({delta:+d})")
    if added:
        parts.append("+" + ",".join(added))
    if removed:
        parts.append("-" + ",".join(removed))
    summary_text = "; ".join(parts) if parts else "no change"

    return {
        "changes": changes,
        "prev": prev,
        "curr": curr,
        "summary": summary_text,
    }

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
