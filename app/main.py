"""FastAPI application entrypoint — API Layer.

Wires the layered routers together and initializes the database on startup.
OpenAPI docs are auto-generated at /docs and /redoc.
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import RedirectResponse

from app import __version__
from app.api import (
    routes_alerts,
    routes_dashboard,
    routes_health,
    routes_infrastructure,
    routes_ingestion,
    routes_services,
)
from app.core.database import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


tags_metadata = [
    {
        "name": "meta",
        "description": "Health checks and service metadata.",
    },
    {
        "name": "ingestion",
        "description": (
            "**Send telemetry in and read it back.** Push logs, metrics, and "
            "trace spans (one object or a JSON array for batches), or bulk-load "
            "the generated sample files. Detection is *not* run here — ingestion "
            "only validates and stores, mirroring how a collector feeds a buffer. "
            "Trigger detection separately via `POST /evaluate/anomalies`."
        ),
    },
    {
        "name": "services",
        "description": "The fictional e-commerce microservice fleet being watched.",
    },
    {
        "name": "alerts",
        "description": (
            "**The watchdog.** Run anomaly detection over a time window, list and "
            "manage the resulting alerts (acknowledge / resolve), and receive "
            "simulated webhook deliveries. Alerts are deduplicated by fingerprint "
            "with a cooldown to prevent alert fatigue."
        ),
    },
    {
        "name": "dashboard",
        "description": "Pre-aggregated data that powers the Streamlit dashboard.",
    },
    {
        "name": "infrastructure",
        "description": (
            "**Hardware observability.** The server fleet and their per-host "
            "CPU / memory / disk usage over time — for spotting resource "
            "saturation (e.g. a disk filling up) independent of application errors."
        ),
    },
]

app = FastAPI(
    title="Andela Watch — Intelligent Observability & Event Watchdog",
    description=(
        "API-first observability platform for a fictional e-commerce microservice "
        "fleet (`auth`, `checkout`, `payment`, `inventory`, `recommendation`, "
        "`notification`).\n\n"
        "**Typical flow:**\n"
        "1. `POST /ingest/*` (or `POST /ingest/sample-data`) to load telemetry.\n"
        "2. `POST /evaluate/anomalies` with a window (`1h` / `3h` / `24h`) to detect "
        "degradation and raise alerts.\n"
        "3. `GET /alerts` and `GET /dashboard/summary` to inspect results.\n\n"
        "An **error** is any `ERROR`-level log or a `5xx` status code. "
        "See the README for how this MVP maps to Kafka / Flink / Prometheus / "
        "Elasticsearch / Jaeger / Alertmanager in production."
    ),
    version=__version__,
    lifespan=lifespan,
    openapi_tags=tags_metadata,
    contact={"name": "Andela Watch"},
)

app.include_router(routes_health.router)
app.include_router(routes_ingestion.router)
app.include_router(routes_services.router)
app.include_router(routes_alerts.router)
app.include_router(routes_dashboard.router)
app.include_router(routes_infrastructure.router)


@app.get("/", include_in_schema=False)
def root():
    return RedirectResponse(url="/docs")
