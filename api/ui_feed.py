from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/ui", tags=["ui"])

@router.get("/feed", response_class=HTMLResponse)
def ui_feed() -> str:
    return """
<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>FrostGate Live Feed</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 16px; }
    .row { border: 1px solid #3333; padding: 10px; border-radius: 10px; margin-bottom: 10px; }
    .top { display:flex; justify-content:space-between; gap:12px; }
    .sev { font-weight: 700; padding: 2px 8px; border-radius: 999px; border:1px solid #3333; }
    .high { background: rgba(255,0,0,0.07); }
    .medium { background: rgba(255,165,0,0.08); }
    .low { background: rgba(0,128,255,0.06); }
    .info { background: rgba(0,0,0,0.03); }
    pre { white-space: pre-wrap; margin: 8px 0 0; }
    .new { outline: 2px solid rgba(0,200,255,0.35); }
    .muted { opacity: 0.75; font-size: 12px; }
    .k { font-weight: 600; opacity: 0.8; }
  </style>
</head>
<body>
  <h2>FrostGate Live Feed</h2>
  <div class="muted">
    Polling <span id="pollMs"></span>ms • showing <span id="limit"></span> items
  </div>
  <div id="root"></div>

<script>
  const API_KEY = localStorage.getItem("FG_API_KEY") || prompt("Enter x-api-key (stored locally):");
  localStorage.setItem("FG_API_KEY", API_KEY);

  const LIMIT = 50;
  const POLL_MS = 1000;

  document.getElementById("pollMs").textContent = POLL_MS;
  document.getElementById("limit").textContent = LIMIT;

  let lastSeenId = 0;

  function esc(s){ return (s ?? "").toString().replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }

  function renderItem(item, isNew){
    const sev = (item.severity || "info").toLowerCase();
    const diff = item.decision_diff ? JSON.stringify(item.decision_diff, null, 2) : "";
    const badgeCls = `sev ${sev}`;
    return `
      <div class="row ${sev} ${isNew ? "new" : ""}">
        <div class="top">
          <div>
            <span class="${badgeCls}">${esc(sev.toUpperCase())}</span>
            <span class="k"> ${esc(item.title)}</span>
          </div>
          <div class="muted">${esc(item.timestamp)}</div>
        </div>
        <div class="muted">
          id=${esc(item.id)} • tenant=${esc(item.tenant_id)} • src=${esc(item.source)} • type=${esc(item.event_type)} • action=${esc(item.action_taken)} • conf=${esc(item.confidence)}
        </div>
        <div>${esc(item.summary)}</div>
        ${diff ? `<pre>${esc(diff)}</pre>` : ""}
      </div>
    `;
  }

  async function tick(){
    const r = await fetch(`/feed/live?limit=${LIMIT}`, {
      headers: { "x-api-key": API_KEY }
    });
    if(!r.ok){
      document.getElementById("root").innerHTML = `<div class="row high">Feed error: ${r.status} ${esc(await r.text())}</div>`;
      return;
    }
    const data = await r.json();
    const items = data.items || [];

    // detect new items by DB id
    let html = "";
    for(const it of items){
      const id = (it.id || 0);
      const isNew = id > lastSeenId;
      html += renderItem(it, isNew);
    }
    if(items.length){
      lastSeenId = Math.max(lastSeenId, ...items.map(x => x.id || 0));
    }
    document.getElementById("root").innerHTML = html;
  }

  tick();
  setInterval(tick, POLL_MS);
</script>
</body>
</html>
"""
