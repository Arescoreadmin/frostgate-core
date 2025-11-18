import json
import os
import random
import string
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List

import httpx
from loguru import logger

BASE_DIR = Path(__file__).resolve().parents[2]
STATE_DIR = BASE_DIR / "state"
STATE_FILE = STATE_DIR / "chaos_status.json"

CORE_BASE_URL = os.getenv("FG_CORE_BASE_URL", "http://127.0.0.1:8080")


def _rand_tenant() -> str:
    return "tenant-" + random.choice(["demo", "alpha", "beta", "prod"])


def _rand_source() -> str:
    return random.choice(
        [
            "edge-gateway-1",
            "edge-gateway-2",
            "api-gateway",
            "ssh-bastion",
            "batch-ingestor",
        ]
    )


def _rand_ip() -> str:
    return ".".join(str(random.randint(1, 254)) for _ in range(4))


def _rand_failed_auths() -> int:
    # Rough split: many benign, some brute-force looking.
    return random.choice([0, 1, 2, 3, 4, 5, 10, 12, 15])


def _rand_event_type() -> str:
    return random.choice(
        [
            "auth",
            "auth",
            "auth",
            "suspicious_llm_usage",
        ]
    )


def build_telemetry() -> Dict[str, Any]:
    ts = datetime.now(timezone.utc).isoformat()
    return {
        "source": _rand_source(),
        "tenant_id": _rand_tenant(),
        "timestamp": ts,
        "payload": {
            "event_type": _rand_event_type(),
            "src_ip": _rand_ip(),
            "failed_auths": _rand_failed_auths(),
            "session_id": "".join(random.choices(string.ascii_lowercase + string.digits, k=12)),
        },
    }


def run_chaos_round(count: int = 10) -> Dict[str, Any]:
    client = httpx.Client(timeout=2.0)
    results: List[Dict[str, Any]] = []
    threat_counts: Dict[str, int] = {}
    errors: List[str] = []

    for i in range(count):
        telemetry = build_telemetry()
        try:
            resp = client.post(
                f"{CORE_BASE_URL}/defend",
                headers={
                    "Content-Type": "application/json",
                    "x-pq-fallback": "1",
                },
                json=telemetry,
            )
            resp.raise_for_status()
            data = resp.json()
            threat = data.get("threat_level", "unknown")
            threat_counts[threat] = threat_counts.get(threat, 0) + 1

            results.append(
                {
                    "tenant_id": telemetry["tenant_id"],
                    "source": telemetry["source"],
                    "threat_level": threat,
                    "rules_triggered": data.get("explain", {}).get("rules_triggered", []),
                }
            )
        except Exception as exc:
            msg = f"request_{i}_failed: {exc}"
            logger.error(msg)
            errors.append(msg)

    client.close()

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "core_base_url": CORE_BASE_URL,
        "requested": count,
        "results_sample": results[:5],
        "threat_counts": threat_counts,
        "errors": errors,
        "status": "ok" if not errors else "degraded",
        "version": "chaos-mvp-0.1",
    }
    return summary


def persist_status(payload: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(payload, indent=2))
    logger.info("chaos_status_updated", extra={"state_file": str(STATE_FILE)})


def main():
    logger.info("chaos_monkey_job_start", extra={"core_base_url": CORE_BASE_URL})
    summary = run_chaos_round(count=int(os.getenv("FG_CHAOS_COUNT", "10")))
    persist_status(summary)
    logger.info(
        "chaos_monkey_job_done",
        extra={
            "status": summary["status"],
            "threat_counts": summary["threat_counts"],
            "error_count": len(summary["errors"]),
        },
    )


if __name__ == "__main__":
    main()
