"""Service registry endpoint."""
from __future__ import annotations

from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models.database_models import Service
from app.models.schemas import ServiceOut

router = APIRouter(tags=["services"])


@router.get("/services", response_model=List[ServiceOut],
            summary="List monitored services",
            response_description="Registered services, alphabetical.")
def get_services(db: Session = Depends(get_db)):
    """List the microservices known to the watchdog.

    Services are seeded from config at startup and auto-registered on first
    ingest. Each has a `name` and a `tier` (`edge` / `core` / `support`).
    """
    return list(db.scalars(select(Service).order_by(Service.name)))
