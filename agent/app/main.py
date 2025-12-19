# agent/app/main.py
from __future__ import annotations

import os
import time
import datetime
from typing import Any, Dict

from agent.app.core_client import CoreClient


def now_utc_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def log(msg: str) -> None:
    print(f"{now_utc_iso()}Z [agent] {msg}", flush=True)


def build_event(tenant_id: str, source: str) -> Dict[str, Any]:
    ts = now_utc_iso()
    return {
        "tenant_id": tenant_id,
        "source": source,
        "event_type": "auth.bruteforce",
        "timestamp": ts,  # <-- REQUIRED at top-level for /ingest
        "event": {
            "username": "admin",
            "ip": "10.0.0.50",
            "attempts": 10,
            "window_sec": 60,
            "timestamp": ts,  # optional, keep if you want
        },
    }


def main() -> None:
    base_url = os.getenv("FG_CORE_BASE_URL", "http://frostgate-core:8080").strip()
    api_key = os.getenv("FG_AGENT_API_KEY", "").strip()
    tenant_id = os.getenv("FG_AGENT_TENANT_ID", "local").strip()
    source = os.getenv("FG_AGENT_SOURCE", "agent").strip()
    interval = int(os.getenv("FG_AGENT_INTERVAL_SEC", "10").strip())

    client = CoreClient(base_url=base_url, api_key=api_key, tenant_id=tenant_id, source=source)

    log(f"starting; core={base_url} tenant={tenant_id} interval={interval}s")

    # Wait until core is ready
    while True:
        try:
            if client.ready():
                log("core ready")
                break
        except Exception as e:
            log(f"waiting for core... ({e})")
        time.sleep(2)

    # Main loop: ingest events, let core decide + persist
    while True:
        event = build_event(tenant_id=tenant_id, source=source)
        try:
            code, body = client.ingest(event)
            if code >= 300:
                log(f"ERROR: /ingest failed: {code} {body}")
            else:
                log(f"ok: /ingest {code}")
        except Exception as e:
            log(f"ERROR: ingest exception: {e}")

        time.sleep(interval)


if __name__ == "__main__":
    main()
