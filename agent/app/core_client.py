# agent/app/core_client.py
from __future__ import annotations

import os
from typing import Any, Dict, Tuple, Optional

import requests


class CoreClient:
    def __init__(self, base_url: str, api_key: str, tenant_id: str, source: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else ""
        self.tenant_id = tenant_id
        self.source = source

        self.session = requests.Session()

    def _headers(self) -> Dict[str, str]:
        # FrostGate Core expects X-API-Key
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return headers

    def ready(self, timeout: int = 2) -> bool:
        url = f"{self.base_url}/health/ready"
        r = self.session.get(url, timeout=timeout)
        return r.status_code == 200

    def ingest(self, event: Dict[str, Any], timeout: int = 5) -> Tuple[int, str]:
        """
        Option A: send event to /ingest, core does decisioning and logs it.
        """
        url = f"{self.base_url}/ingest"
        r = self.session.post(url, json=event, headers=self._headers(), timeout=timeout)
        return r.status_code, r.text
