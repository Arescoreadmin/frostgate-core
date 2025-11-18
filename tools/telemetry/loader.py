from __future__ import annotations

import json
from pathlib import Path
from typing import List, Dict, Any

from api.schemas import TelemetryInput


BASE_DIR = Path(__file__).resolve().parents[2]
GOLDEN_PATH = BASE_DIR / "tools" / "telemetry" / "golden_sample.json"


def load_golden_samples() -> List[Dict[str, Any]]:
    """
    Load golden samples from JSON.

    Each item in the file is expected to look like:
    {
      "source": "...",
      "tenant_id": "...",
      "timestamp": "...",
      "payload": {...},
      "label": "benign" | "malicious" | ...
    }

    We keep the label separate from the TelemetryInput so the rules engine
    only sees the real telemetry fields.
    """
    raw = json.loads(GOLDEN_PATH.read_text())
    samples: List[Dict[str, Any]] = []

    for item in raw:
        item = dict(item)
        label = item.pop("label", None)
        telemetry = TelemetryInput(**item)
        samples.append(
            {
                "label": label,
                "telemetry": telemetry,
            }
        )

    return samples
