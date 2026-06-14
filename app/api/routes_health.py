"""Liveness / readiness."""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__

router = APIRouter(tags=["meta"])


@router.get(
    "/health",
    summary="Liveness check",
    response_description="Service status and running version.",
)
def health():
    """Lightweight liveness probe.

    Returns `{"status": "ok"}` plus the running version. Use it for uptime
    monitoring or to confirm the API is reachable before sending telemetry.
    """
    return {"status": "ok", "service": "andela-watch", "version": __version__}
