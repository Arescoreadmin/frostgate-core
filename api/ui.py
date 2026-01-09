from __future__ import annotations

import os
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse

from api.ratelimit import rate_limit_guard

router = APIRouter(
    prefix="/ui",
    tags=["ui"],
    # IMPORTANT: do NOT put rate_limit_guard here or it will block /ui/token GET
)

UI_COOKIE_NAME = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")
ERR_INVALID = "Invalid or missing API key"


def _env_bool(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "y", "on"}


def _auth_enabled() -> bool:
    # If explicitly configured, honor it.
    if os.getenv("FG_AUTH_ENABLED") is not None:
        return _env_bool("FG_AUTH_ENABLED", default=False)
    # Otherwise: auth enabled if an API key exists.
    return bool(os.getenv("FG_API_KEY"))


def _is_prod() -> bool:
    return os.getenv("FG_ENV", "dev").strip().lower() in {"prod", "production"}


def _html_headers() -> dict[str, str]:
    # Minimal-but-real security headers for embedded UI.
    # CSP allows inline script because the UI is an inline HTML blob.
    # If you want to harden further, move JS into a static file and drop 'unsafe-inline'.
    return {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": (
            "default-src 'self'; "
            "img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline'; "
            "script-src 'self' 'unsafe-inline'; "
            "connect-src 'self'; "
            "base-uri 'none'; "
            "form-action 'none'"
        ),
    }


def _get_cookie_key(req: Request) -> Optional[str]:
    v = req.cookies.get(UI_COOKIE_NAME)
    if v and str(v).strip():
        return str(v).strip()
    return None


def _get_header_key(req: Request) -> Optional[str]:
    v = req.headers.get("x-api-key")
    if v and str(v).strip():
        return str(v).strip()
    return None


def _get_query_key(req: Request) -> Optional[str]:
    v = req.query_params.get("api_key") or req.query_params.get("key")
    if v and str(v).strip():
        return str(v).strip()
    return None


def _require_ui_key(req: Request) -> None:
    """
    UI auth gate:
    - If auth disabled, no-op.
    - If enabled, accept API key from cookie OR x-api-key OR query param.
    NOTE: This only checks "present". Actual validation happens on API endpoints
    via require_api_key_always/require_scopes. UI is just a UX convenience gate.
    """
    if not _auth_enabled():
        return
    if _get_cookie_key(req) or _get_header_key(req) or _get_query_key(req):
        return
    raise HTTPException(status_code=401, detail=ERR_INVALID)


# =============================================================================
# === FG:UI_FEED:BEGIN ===
# =============================================================================


