from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid
import hashlib
import pathlib
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterator, Optional
from urllib import request as urlrequest
from urllib.error import URLError, HTTPError


# -----------------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------------

DEFAULT_CORE_URL = os.getenv("FG_CORE_URL", "http://localhost:18080").rstrip("/")
DEFAULT_QUEUE_DIR = os.getenv("FG_AGENT_QUEUE_DIR", "/var/lib/frostgate/agent_queue")
DEFAULT_SOURCE = os.getenv("FG_AGENT_SOURCE", "edge1")
DEFAULT_TENANT = os.getenv("FG_AGENT_TENANT_ID", "t1")

SEND_INTERVAL_MS = int(os.getenv("FG_AGENT_SEND_INTERVAL_MS", "500"))
MAX_BATCH = int(os.getenv("FG_AGENT_MAX_BATCH", "50"))
MAX_BACKOFF_MS = int(os.getenv("FG_AGENT_MAX_BACKOFF_MS", "10000"))

# NOTE: set this in env when running locally
AGENT_KEY = os.getenv("FG_AGENT_KEY", "")


# -----------------------------------------------------------------------------
# Models
# -----------------------------------------------------------------------------

@dataclass(frozen=True)
class TelemetryEvent:
    source: str
    tenant_id: str
    timestamp: str  # ISO8601 UTC string
    payload: Dict[str, Any]
    event_id: str

    def to_wire(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "payload": self.payload,
        }


# -----------------------------------------------------------------------------
# Deterministic ID
# -----------------------------------------------------------------------------

def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def deterministic_event_id(source: str, tenant_id: str, timestamp_iso: str, payload: Dict[str, Any]) -> str:
    """
    Must match server-side logic: stable, predictable, content-addressed.
    """
    canonical_payload = json.dumps(payload or {}, sort_keys=True, separators=(",", ":"))
    base = f"{tenant_id}|{source}|{timestamp_iso}|{canonical_payload}".encode("utf-8")
    return hashlib.sha256(base).hexdigest()


# -----------------------------------------------------------------------------
# Disk Queue (atomic)
# -----------------------------------------------------------------------------

class DiskQueue:
    """
    Queue layout:
      <queue_dir>/
        pending/
          <event_id>.<nonce>.json
        sent/
          <event_id>.<nonce>.json
        dead/
          <event_id>.<nonce>.json
    """
    def __init__(self, root: str) -> None:
        self.root = pathlib.Path(root)
        self.pending = self.root / "pending"
        self.sent = self.root / "sent"
        self.dead = self.root / "dead"
        for p in (self.pending, self.sent, self.dead):
            p.mkdir(parents=True, exist_ok=True)

    def enqueue(self, ev: TelemetryEvent) -> pathlib.Path:
        nonce = uuid.uuid4().hex[:10]
        fname = f"{ev.event_id}.{nonce}.json"
        tmp = self.pending / f".{fname}.tmp"
        final = self.pending / fname

        data = {
            "event_id": ev.event_id,
            "source": ev.source,
            "tenant_id": ev.tenant_id,
            "timestamp": ev.timestamp,
            "payload": ev.payload,
        }

        tmp.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")
        tmp.replace(final)  # atomic on same filesystem
        return final

    def iter_pending(self, limit: int) -> Iterator[pathlib.Path]:
        files = sorted(self.pending.glob("*.json"), key=lambda p: p.stat().st_mtime)
        for p in files[:limit]:
            yield p

    def mark_sent(self, path: pathlib.Path) -> pathlib.Path:
        dest = self.sent / path.name
        path.replace(dest)
        return dest

    def mark_dead(self, path: pathlib.Path) -> pathlib.Path:
        dest = self.dead / path.name
        path.replace(dest)
        return dest


# -----------------------------------------------------------------------------
# HTTP client (no external deps)
# -----------------------------------------------------------------------------

def post_json(url: str, api_key: str, payload: Dict[str, Any], timeout_s: int = 10) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    req = urlrequest.Request(
        url=url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "x-api-key": api_key,
        },
    )
    try:
        with urlrequest.urlopen(req, timeout=timeout_s) as resp:
            resp_body = resp.read().decode("utf-8", errors="replace")
            return resp.status, resp_body
    except HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace") if e.fp else ""
        return e.code, err_body
    except URLError as e:
        return 0, str(e)


# -----------------------------------------------------------------------------
# Collectors (stubs)
# -----------------------------------------------------------------------------

def collect_heartbeat(source: str, tenant_id: str) -> TelemetryEvent:
    ts = _utc_now_iso()
    payload = {
        "event_type": "heartbeat",
        "status": "ok",
        "uptime_s": int(time.time()),
    }
    eid = deterministic_event_id(source, tenant_id, ts, payload)
    return TelemetryEvent(source=source, tenant_id=tenant_id, timestamp=ts, payload=payload, event_id=eid)


# -----------------------------------------------------------------------------
# Agent loops
# -----------------------------------------------------------------------------

def producer_loop(q: DiskQueue, source: str, tenant_id: str, interval_ms: int = 2000) -> None:
    """
    MVP producer: heartbeat every 2s.
    Later: plug in real collectors (process list, auth logs, netflow, etc).
    """
    while True:
        ev = collect_heartbeat(source, tenant_id)
        q.enqueue(ev)
        time.sleep(interval_ms / 1000.0)


def sender_loop(q: DiskQueue, core_url: str, api_key: str) -> None:
    """
    Sender: pulls from pending, posts to /ingest, marks sent.
    Retries on transient failures with capped exponential backoff.
    """
    if not api_key:
        raise RuntimeError("FG_AGENT_KEY is missing. Export it before running the agent.")

    ingest_url = f"{core_url}/ingest"

    backoff_ms = SEND_INTERVAL_MS
    while True:
        sent_any = False

        for path in q.iter_pending(MAX_BATCH):
            raw = path.read_text(encoding="utf-8")
            doc = json.loads(raw)

            wire = {
                "source": doc["source"],
                "tenant_id": doc["tenant_id"],
                "timestamp": doc["timestamp"],
                "payload": doc.get("payload") or {},
            }

            status, body = post_json(ingest_url, api_key=api_key, payload=wire)

            # Success: 200 or 202 accepted
            if status in (200, 201, 202):
                q.mark_sent(path)
                sent_any = True
                continue

            # Unauthorized/forbidden: stop burning cycles, move to dead
            if status in (401, 403):
                q.mark_dead(path)
                continue

            # Otherwise transient: keep it in pending, backoff
            # status=0 means connection/URL error
            # You can optionally log `body` somewhere real.
            break

        if sent_any:
            backoff_ms = SEND_INTERVAL_MS
        else:
            backoff_ms = min(int(backoff_ms * 1.5), MAX_BACKOFF_MS)

        time.sleep(backoff_ms / 1000.0)


def main() -> None:
    q = DiskQueue(DEFAULT_QUEUE_DIR)

    t_prod = threading.Thread(
        target=producer_loop,
        args=(q, DEFAULT_SOURCE, DEFAULT_TENANT),
        daemon=True,
        name="producer",
    )
    t_send = threading.Thread(
        target=sender_loop,
        args=(q, DEFAULT_CORE_URL, AGENT_KEY),
        daemon=True,
        name="sender",
    )

    t_prod.start()
    t_send.start()

    # keep main alive
    while True:
        time.sleep(5)


if __name__ == "__main__":
    main()
