from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import random
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, Optional

import httpx

from agent.app.agent_main import run

if __name__ == "__main__":
    run()


@dataclass(frozen=True)
class AgentConfig:
    core_base_url: str
    agent_key: str
    tenant_id: str
    source: str

    queue_path: str
    flush_interval_s: float
    batch_size: int
    max_queue: int

    connect_timeout_s: float
    read_timeout_s: float

    heartbeat_enabled: bool


def load_config() -> AgentConfig:
    core_base_url = (os.getenv("FG_CORE_BASE_URL") or "http://localhost:18080").rstrip("/")
    agent_key = (os.getenv("FG_AGENT_KEY") or os.getenv("FG_AGENT_API_KEY") or "").strip()
    tenant_id = (os.getenv("FG_TENANT_ID") or "t1").strip()
    source = (os.getenv("FG_SOURCE") or os.getenv("HOSTNAME") or "agent1").strip()

    queue_path = os.getenv("FG_AGENT_QUEUE_PATH") or str(Path(os.getenv("FG_AGENT_QUEUE_DIR", "/var/lib/frostgate/agent_queue")) / "agent_queue.db")
    flush_interval_s = float(os.getenv("FG_AGENT_FLUSH_INTERVAL_SECONDS", "2"))
    batch_size = int(os.getenv("FG_AGENT_BATCH_SIZE", "50"))
    max_queue = int(os.getenv("FG_AGENT_MAX_QUEUE", "50000"))

    connect_timeout_s = float(os.getenv("FG_AGENT_CONNECT_TIMEOUT_SECONDS", "2"))
    read_timeout_s = float(os.getenv("FG_AGENT_TIMEOUT_SECONDS", "4"))

    heartbeat_enabled = (os.getenv("FG_AGENT_HEARTBEAT", "true").lower() == "true")

    if not agent_key:
        raise RuntimeError("FG_AGENT_KEY (or FG_AGENT_API_KEY) is required")

    return AgentConfig(
        core_base_url=core_base_url,
        agent_key=agent_key,
        tenant_id=tenant_id,
        source=source,
        queue_path=queue_path,
        flush_interval_s=flush_interval_s,
        batch_size=batch_size,
        max_queue=max_queue,
        connect_timeout_s=connect_timeout_s,
        read_timeout_s=read_timeout_s,
        heartbeat_enabled=heartbeat_enabled,
    )


def ensure_queue(db_path: str) -> None:
    os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS queue (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              event_id TEXT NOT NULL UNIQUE,
              created_at TEXT NOT NULL,
              payload TEXT NOT NULL,
              attempts INTEGER NOT NULL DEFAULT 0,
              next_attempt_at REAL NOT NULL DEFAULT 0
            );
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_queue_next ON queue(next_attempt_at);")
        conn.commit()