@router.get(
    "/feed",
    response_class=HTMLResponse,
    operation_id="ui_feed_page",
    dependencies=[Depends(rate_limit_guard)],
)
def ui_feed(request: Request) -> HTMLResponse:
    _require_ui_key(request)
    # Canonical UI feed: SSE + polling fallback + watchdog. Single source of truth.
    return HTMLResponse(r"""\
<!doctype html>
<html>
<head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1"/>
  <title>FrostGate Live Feed</title>
  <style>
    :root { color-scheme: dark; }
    body { font-family: ui-sans-serif, system-ui, -apple-system, Segoe UI, Roboto, Arial; margin:0; background:#0b0b0b; color:#ddd; }
    .wrap { padding: 14px 16px; }
    .bar {
      position: sticky; top: 0; z-index: 999;
      background: rgba(11,11,11,0.92);
      backdrop-filter: blur(6px);
      border-bottom: 1px solid #222;
    }
    .row { display:flex; gap:12px; align-items:center; flex-wrap:wrap; }
    .pill { padding:2px 8px; border-radius:999px; border:1px solid #333; font-size:12px; }
    .ok { background:#0b2; color:#000; border-color:#0b2; }
    .bad { background:#f33; color:#000; border-color:#f33; }
    .warn { background:#fc3; color:#000; border-color:#fc3; }
    .muted { opacity:.75; }
    .btn { cursor:pointer; padding:6px 10px; border-radius:10px; border:1px solid #333; background:#111; color:#ddd; }
    .btn:hover { border-color:#666; }
    input, select { padding:6px 8px; border-radius:10px; border:1px solid #333; background:#111; color:#ddd; }
    code { background:#111; border:1px solid #222; padding: 1px 6px; border-radius: 8px; }
    table { width:100%; border-collapse:collapse; margin-top:10px; }
    th, td { border-bottom:1px solid #222; padding:8px; vertical-align:top; font-size:13px; }
    th { text-align:left; position: sticky; top: 64px; background:#0f0f0f; z-index:50; }
    pre { white-space:pre-wrap; word-break:break-word; margin:0; }
    details pre { padding:8px; border:1px solid #222; border-radius:8px; margin-top:8px; background:#0d0d0d; }
    .chips { display:flex; gap:6px; flex-wrap:wrap; }
    .chip { font-size:11px; padding:2px 6px; border-radius:999px; border:1px solid #333; }
    .statgrid { display:flex; gap:10px; flex-wrap:wrap; }
    .stat { border:1px solid #222; border-radius:12px; padding:6px 10px; background:#0e0e0e; font-size:12px; }
    .right { margin-left:auto; }
  </style>
</head>

<body>
  <div class="bar">
    <div class="wrap">
      <div class="row">
        <div><strong>Live Feed</strong> <span class="muted">/ui/feed</span></div>
        <span id="conn" class="pill warn">connecting</span>

        <button id="pauseBtn" class="btn">Pause</button>
        <button id="clearBtn" class="btn">Clear</button>
        <label class="muted"><input id="autoScroll" type="checkbox" checked/> autoscroll</label>

        <div class="right row">
          <label class="muted">only_changed <input id="onlyChanged" type="checkbox"/></label>
          <label class="muted">only_actionable <input id="onlyActionable" type="checkbox"/></label>
          <label class="muted">level
            <select id="level">
              <option value="">all</option>
              <option value="none">none</option>
              <option value="low">low</option>
              <option value="medium">medium</option>
              <option value="high">high</option>
              <option value="critical">critical</option>
            </select>
          </label>
          <label class="muted">q <input id="q" type="text" placeholder="search…" style="width:160px"/></label>
          <label class="muted">limit <input id="limit" type="number" min="10" max="500" value="120" style="width:84px"/></label>
        </div>
      </div>

      <div class="row muted" style="margin-top:10px;">
        <div>Last event id: <span id="lastId">-</span></div>
        <div>Last update: <span id="lastTs">-</span></div>
        <div>Age: <span id="age">-</span></div>
        <div class="muted">Dev: hit <code>/ui/token?api_key=...</code> once to mint cookie.</div>
      </div>

      <div class="row" style="margin-top:10px;">
        <div class="statgrid" id="stats"></div>
      </div>
    </div>
  </div>

  <div class="wrap">
    <table>
      <thead>
        <tr>
          <th style="width:160px;">time</th>
          <th style="width:150px;">type/source</th>
          <th style="width:120px;">level/score</th>
          <th style="width:140px;">decision</th>
          <th>summary</th>
          <th style="width:240px;">changes</th>
        </tr>
      </thead>
      <tbody id="tbody"></tbody>
    </table>
  </div>

<script>
(() => {
  const tbody = document.getElementById("tbody");
  const conn = document.getElementById("conn");
  const lastIdEl = document.getElementById("lastId");
  const lastTsEl = document.getElementById("lastTs");
  const ageEl = document.getElementById("age");
  const statsEl = document.getElementById("stats");

  const pauseBtn = document.getElementById("pauseBtn");
  const clearBtn = document.getElementById("clearBtn");
  const autoScrollEl = document.getElementById("autoScroll");

  const onlyChangedEl = document.getElementById("onlyChanged");
  const onlyActionableEl = document.getElementById("onlyActionable");
  const levelEl = document.getElementById("level");
  const limitEl = document.getElementById("limit");
  const qEl = document.getElementById("q");

  let paused = false;
  let es = null;
  let sinceId = null;
  let rows = [];
  let lastMsgAt = 0;
  let lastEventTs = null;
  let reconnectBackoff = 800;

  function setConn(state, kind) {
    conn.textContent = state;
    conn.className = "pill " + (kind || "warn");
  }
  function nowIso() { return new Date().toISOString(); }
  function clip() {
    const max = parseInt(limitEl.value || "120", 10);
    if (rows.length > max) rows = rows.slice(0, max);
  }
  function esc(s) {
    return (s ?? "").toString().replace(/[&<>"']/g, m => ({
      "&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"
    }[m]));
  }
  function badgeFor(level) {
    if (level === "critical" || level === "high") return "bad";
    if (level === "medium") return "warn";
    return "ok";
  }
  function fmtChanges(item) {
    const arr = item.changed_fields || [];
    if (!arr.length) return "<span class='muted'>—</span>";
    return "<div class='chips'>" + arr.map(x => `<span class="chip">${esc(x)}</span>`).join("") + "</div>";
  }
  function diffSummary(item) {
    const d = item.decision_diff;
    if (!d) return "";
    if (typeof d === "string") return d;
    if (d.summary) return d.summary;
    if (Array.isArray(d.changes) && d.changes.length) return "changes: " + d.changes.join(", ");
    return "";
  }
  function computeStats() {
    const counts = { total: rows.length, critical:0, high:0, medium:0, low:0, none:0 };
    let actionable = 0, changed = 0, maxScore = null;
    for (const it of rows) {
      const lvl = (it.threat_level || "none").toLowerCase();
      if (counts[lvl] != null) counts[lvl]++; else counts.none++;
      if ((it.changed_fields || []).length) changed++;
      if (it.action_taken && it.action_taken !== "allow") actionable++;
      if (typeof it.score === "number") maxScore = (maxScore == null ? it.score : Math.max(maxScore, it.score));
    }
    const parts = [
      ["total", counts.total],
      ["critical", counts.critical],
      ["high", counts.high],
      ["medium", counts.medium],
      ["low", counts.low],
      ["changed", changed],
      ["actionable", actionable],
      ["max score", (maxScore == null ? "—" : maxScore.toFixed(1))],
    ];
    statsEl.innerHTML = parts.map(([k,v]) => `<div class="stat"><span class="muted">${esc(k)}</span> <b>${esc(v)}</b></div>`).join("");
  }
  function render() {
    computeStats();
    tbody.innerHTML = rows.map(item => {
      const t = esc(item.timestamp || "");
      const type = esc(item.event_type || "");
      const src = esc(item.source || "");
      const lvl = esc(item.threat_level || "");
      const score = (item.score ?? "");
      const decision = esc(item.action_taken || item.decision || "");
      const reason = esc(item.action_reason || "");
      const sum = esc(item.summary || item.title || diffSummary(item) || reason || "");
      const changes = fmtChanges(item);
      const full = esc(JSON.stringify(item, null, 2));
      const copyPayload = JSON.stringify(JSON.stringify(item, null, 2));
      return `
        <tr>
          <td>${t}</td>
          <td><div><strong>${type}</strong></div><div class="muted">${src}</div></td>
          <td>
            <span class="pill ${badgeFor((item.threat_level||"").toLowerCase())}">${lvl || "?"}</span>
            <div class="muted">score ${esc(score)}</div>
          </td>
          <td>${decision || "<span class='muted'>—</span>"}</td>
          <td>
            <details>
              <summary>${sum || "<span class='muted'>—</span>"}</summary>
              <div class="row" style="margin-top:8px;">
                <button class="btn" onclick='navigator.clipboard.writeText(${copyPayload})'>Copy JSON</button>
                <span class="muted">id ${esc(item.id ?? "")}</span>
                <span class="muted">${esc(reason)}</span>
              </div>
              <pre>${full}</pre>
            </details>
          </td>
          <td>${changes}</td>
        </tr>
      `;
    }).join("");
    if (autoScrollEl.checked && !paused) {
      window.scrollTo({ top: 0, behavior: "smooth" });
    }
  }
  function addItems(items) {
    if (!items || !items.length) return;
    for (const it of items) {
      rows.unshift(it);
      if (typeof it.id === "number") sinceId = Math.max(sinceId || 0, it.id);
      if (it.timestamp) { try { lastEventTs = new Date(it.timestamp); } catch {} }
    }
    clip();
    render();
    lastIdEl.textContent = sinceId ?? "-";
    lastTsEl.textContent = nowIso();
    lastMsgAt = Date.now();
    reconnectBackoff = 800;
  }
  function buildQuery() {
    const q = new URLSearchParams();
    q.set("limit", "50");
    q.set("interval", "1.0");
    if (sinceId != null) q.set("since_id", String(sinceId));
    if (onlyChangedEl.checked) q.set("only_changed", "1");
    if (onlyActionableEl.checked) q.set("only_actionable", "1");
    if (levelEl.value) q.set("threat_level", levelEl.value);
    const qq = (qEl.value || "").trim();
    if (qq) q.set("q", qq);
    return q.toString();
  }
  async function pollOnce() {
    if (paused) return;
    try {
      setConn("polling", "warn");
      const qs = new URLSearchParams();
      qs.set("limit", "50");
      if (sinceId != null) qs.set("since_id", String(sinceId));
      if (onlyChangedEl.checked) qs.set("only_changed", "1");
      if (onlyActionableEl.checked) qs.set("only_actionable", "1");
      if (levelEl.value) qs.set("threat_level", levelEl.value);
      const qq = (qEl.value || "").trim();
      if (qq) qs.set("q", qq);
      const r = await fetch("/feed/live?" + qs.toString(), { credentials: "same-origin" });
      if (!r.ok) throw new Error("poll failed " + r.status);
      const data = await r.json();
      addItems(data.items || []);
      setConn("poll ok", "ok");
    } catch (e) {
      console.error(e);
      setConn("poll error", "bad");
    }
  }
  function connectSSE() {
    if (es) { try { es.close(); } catch {} es = null; }
    const url = "/feed/stream?" + buildQuery();
    setConn("connecting", "warn");
    try {
      es = new EventSource(url, { withCredentials: true });
      es.onopen = () => { setConn("sse connected", "ok"); lastMsgAt = Date.now(); };
      es.onmessage = (ev) => {
        if (paused) return;
        try {
          const payload = JSON.parse(ev.data);
          if (Array.isArray(payload)) addItems(payload);
          else if (payload && payload.items) addItems(payload.items);
        } catch (e) {
          console.error("bad SSE payload", e, ev.data);
        }
      };
      es.onerror = () => {
        setConn("sse error (reconnect)", "bad");
        try { es.close(); } catch {}
        es = null;
        const wait = Math.min(8000, reconnectBackoff);
        reconnectBackoff = Math.min(8000, reconnectBackoff * 1.6);
        setTimeout(() => connectSSE(), wait);
      };
    } catch (e) {
      console.error(e);
      setConn("sse init failed (poll)", "bad");
    }
  }
  function resetAndReconnect() {
    sinceId = null;
    rows = [];
    render();
    connectSSE();
    pollOnce();
  }

  pauseBtn.onclick = () => {
    paused = !paused;
    pauseBtn.textContent = paused ? "Resume" : "Pause";
    if (!paused) lastMsgAt = Date.now();
  };
  clearBtn.onclick = () => { rows = []; render(); };

  [onlyChangedEl, onlyActionableEl, levelEl].forEach(el => el.addEventListener("change", resetAndReconnect));
  qEl.addEventListener("keydown", (e) => { if (e.key === "Enter") resetAndReconnect(); });
  limitEl.addEventListener("change", () => { clip(); render(); });

  setInterval(() => {
    if (lastEventTs) {
      const sec = Math.max(0, Math.floor((Date.now() - lastEventTs.getTime()) / 1000));
      ageEl.textContent = sec + "s";
    } else {
      ageEl.textContent = "-";
    }
    if (paused) return;
    if (!es) return;
    const silence = Date.now() - (lastMsgAt || 0);
    if (silence > 15000) {
      setConn("stalled (reconnect)", "warn");
      try { es.close(); } catch {}
      es = null;
      connectSSE();
      pollOnce();
    }
  }, 1000);

  connectSSE();
  pollOnce();
})();
</script>
</body>
</html>

""")


