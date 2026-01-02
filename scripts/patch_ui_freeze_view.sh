#!/usr/bin/env bash
set -euo pipefail

FILE="${1:-api/ui.py}"
[[ -f "$FILE" ]] || { echo "Missing $FILE" >&2; exit 1; }

ts="$(date +%Y%m%d_%H%M%S)"
cp -a "$FILE" "${FILE}.bak.${ts}"
echo "Backup: ${FILE}.bak.${ts}"

python - <<'PY'
from pathlib import Path
import re

path = Path("api/ui.py")
s = path.read_text(encoding="utf-8")

def must_find(pat: str, msg: str):
    if re.search(pat, s, flags=re.MULTILINE) is None:
        raise SystemExit(f"PATCH FAILED: {msg}")

# 1) Add Pause/Resume button in the top bar (next to Refresh)
must_find(r'<button onclick="refreshFull\(\)">Refresh</button>', "Refresh button not found")
s = s.replace(
    '<button onclick="refreshFull()">Refresh</button>',
    '<button onclick="refreshFull()">Refresh</button>\n'
    '    <button id="pauseBtn" onclick="togglePause()">Pause</button>'
)

# 2) Add pause state + togglePause() + openFrozen() helpers near the top of <script>
must_find(r"<script>\s*let timer", "script timer init not found")
s = re.sub(
    r"(<script>\s*let timer\s*=\s*null;\s*)",
    r"""\1
let paused = false;

function setStatus(msg){
  const status = document.getElementById("status");
  if (status) status.textContent = msg;
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

function openFrozen(title, obj){
  const w = window.open("", "_blank", "noopener,noreferrer");
  if (!w) return;

  const safeTitle = (title || "FrostGate Event").replace(/</g,"&lt;").replace(/>/g,"&gt;");
  const payload = JSON.stringify(obj || {}, null, 2)
    .replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;");

  w.document.open();
  w.document.write(`<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>${safeTitle}</title>
  <style>
    body { font-family: system-ui, sans-serif; margin: 24px; background: #0b0f14; color: #e6edf3; }
    h2 { margin: 0 0 12px; }
    pre { white-space: pre-wrap; word-break: break-word; background: #0b0f14; border: 1px solid #223; padding: 12px; border-radius: 12px; }
    .meta { opacity:.8; margin-bottom: 12px; }
  </style>
</head>
<body>
  <h2>${safeTitle}</h2>
  <div class="meta">Frozen snapshot. No polling. No refresh spam.</div>
  <pre>${payload}</pre>
</body>
</html>`);
  w.document.close();
}

""",
    s,
    flags=re.MULTILINE,
)

# 3) Auto-pause when user opens any <details> (so it doesn't collapse)
# Add listener near bottom before refreshFull(); startPoll();
must_find(r"refreshFull\(\);\s*startPoll\(\);", "boot sequence not found")
s = s.replace(
    "refreshFull();\nstartPoll();",
    """document.addEventListener("toggle", (e) => {
  // When a details element is opened, auto-pause to avoid re-render collapsing it.
  const d = e.target;
  if (d && d.tagName === "DETAILS" && d.open) {
    togglePause(true);
  }
}, true);

refreshFull();
startPoll();"""
)

# 4) Add "Open frozen" buttons to renderItem() and renderGroups() output.
# renderItem: insert button near title or meta
must_find(r"function renderItem", "renderItem not found")
s = s.replace(
    '<div class="title">${escapeHtml(i.title || "")}</div>',
    '<div class="title">${escapeHtml(i.title || "")}</div>\n'
    '      <div class="row" style="margin-top:6px;">\n'
    '        <button onclick=\'openFrozen(i.title || "Event", i)\'>Open frozen</button>\n'
    '        <button onclick="togglePause(true)">Pause</button>\n'
    '      </div>'
)

# renderGroups: add Open frozen for the latest item in group
must_find(r"function renderGroups", "renderGroups not found")
s = s.replace(
    '<div class="title">${escapeHtml(i.title || "")}</div>',
    '<div class="title">${escapeHtml(i.title || "")}</div>\n'
    '        <div class="row" style="margin-top:6px;">\n'
    '          <button onclick=\'openFrozen(i.title || "Event", i)\'>Open frozen</button>\n'
    '          <button onclick="togglePause(true)">Pause</button>\n'
    '        </div>'
)

# 5) Ensure loadIncremental respects paused state (no fetch, no repaint)
must_find(r"async function loadIncremental", "loadIncremental not found")
s = re.sub(
    r"(async function loadIncremental\([^)]*\)\s*\{\s*)",
    r"\1\n  if (paused) { return; }\n",
    s,
    count=1,
    flags=re.MULTILINE,
)

path.write_text(s, encoding="utf-8")
print("✅ Patched: Pause/Resume + auto-pause on details + Open frozen view + paused guard")
PY

python -m py_compile api/ui.py
echo "✅ Compile OK: api/ui.py"
