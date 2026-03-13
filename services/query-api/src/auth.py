"""API key authentication via X-API-Key header.

References:
  - TDD: Section 9.1 (API Key Auth)
"""

from __future__ import annotations

import os

from fastapi import HTTPException, Request


def _get_valid_keys() -> set[str]:
    """Load valid API keys from the API_KEYS environment variable.

    Keys are comma-separated. If API_KEYS is not set or empty,
    authentication is disabled (all requests pass).
    """
    raw = os.getenv("API_KEYS", "")
    if not raw.strip():
        return set()
    return {k.strip() for k in raw.split(",") if k.strip()}


async def verify_api_key(request: Request) -> None:
    """FastAPI dependency that checks the X-API-Key header.

    If API_KEYS env var is empty/unset, auth is disabled (dev mode).
    Otherwise, requests without a valid key receive HTTP 401.
    """
    valid_keys = _get_valid_keys()

    # No keys configured → auth disabled (local dev)
    if not valid_keys:
        return

    api_key = request.headers.get("X-API-Key", "")
    if api_key not in valid_keys:
        raise HTTPException(status_code=401, detail="Invalid or missing API key")