def ui_token_get(
    request: Request,
    api_key: str | None = Query(default=None, alias="api_key"),
    key: str | None = Query(default=None),
):
    # Dev-only convenience. Keep it OFF in prod.
    if os.getenv("FG_UI_TOKEN_GET_ENABLED", "1") != "1":
        raise HTTPException(status_code=404, detail="Not Found")

    raw = (api_key or key or "").strip()
    if not raw:
        raise HTTPException(status_code=400, detail="missing api_key")

    api_key_val = raw

    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FrostGate UI Token</title>
</head>
<body>
<script>
  try {{
    localStorage.setItem("FG_API_KEY", {api_key_val!r});
  }} catch (e) {{
    console.warn("localStorage blocked:", e);
  }}
  window.location.replace("/ui/feed");
</script>
<p>Setting token…</p>
</body>
</html>
"""

    resp = HTMLResponse(content=html, headers=_html_headers())
    resp.set_cookie(
        key=UI_COOKIE_NAME,
        value=api_key_val,
        httponly=True,
        samesite="lax",
        secure=_is_prod(),
        path="/",
        max_age=60 * 60 * 8,
    )
    return resp


@router.get("/token")
def ui_token(api_key: str, response: Response):
    response.set_cookie(
        "fg_api_key",
        api_key,
        httponly=True,
        samesite="lax",
    )
    return {"ok": True}
