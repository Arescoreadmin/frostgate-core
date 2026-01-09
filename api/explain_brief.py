from __future__ import annotations

from typing import Any, Dict, List, Optional


def build_explanation_brief(
    event_type: str,
    triggered_rules: List[str],
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Deterministic, testable 1-liner explanation for humans.
    No LLMs, no fluff, no internal scoring jargon.
    """
    metadata = metadata or {}
    source_ip = metadata.get("source_ip") or metadata.get("ip") or "an unknown source"
    username = metadata.get("username") or metadata.get("user") or "an unknown user"

    if not triggered_rules:
        return "No threat rules triggered for this event."

    # Pick primary rule: first triggered (keep deterministic)
    primary = triggered_rules[0]

    templates = {
        "auth_bruteforce": f"Repeated failed logins from {source_ip} triggered the brute-force rule.",
        "brute_force": f"Repeated failed logins from {source_ip} triggered the brute-force rule.",
        "rate_limit": f"High request rate from {source_ip} triggered the rate-limit rule.",
        "suspicious_login": f"Suspicious login activity for {username} from {source_ip} triggered a login anomaly rule.",
    }

    return templates.get(primary, f"Suspicious behavior matched rule '{primary}'.")