def deterministic_event_id(tenant_id: str, source: str, event_type: str, subject: str, ts: datetime, features: Dict[str, Any]) -> str:
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    ts = ts.astimezone(timezone.utc)

    bucket = int(ts.timestamp() // 5) * 5
    stable = {
        "tenant_id": tenant_id,
        "source": source,
        "event_type": event_type,
        "subject": subject or "",
        "bucket": bucket,
        "features": features or {},
    }
    blob = json.dumps(stable, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def queue_put(db_path: str, payload: Dict[str, Any]) -> bool:
    event_id = payload.get("event_id")
    if not event_id:
        return False
    with sqlite3.connect(db_path) as conn:
        try:
            conn.execute(
                "INSERT INTO queue(event_id, created_at, payload, attempts, next_attempt_at) VALUES(?,?,?,?,?)",
                (event_id, datetime.now(timezone.utc).isoformat(), json.dumps(payload), 0, 0.0),
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            # already queued (dedupe)
            return False


def queue_size(db_path: str) -> int:
    with sqlite3.connect(db_path) as conn:
        row = conn.execute("SELECT COUNT(*) FROM queue").fetchone()
        return int(row[0]) if row else 0


def queue_pop_batch(db_path: str, limit: int) -> list[dict]:
    now = time.time()
    with sqlite3.connect(db_path) as conn:
        rows = conn.execute(
            "SELECT id, event_id, payload, attempts FROM queue WHERE next_attempt_at <= ? ORDER BY id ASC LIMIT ?",
            (now, limit),
        ).fetchall()
        return [{"id": r[0], "event_id": r[1], "payload": json.loads(r[2]), "attempts": int(r[3])} for r in rows]


def queue_delete(db_path: str, ids: Iterable[int]) -> None:
    ids = list(ids)
    if not ids:
        return
    with sqlite3.connect(db_path) as conn:
        conn.executemany("DELETE FROM queue WHERE id = ?", [(i,) for i in ids])
        conn.commit()


def queue_backoff(db_path: str, ids: Iterable[int], attempts: int) -> None:
    ids = list(ids)
    if not ids:
        return
    # exponential backoff with jitter, capped
    base = min(60.0, (2 ** min(attempts, 6)))  # caps at 64s-ish
    delay = base + random.random() * 0.5 * base
    next_at = time.time() + delay
    with sqlite3.connect(db_path) as conn:
        conn.executemany(
            "UPDATE queue SET attempts = attempts + 1, next_attempt_at = ? WHERE id = ?",
            [(next_at, i) for i in ids],
        )
        conn.commit()


def build_event(cfg: AgentConfig, event_type: str, subject: str, features: Dict[str, Any], raw: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    ts = datetime.now(timezone.utc)
    eid = deterministic_event_id(cfg.tenant_id, cfg.source, event_type, subject, ts, features)
    return {
        "event_id": eid,
        "tenant_id": cfg.tenant_id,
        "source": cfg.source,
        "timestamp": ts.isoformat(),
        "event_type": event_type,
        "subject": subject,
        "features": features,
        "raw": raw,
    }


def collect_stub(cfg: AgentConfig) -> Iterable[Dict[str, Any]]:
    # Collector stubs: predictable, low-noise.
    if cfg.heartbeat_enabled:
        yield build_event(cfg, "heartbeat", subject=cfg.source, features={"alive": True, "v": 1})
    # Example auth signal stub (disabled by default, enable later via real collectors)
    # yield build_event(cfg, "auth", subject="1.2.3.4", features={"failed_auths": 7, "src_ip": "1.2.3.4"})


def send_batch(cfg: AgentConfig, events: list[dict]) -> bool:
    url = f"{cfg.core_base_url}/ingest"
    headers = {"x-api-key": cfg.agent_key, "content-type": "application/json"}

    timeout = httpx.Timeout(cfg.read_timeout_s, connect=cfg.connect_timeout_s)
    with httpx.Client(timeout=timeout) as client:
        r = client.post(url, headers=headers, json={"events": events})
        if r.status_code == 200:
            return True
        # 4xx means config/auth bug, do NOT hammer. Backoff still applies via caller.
        return False


def run() -> None:
    cfg = load_config()
    ensure_queue(cfg.queue_path)

    while True:
        # Backpressure: if queue is huge, only collect heartbeat (or nothing)
        qn = queue_size(cfg.queue_path)
        if qn < cfg.max_queue:
            for ev in collect_stub(cfg):
                queue_put(cfg.queue_path, ev)

        batch = queue_pop_batch(cfg.queue_path, cfg.batch_size)
        if batch:
            payloads = [b["payload"] for b in batch]
            ok = send_batch(cfg, payloads)
            if ok:
                queue_delete(cfg.queue_path, [b["id"] for b in batch])
            else:
                # Backoff based on max attempts in this batch
                max_attempts = max(b["attempts"] for b in batch)
                queue_backoff(cfg.queue_path, [b["id"] for b in batch], max_attempts + 1)

        time.sleep(cfg.flush_interval_s)
