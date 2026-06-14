"""Infrastructure / hardware observability endpoints (per-server)."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.schemas import HostOut, InfrastructureSummary
from app.services import dashboard_service

router = APIRouter(tags=["infrastructure"])


@router.get("/hosts", response_model=List[HostOut],
            summary="List servers in the fleet",
            response_description="Each server with its service and availability zone.")
def get_hosts(db: Session = Depends(get_db)):
    """List the (hypothetical) distributed server fleet being monitored.

    Each server hosts a service and lives in an availability `zone`. Use a
    server's `host` to filter `/infrastructure/summary`, `/logs`, or `/metrics`.
    """
    return [HostOut(host=h["host"], service=h["service"], zone=h["zone"])
            for h in dashboard_service.list_hosts(db)]


@router.get("/infrastructure/summary", response_model=InfrastructureSummary,
            summary="Per-server hardware usage (CPU / memory / disk)",
            response_description="Bucketed series plus latest/avg/peak per server.")
def infrastructure_summary(
    window: str = Query(settings.DEFAULT_WINDOW,
                        description="Observation window: 30m / 1h / 2h / 3h / 6h / "
                                    "12h / 24h / 7d"),
    host: Optional[List[str]] = Query(None,
                                      description="Repeat to select specific servers; "
                                                  "omit for all."),
    db: Session = Depends(get_db),
):
    """Hardware telemetry for the server fleet over the chosen window.

    Returns, per server, the **CPU / memory / disk** time series (bucketed to the
    window) plus **latest**, **average**, and **peak** values, and a worst-of-the-
    three **status** (`OK` / `WARN` / `CRITICAL`). Disk crossing 80% warns and 90%
    is critical. Pass `host=` (repeatable) to focus on specific servers.
    """
    if window not in settings.SUPPORTED_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported window '{window}'. Choose one of {settings.SUPPORTED_WINDOWS}.")
    return dashboard_service.infrastructure_summary(db, window=window, hosts=host)
