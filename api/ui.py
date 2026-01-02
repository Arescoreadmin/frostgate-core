from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from api.auth_scopes import require_api_key_always, require_scopes
from api.ratelimit import rate_limit_guard

router = APIRouter(
    prefix="/ui",
    tags=["ui"],
    dependencies=[Depends(rate_limit_guard)],
)

UI_COOKIE_NAME = os.getenv("FG_UI_COOKIE_NAME", "fg_api_key")


def _is_prod() -> bool:
    return os.getenv("FG_ENV", "dev").strip().lower() in {"prod", "production"}


def _html_headers() -> dict[str, str]:
    # Keep it simple: no caching, reduce clickjacking risk.
    # CSP is intentionally minimal because UI is inline JS.
    return {
        "Cache-Control": "no-store, max-age=0",
        "Pragma": "no-cache",
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


@router.get("/feed", response_class=HTMLResponse)
def ui_feed() -> HTMLResponse:
    html = r"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>FrostGate Live Feed</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; background: #0b0f14; color: #e6edf3; }
    .top { display:flex; gap:12px; align-items:center; flex-wrap:wrap; margin-bottom: 14px; }
    button, input, select { padding: 8px 10px; border-radius: 10px; border: 1px solid #223; background:#101826; color:#e6edf3; }
    button { cursor: pointer; }
    button:disabled { opacity: .6; cursor: not-allowed; }
    label { display:flex; gap:8px; align-items:center; }
    .grid { display:grid; grid-template-columns: repeat(auto-fill,minmax(420px,1fr)); gap: 12px; }
    .card { border: 1px solid #223; border-radius: 14px; padding: 12px; background:#0f1623; }
    .sev { font-weight: 800; letter-spacing: 0.08em; text-transform: uppercase; font-size: 12px; opacity: .9; display:flex; gap:8px; flex-wrap:wrap; align-items:center; }
    .badge { border:1px solid #223; padding:2px 8px; border-radius:999px; font-size:12px; opacity:.9; }
    .title { font-weight: 750; margin-top: 6px; }
    .meta { opacity: .78; font-size: 12px; margin-top: 8px; line-height: 1.4; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0b0f14; border: 1px solid #223; padding: 10px; border-radius: 12px; margin-top: 10px; }
    details > summary { cursor:pointer; opacity:.9; margin-top:10px; }
    .row { display:flex; gap:10px; flex-wrap:wrap; align-items:center; }
    .muted { opacity:.75; }
    .spacer { flex: 1; }
    .right { margin-left:auto; display:flex; gap:10px; align-items:center; flex-wrap:wrap; }
    .mono { font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace; }
  </style>
</head>
<body>
  <h2>FrostGate Live Feed</h2>

  <div class="top">
    <button onclick="refreshFull()">Refresh</button>
    <button id="pauseBtn" onclick="togglePause()">Pause</button>
    <button onclick="applyDevPreset()">DEV: show everything</button>

    <label>Limit <input id="limit" type="number" min="1" max="200" value="50"></label>
    <label>Poll ms <input id="poll" type="number" min="250" max="20000" value="2500"></label>

    <!-- IMPORTANT: your DB does NOT store 'severity'. This dropdown is a THREAT LEVEL shortcut. -->
    <label>Threat
      <select id="severity">
        <option value="" selected>any</option>
        <option value="critical">critical</option>
        <option value="high">high</option>
        <option value="medium">medium</option>
        <option value="low">low</option>
        <option value="none">none</option>
      </select>
    </label>

    <label>Action <input id="action_taken" placeholder="e.g. blocked / log_only" size="14"></label>
    <label>Source <input id="source" placeholder="e.g. waf / edge-gw" size="12"></label>
    <label>Tenant <input id="tenant_id" placeholder="tenant id" size="12"></label>
    <label>Search <input id="q" placeholder="title/event/id..." size="16"></label>

    <label><input id="only_changed" type="checkbox"> only changed</label>
    <label><input id="only_actionable" type="checkbox"> only actionable</label>
    <label><input id="group_repeats" type="checkbox" checked> group repeats</label>
    <label><input id="auto_pause_details" type="checkbox" checked> auto-pause on open</label>

    <span class="right">
      <span id="status" class="meta"></span>
    </span>
  </div>

  <div id="grid" class="grid"></div>

<script>
let timer = null;
let paused = false;
let lastId = 0;
let groups = new Map();
let inflight = null; // AbortController

const STORAGE_KEY = "fg_live_feed_state_v1";

function setStatus(msg){
  const status = document.getElementById("status");
  if (status) status.textContent = msg || "";
}

function stopPoll() { if (timer) clearInterval(timer); timer = null; }
function startPoll() {
  stopPoll();
  const pollMs = parseInt(document.getElementById("poll").value || "2500", 10);
  if (!pollMs || pollMs < 250) return;
  timer = setInterval(() => loadIncremental(false), pollMs);
}

function togglePause(force){
  if (typeof force === "boolean") paused = force;
  else paused = !paused;

  const btn = document.getElementById("pauseBtn");
  if (btn) btn.textContent = paused ? "Resume" : "Pause";

  if (paused) {
    stopPoll();
    setStatus("Paused (polling stopped).");
  } else {
    startPoll();
    loadIncremental(true);
  }
}

function escapeHtml(s){
  const str = String(s ?? "");
  return str.replace(/[&<>"']/g, (c) => ({
    "&":"&amp;",
    "<":"&lt;",
    ">":"&gt;",
    '"':"&quot;",
    "'":"&#39;"
  }[c]));
}

function openFrozen(title, obj){
  const w = window.open("", "_blank", "noopener,noreferrer");
  if (!w) return;

  const safeTitle = escapeHtml(title || "FrostGate Event");
  const payload = escapeHtml(JSON.stringify(obj || {}, null, 2));

  w.document.open();
  w.document.write(`<!doctype html>
<html><head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>${safeTitle}</title>
<style>
body{font-family:system-ui,sans-serif;margin:24px;background:#0b0f14;color:#e6edf3}
h2{margin:0 0 12px}
pre{white-space:pre-wrap;word-break:break-word;background:#0f1623;border:1px solid #223;padding:12px;border-radius:12px}
.meta{opacity:.8;margin-bottom:12px}
</style>
</head>
<body>
<h2>${safeTitle}</h2>
<div class="meta">Frozen snapshot. No polling. No refresh spam.</div>
<pre>${payload}</pre>
</body></html>`);
  w.document.close();
}

function applyDevPreset() {
  document.getElementById("severity").value = "";
  document.getElementById("action_taken").value = "";
  document.getElementById("source").value = "";
  document.getElementById("tenant_id").value = "";
  document.getElementById("q").value = "";
  document.getElementById("only_changed").checked = false;
  document.getElementById("only_actionable").checked = false;
  document.getElementById("group_repeats").checked = false;
  refreshFull();
}

function getParams() {
  const p = new URLSearchParams();
  p.set("limit", document.getElementById("limit").value || "50");

  // IMPORTANT: do NOT send `severity=` to backend.
  // The DB model doesn't store severity, your backend may 500 or always return empty.
  // We use this dropdown as a quick filter for threat_level.
  const threatFromDropdown = document.getElementById("severity").value.trim();

  const action = document.getElementById("action_taken").value.trim();
  const source = document.getElementById("source").value.trim();
  const tenant = document.getElementById("tenant_id").value.trim();
  const q = document.getElementById("q").value.trim();

  if (threatFromDropdown) p.set("threat_level", threatFromDropdown);
  if (action) p.set("action_taken", action);
  if (source) p.set("source", source);
  if (tenant) p.set("tenant_id", tenant);
  if (q) p.set("q", q);

  if (document.getElementById("only_changed").checked) p.set("only_changed", "true");
  if (document.getElementById("only_actionable").checked) p.set("only_actionable", "true");
  return p;
}

function summarizeAction(i){
  // Your current payloads often have null action_taken.
  // Keep UI readable anyway.
  const tl = i.threat_level || "";
  const at = i.action_taken || "";
  return `${tl}${at ? " · " + at : ""}`;
}

function renderItem(i) {
  const rules = (i.rules_triggered || []).slice(0, 4).map(r => `<span class="badge">${escapeHtml(r)}</span>`).join("");
  const changed = (i.changed_fields || []).slice(0, 6).map(f => `<span class="badge">${escapeHtml(f)}</span>`).join("");

  const conf = Number(i.confidence ?? 0);
  const score = (i.score != null) ? Number(i.score) : null;

  return `
    <div class="card">
      <div class="sev">
        <span>${escapeHtml(summarizeAction(i) || "none")}</span>
        <span class="badge">conf ${conf.toFixed(2)}</span>
        ${score != null && !Number.isNaN(score) ? `<span class="badge">score ${score.toFixed(2)}</span>` : ""}
        <span class="spacer"></span>
        <button onclick='openFrozen(i.title || "Event", i)'>Open frozen</button>
        <button onclick="togglePause(true)">Pause</button>
      </div>

      <div class="title">${escapeHtml(i.title || i.event_type || "event")}</div>
      <div class="muted">${escapeHtml(i.summary || "")}</div>

      <div class="row" style="margin-top:8px;">
        ${rules}
        ${changed ? `<span class="badge">changed:</span>${changed}` : ""}
      </div>

      <div class="meta">
        <span class="mono">id:</span> ${escapeHtml(i.id || "")} ·
        <span class="mono">event_id:</span> ${escapeHtml(i.event_id || i.decision_id || "")}<br/>
        <span class="mono">source:</span> ${escapeHtml(i.source || "")} ·
        <span class="mono">tenant:</span> ${escapeHtml(i.tenant_id || "")}<br/>
        <span class="mono">type:</span> ${escapeHtml(i.event_type || "")} ·
        <span class="mono">time:</span> ${escapeHtml(i.timestamp || "")}
        ${i.action_reason ? `<br/><span class="mono">reason:</span> ${escapeHtml(i.action_reason)}` : ""}
      </div>

      ${i.decision_diff ? `
        <details>
          <summary>Diff</summary>
          <pre>${escapeHtml(JSON.stringify(i.decision_diff, null, 2))}</pre>
        </details>
      ` : ""}

      ${i.metadata ? `
        <details>
          <summary>Raw metadata</summary>
          <pre>${escapeHtml(JSON.stringify(i.metadata, null, 2))}</pre>
        </details>
      ` : ""}
    </div>
  `;
}

function renderGroups() {
  const grid = document.getElementById("grid");
  const arr = Array.from(groups.values()).sort((a,b) => (b.last.id - a.last.id));

  grid.innerHTML = arr.map(g => {
    const i = g.last;
    return `
      <div class="card">
        <div class="sev">
          <span>${escapeHtml(summarizeAction(i) || "none")}</span>
          <span class="badge">x${g.count}</span>
          <span class="badge">last id ${escapeHtml(i.id)}</span>
          <span class="spacer"></span>
          <button onclick='openFrozen(i.title || "Event", i)'>Open frozen</button>
          <button onclick="togglePause(true)">Pause</button>
        </div>
        <div class="title">${escapeHtml(i.title || i.event_type || "event")}</div>
        <div class="muted">${escapeHtml(i.summary || "")}</div>
        <div class="meta">
          last: ${escapeHtml(i.timestamp || "")}<br/>
          source: ${escapeHtml(i.source || "")} · tenant: ${escapeHtml(i.tenant_id || "")}<br/>
          fingerprint: ${escapeHtml(i.fingerprint || "")}
        </div>
        <details>
          <summary>Show latest details</summary>
          ${renderItem(i)}
        </details>
      </div>
    `;
  }).join("");
}

async function requestFeed(since) {
  const params = getParams();
  if (since != null) params.set("since_id", String(since));

  if (inflight) inflight.abort();
  inflight = new AbortController();

  return fetch(`/feed/live?` + params.toString(), {
    credentials: "same-origin",
    signal: inflight.signal,
    headers: { "Accept": "application/json" }
  });
}

async function refreshFull() {
  lastId = 0;
  groups = new Map();
  document.getElementById("grid").innerHTML = "";
  await loadIncremental(true);
}

function persistState(){
  const state = {
    limit: document.getElementById("limit").value,
    poll: document.getElementById("poll").value,
    severity: document.getElementById("severity").value,
    action_taken: document.getElementById("action_taken").value,
    source: document.getElementById("source").value,
    tenant_id: document.getElementById("tenant_id").value,
    q: document.getElementById("q").value,
    only_changed: document.getElementById("only_changed").checked,
    only_actionable: document.getElementById("only_actionable").checked,
    group_repeats: document.getElementById("group_repeats").checked,
    auto_pause_details: document.getElementById("auto_pause_details").checked,
  };
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); } catch {}
}

function restoreState(){
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return;
    const s = JSON.parse(raw);
    if (!s || typeof s !== "object") return;

    if (s.limit) document.getElementById("limit").value = s.limit;
    if (s.poll) document.getElementById("poll").value = s.poll;
    if (typeof s.severity === "string") document.getElementById("severity").value = s.severity;

    if (typeof s.action_taken === "string") document.getElementById("action_taken").value = s.action_taken;
    if (typeof s.source === "string") document.getElementById("source").value = s.source;
    if (typeof s.tenant_id === "string") document.getElementById("tenant_id").value = s.tenant_id;
    if (typeof s.q === "string") document.getElementById("q").value = s.q;

    if (typeof s.only_changed === "boolean") document.getElementById("only_changed").checked = s.only_changed;
    if (typeof s.only_actionable === "boolean") document.getElementById("only_actionable").checked = s.only_actionable;
    if (typeof s.group_repeats === "boolean") document.getElementById("group_repeats").checked = s.group_repeats;
    if (typeof s.auto_pause_details === "boolean") document.getElementById("auto_pause_details").checked = s.auto_pause_details;
  } catch {}
}

let debounceTimer = null;
function debouncedRefresh(){
  persistState();
  if (debounceTimer) clearTimeout(debounceTimer);
  debounceTimer = setTimeout(() => refreshFull(), 250);
}

async function loadIncremental(forceFull=false) {
  if (paused) return;

  try {
    setStatus("Loading…");
    const since = (forceFull ? 0 : lastId);

    const res = await requestFeed(since);
    if (!res.ok) {
      const txt = await res.text();
      setStatus(`Error ${res.status}: ${txt}`);
      return;
    }

    const data = await res.json();
    const items = data.items || [];

    if (items.length === 0) {
      setStatus(`No results (filters too strict?) lastId=${lastId}`);
      return;
    }

    lastId = Math.max(lastId, Number(data.next_since_id || lastId));
    const groupMode = document.getElementById("group_repeats").checked;

    for (const i of items) {
      if (!i || !i.id) continue;

      if (groupMode) {
        const fp = i.fingerprint || "unknown";
        const cur = groups.get(fp);
        if (!cur) groups.set(fp, {count: 1, last: i});
        else {
          cur.count += 1;
          if (i.id >= cur.last.id) cur.last = i;
        }
      } else {
        const grid = document.getElementById("grid");
        const div = document.createElement("div");
        div.innerHTML = renderItem(i);
        grid.prepend(div.firstElementChild);
        while (grid.children.length > 200) grid.removeChild(grid.lastElementChild);
      }
    }

    if (groupMode) renderGroups();
    setStatus(`Updated ${new Date().toLocaleTimeString()} (new ${items.length}) lastId=${lastId}`);
  } catch (e) {
    // Abort is not a UI error, it's expected.
    if (e && e.name === "AbortError") return;
    console.error("UI loadIncremental error:", e);
    setStatus("UI error: " + (e && e.message ? e.message : String(e)));
  }
}

// Wire events (debounced so it’s not spammy or racey)
["limit","poll","severity","action_taken","source","tenant_id"].forEach(id => {
  document.getElementById(id).addEventListener("change", debouncedRefresh);
});
document.getElementById("q").addEventListener("keydown", (e)=>{ if(e.key==="Enter") debouncedRefresh(); });
document.getElementById("only_changed").addEventListener("change", debouncedRefresh);
document.getElementById("only_actionable").addEventListener("change", debouncedRefresh);
document.getElementById("group_repeats").addEventListener("change", debouncedRefresh);
document.getElementById("auto_pause_details").addEventListener("change", persistState);

document.addEventListener("toggle", (e) => {
  const d = e.target;
  if (d && d.tagName === "DETAILS" && d.open) {
    if (document.getElementById("auto_pause_details").checked) togglePause(true);
  }
}, true);

restoreState();
refreshFull();
startPoll();
</script>
</body>
</html>
"""
    return HTMLResponse(content=html, headers=_html_headers())


@router.post(
    "/token",
    dependencies=[
        Depends(require_api_key_always),
        Depends(require_scopes("feed:read")),
    ],
)
def ui_token_post(
    resp: Response,
    x_api_key: str | None = Header(default=None, alias="X-API-Key"),
):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")

    # Production: secure cookie.
    # Dev: allow http://127.0.0.1 usage.
    resp.set_cookie(
        key=UI_COOKIE_NAME,
        value=x_api_key,
        httponly=True,
        samesite="lax",
        secure=_is_prod(),
        path="/",
        max_age=60 * 60 * 8,
    )
    return {"ok": True}


@router.get("/token")
def ui_token_get(
    request: Request,
    key: str | None = Query(default=None),
):
    # Dev-only convenience. DO NOT enable in prod.
    if os.getenv("FG_UI_TOKEN_GET_ENABLED", "0") != "1":
        raise HTTPException(status_code=404, detail="Not Found")
    if not key:
        raise HTTPException(status_code=400, detail="missing key")

    resp = RedirectResponse(url="/ui/feed", status_code=302)
    resp.set_cookie(
        key=UI_COOKIE_NAME,
        value=key,
        httponly=True,
        max_age=60 * 60 * 8,
        samesite="lax",
        secure=_is_prod(),
        path="/",
    )
    return resp
