"""Ingestion + data-read endpoints."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.database_models import LogEntry, MetricPoint, TraceSpan
from app.models.schemas import (
    IngestResult,
    LogBatch,
    LogIn,
    LogOut,
    MetricBatch,
    MetricIn,
    MetricOut,
    SampleIngestResult,
    TraceBatch,
    TraceIn,
    TraceOut,
)
from app.services import ingestion_service

router = APIRouter(tags=["ingestion"])


def _as_list(body):
    return body if isinstance(body, list) else [body]


# --------------------------- ingest ---------------------------------------- #
@router.post("/ingest/logs", response_model=IngestResult,
             summary="Ingest log event(s)",
             response_description="How many records were accepted.")
def ingest_logs(body: LogBatch, db: Session = Depends(get_db),
                _: None = Depends(require_api_key)):
    """Store one log event or a **batch** (send a JSON array).

    Each log carries `service`, `level` (INFO/WARN/ERROR/…), `message`, and
    optionally `status_code`, `latency_ms`, `endpoint`, `trace_id`, and
    `attributes`. Omit `timestamp` to default to now (UTC).

    Anything with `level=ERROR` or a `5xx` `status_code` counts as an **error**
    during anomaly detection. Unknown services are auto-registered. Detection is
    not run here — call `POST /evaluate/anomalies` afterwards.
    """
    items: List[LogIn] = _as_list(body)
    n = ingestion_service.ingest_logs(db, [i.model_dump() for i in items])
    return IngestResult(accepted=n, kind="logs")


@router.post("/ingest/metrics", response_model=IngestResult,
             summary="Ingest metric point(s)",
             response_description="How many records were accepted.")
def ingest_metrics(body: MetricBatch, db: Session = Depends(get_db),
                   _: None = Depends(require_api_key)):
    """Store one metric point or a **batch** (JSON array).

    A metric is a `service` + `name` (e.g. `latency_p95_ms`, `cpu_pct`,
    `error_rate`, `request_rate`) + numeric `value`, with an optional `unit`.
    Latency trends on the dashboard are driven by the `latency_p95_ms` metric.
    """
    items: List[MetricIn] = _as_list(body)
    n = ingestion_service.ingest_metrics(db, [i.model_dump() for i in items])
    return IngestResult(accepted=n, kind="metrics")


@router.post("/ingest/traces", response_model=IngestResult,
             summary="Ingest trace span(s)",
             response_description="How many records were accepted.")
def ingest_traces(body: TraceBatch, db: Session = Depends(get_db),
                  _: None = Depends(require_api_key)):
    """Store one trace span or a **batch** (JSON array).

    A span links to a request flow via `trace_id` / `span_id` /
    `parent_span_id`, and records the `service`, `operation`, `status`
    (OK/ERROR), `http_status`, and `duration_ms`. Spans sharing a `trace_id`
    form one request's call graph.
    """
    items: List[TraceIn] = _as_list(body)
    n = ingestion_service.ingest_traces(db, [i.model_dump() for i in items])
    return IngestResult(accepted=n, kind="traces")


@router.post("/ingest/sample-data", response_model=SampleIngestResult,
             summary="Bulk-load generated sample telemetry",
             response_description="Per-kind counts loaded and the source directory.")
def ingest_sample_data(db: Session = Depends(get_db),
                       _: None = Depends(require_api_key)):
    """Bulk-load `logs.jsonl` / `metrics.jsonl` / `traces.jsonl` from
    `data/sample_telemetry/` into the database (chunked bulk inserts).

    Generate those files first with `python scripts/generate_sample_data.py`.
    This is the fastest way to populate a demo dataset (hundreds of thousands of
    rows in seconds).
    """
    res = ingestion_service.ingest_sample_data(db)
    return SampleIngestResult(**res)


# --------------------------- read ------------------------------------------ #
@router.get("/logs", response_model=List[LogOut],
            summary="Query stored logs",
            response_description="Most recent logs first.")
def get_logs(service: Optional[str] = None, level: Optional[str] = None,
             status_code: Optional[int] = None, limit: int = Query(100, le=2000),
             db: Session = Depends(get_db)):
    """List stored log events, newest first.

    Filter by `service`, `level` (e.g. `ERROR`), and/or `status_code`
    (e.g. `500`). Use `limit` to cap results (≤ 2000).
    """
    q = select(LogEntry).order_by(LogEntry.timestamp.desc()).limit(limit)
    if service:
        q = q.where(LogEntry.service == service)
    if level:
        q = q.where(LogEntry.level == level.upper())
    if status_code:
        q = q.where(LogEntry.status_code == status_code)
    return list(db.scalars(q))


@router.get("/metrics", response_model=List[MetricOut],
            summary="Query stored metrics",
            response_description="Most recent metric points first.")
def get_metrics(service: Optional[str] = None, name: Optional[str] = None,
                limit: int = Query(200, le=5000), db: Session = Depends(get_db)):
    """List stored metric points, newest first. Filter by `service` and/or metric
    `name` (e.g. `latency_p95_ms`)."""
    q = select(MetricPoint).order_by(MetricPoint.timestamp.desc()).limit(limit)
    if service:
        q = q.where(MetricPoint.service == service)
    if name:
        q = q.where(MetricPoint.name == name)
    return list(db.scalars(q))


@router.get("/traces", response_model=List[TraceOut],
            summary="Query stored trace spans",
            response_description="Most recent spans first.")
def get_traces(service: Optional[str] = None, trace_id: Optional[str] = None,
               status: Optional[str] = None, limit: int = Query(100, le=2000),
               db: Session = Depends(get_db)):
    """List stored trace spans, newest first. Filter by `service`, a specific
    `trace_id` (to reconstruct one request flow), and/or `status` (OK/ERROR)."""
    q = select(TraceSpan).order_by(TraceSpan.timestamp.desc()).limit(limit)
    if service:
        q = q.where(TraceSpan.service == service)
    if trace_id:
        q = q.where(TraceSpan.trace_id == trace_id)
    if status:
        q = q.where(TraceSpan.status == status.upper())
    return list(db.scalars(q))
