from __future__ import annotations

import os
from urllib.parse import urljoin, urlparse

import httpx


class CoreClient:
    """
    Read-only client with a provable blast radius:
      - Only allows GET calls to /decisions (and subpaths).
      - Refuses any other path even if misconfigured.
    """

    # Allowed prefix (hardcoded)
    ALLOWED_PREFIX = "/decisions"

    def __init__(self) -> None:
        base_url = os.getenv("FG_CORE_BASE_URL", "http://frostgate-core:8080").strip()
        api_key = os.getenv("FG_AGENT_API_KEY", "").strip()
        timeout_s = float(os.getenv("FG_AGENT_TIMEOUT_SECONDS", "2"))

        if not api_key:
            raise RuntimeError("FG_AGENT_API_KEY is required")

        # Normalize base URL (no trailing slash)
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout_s)

        # Safety: refuse weird schemes
        parsed = urlparse(self.base_url)
        if parsed.scheme not in ("http", "https"):
            raise RuntimeError(f"Invalid FG_CORE_BASE_URL scheme: {parsed.scheme}")

        self._client = httpx.Client(
            headers={"X-API-Key": self.api_key},
            timeout=self.timeout,
            follow_redirects=False,
        )

    def close(self) -> None:
        self._client.close()

    def _assert_allowed_path(self, path: str) -> None:
        # Must start with /decisions exactly
        if not path.startswith(self.ALLOWED_PREFIX):
            raise ValueError(f"Blocked path '{path}'. Allowed only: {self.ALLOWED_PREFIX}")

        # Block path traversal / weirdness
        if ".." in path or "//" in path:
            raise ValueError(f"Blocked suspicious path '{path}'")

    def get_decisions(self, query: dict | None = None) -> dict:
        path = "/decisions"
        self._assert_allowed_path(path)

        url = urljoin(self.base_url + "/", path.lstrip("/"))
        return self._client.get(url, params=query or {}).json()

    def get_decision(self, decision_id: str) -> dict:
        path = f"/decisions/{decision_id}"
        self._assert_allowed_path(path)

        url = urljoin(self.base_url + "/", path.lstrip("/"))
        return self._client.get(url).json()
