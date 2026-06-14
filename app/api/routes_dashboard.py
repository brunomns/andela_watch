"""Dashboard summary endpoint."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.models.schemas import DashboardSummary
from app.services import dashboard_service

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard/summary", response_model=DashboardSummary,
            summary="Aggregated dashboard data",
            response_description="Everything the dashboard needs for one window.")
def dashboard_summary(
    window: str = Query(settings.DEFAULT_WINDOW,
                        description="Observation window: 1h / 3h / 24h"),
    db: Session = Depends(get_db),
):
    """One-shot aggregation for the chosen observation window (`1h`/`3h`/`24h`).

    Returns the **system health score**, total requests/errors, **error volume
    over time** (bucketed), **per-service error rate & health**, **latency
    trends**, **active alerts** and history, the **anomaly timeline**, the
    **severity distribution**, and plain-English **diagnostics** pointing at the
    likely root cause per degraded service. Powers the Streamlit dashboard, but
    is a normal JSON endpoint you can consume directly.
    """
    if window not in settings.SUPPORTED_WINDOWS:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported window '{window}'. Choose one of {settings.SUPPORTED_WINDOWS}.")
    return dashboard_service.summary(db, window=window)
