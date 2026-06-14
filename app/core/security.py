"""Optional API-key gate for write/admin endpoints.

No-op unless WATCHDOG_API_KEY is set, keeping local MVP usage frictionless.
"""
from __future__ import annotations

from typing import Optional

from fastapi import Header, HTTPException

from app.core.config import settings


def require_api_key(x_api_key: Optional[str] = Header(default=None)) -> None:
    if settings.API_KEY and x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key")
