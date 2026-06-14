"""Ingestion Layer.

Validates and persists logs / metrics / traces. Uses chunked bulk inserts so the
file-based path can absorb hundreds of thousands of rows quickly.

Production mapping: these endpoints stand in for an OpenTelemetry Collector +
Kafka ingestion fleet. Detection is intentionally decoupled (see anomaly_service)
exactly as a stream processor (Flink) would consume the buffer asynchronously.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.database_models import LogEntry, MetricPoint, Service, TraceSpan
from app.utils.time_utils import utcnow

_CHUNK = 5000


def _parse_ts(value) -> datetime:
    if value is None:
        return utcnow()
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    # ISO 8601 string
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return dt.replace(tzinfo=None)


def _ensure_services(db: Session, names: Iterable[str]) -> None:
    wanted = set(names)
    if not wanted:
        return
    existing = set(db.scalars(select(Service.name).where(Service.name.in_(wanted))))
    for name in wanted - existing:
        cfg = settings.service(name)
        db.add(Service(name=name, tier=cfg["tier"] if cfg else "core",
                       description=(f"{cfg['tier']} microservice" if cfg else "")))
    if wanted - existing:
        db.commit()


def _bulk_insert(db: Session, model, mappings: List[dict]) -> int:
    total = 0
    for i in range(0, len(mappings), _CHUNK):
        chunk = mappings[i:i + _CHUNK]
        db.bulk_insert_mappings(model, chunk)
        total += len(chunk)
    db.commit()
    return total


# --------------------------------------------------------------------------- #
# Per-kind ingestion (accepts dicts; API layer validates via Pydantic first)
# --------------------------------------------------------------------------- #
def ingest_logs(db: Session, items: List[dict]) -> int:
    mappings = [{
        "service": it["service"],
        "host": it.get("host"),
        "level": (it.get("level") or "INFO").upper(),
        "message": it.get("message", ""),
        "endpoint": it.get("endpoint"),
        "status_code": it.get("status_code"),
        "latency_ms": it.get("latency_ms"),
        "trace_id": it.get("trace_id"),
        "attributes": it.get("attributes"),
        "timestamp": _parse_ts(it.get("timestamp")),
    } for it in items]
    _ensure_services(db, (m["service"] for m in mappings))
    return _bulk_insert(db, LogEntry, mappings)


def ingest_metrics(db: Session, items: List[dict]) -> int:
    mappings = [{
        "service": it["service"],
        "host": it.get("host"),
        "name": it["name"],
        "value": float(it["value"]),
        "unit": it.get("unit"),
        "timestamp": _parse_ts(it.get("timestamp")),
    } for it in items]
    _ensure_services(db, (m["service"] for m in mappings))
    return _bulk_insert(db, MetricPoint, mappings)


def ingest_traces(db: Session, items: List[dict]) -> int:
    mappings = [{
        "trace_id": it["trace_id"],
        "span_id": it["span_id"],
        "parent_span_id": it.get("parent_span_id"),
        "service": it["service"],
        "operation": it.get("operation", ""),
        "status": (it.get("status") or "OK").upper(),
        "http_status": it.get("http_status"),
        "duration_ms": float(it.get("duration_ms", 0.0)),
        "timestamp": _parse_ts(it.get("timestamp")),
    } for it in items]
    _ensure_services(db, (m["service"] for m in mappings))
    return _bulk_insert(db, TraceSpan, mappings)


# --------------------------------------------------------------------------- #
# File-based bulk ingestion
# --------------------------------------------------------------------------- #
def _read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    with path.open() as f:
        return [json.loads(line) for line in f if line.strip()]


def ingest_sample_data(db: Session, sample_dir: Optional[Path] = None) -> Dict[str, int]:
    """Load logs.jsonl / metrics.jsonl / traces.jsonl from the sample directory."""
    sample_dir = Path(sample_dir or settings.SAMPLE_DIR)
    logs = _read_jsonl(sample_dir / "logs.jsonl")
    metrics = _read_jsonl(sample_dir / "metrics.jsonl")
    traces = _read_jsonl(sample_dir / "traces.jsonl")

    return {
        "logs": ingest_logs(db, logs) if logs else 0,
        "metrics": ingest_metrics(db, metrics) if metrics else 0,
        "traces": ingest_traces(db, traces) if traces else 0,
        "source_dir": str(sample_dir),
    }
