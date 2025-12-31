#!/usr/bin/env bash
set -euo pipefail

python -m py_compile api/defend.py >/dev/null 2>&1 || true

apply_patch <<'PATCH'
*** Begin Patch
*** Update File: api/defend.py
@@
-def _to_utc(dt: datetime | str) -> datetime:
-    if isinstance(dt, str):
-        dt = _parse_dt(dt)
-    if dt.tzinfo is None:
-        return dt.replace(tzinfo=timezone.utc)
-    return dt.astimezone(timezone.utc)
+def _to_utc(dt: datetime | str | None) -> datetime:
+    if dt is None:
+        return datetime.now(timezone.utc)
+    if isinstance(dt, str):
+        dt = _parse_dt(dt)
+    if dt.tzinfo is None:
+        return dt.replace(tzinfo=timezone.utc)
+    return dt.astimezone(timezone.utc)
*** End Patch
PATCH

python -m py_compile api/defend.py
make test
