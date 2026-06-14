"""ORM models — the Storage Layer.

Tables: services, logs, metrics, traces, alerts, anomaly_evaluations, notifications.

Production mapping (see docs/production_evolution.md):
  logs    -> Elasticsearch / OpenSearch
  metrics -> Prometheus / TimescaleDB
  traces  -> Jaeger / Tempo
  alerts  -> Alertmanager / PagerDuty state
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class Service(Base):
    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    tier: Mapped[str] = mapped_column(String(32), default="core")
    description: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class LogEntry(Base):
    __tablename__ = "logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    host: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    level: Mapped[str] = mapped_column(String(16), index=True, default="INFO")
    message: Mapped[str] = mapped_column(String(1000), default="")
    endpoint: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    status_code: Mapped[Optional[int]] = mapped_column(Integer, index=True, nullable=True)
    latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64), index=True, nullable=True)
    attributes: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    __table_args__ = (
        Index("ix_logs_service_ts", "service", "timestamp"),
        Index("ix_logs_service_status_ts", "service", "status_code", "timestamp"),
    )


class MetricPoint(Base):
    __tablename__ = "metrics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    host: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(128), index=True)
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    __table_args__ = (
        Index("ix_metrics_service_name_ts", "service", "name", "timestamp"),
        Index("ix_metrics_host_name_ts", "host", "name", "timestamp"),
    )


class TraceSpan(Base):
    __tablename__ = "traces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    span_id: Mapped[str] = mapped_column(String(64))
    parent_span_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    operation: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16), default="OK")  # OK | ERROR
    http_status: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, default=0.0)
    timestamp: Mapped[datetime] = mapped_column(DateTime, index=True)

    __table_args__ = (
        Index("ix_traces_service_ts", "service", "timestamp"),
    )


class Alert(Base):
    __tablename__ = "alerts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Stable identity for dedup: hash(service|rule|kind).
    fingerprint: Mapped[str] = mapped_column(String(64), index=True)
    service: Mapped[Optional[str]] = mapped_column(String(128), index=True, nullable=True)
    rule: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    # LOW | MEDIUM | HIGH | CRITICAL
    severity: Mapped[str] = mapped_column(String(16), index=True, default="LOW")
    title: Mapped[str] = mapped_column(String(500))
    description: Mapped[str] = mapped_column(String(2000), default="")
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    # open | acknowledged | resolved
    status: Mapped[str] = mapped_column(String(16), index=True, default="open")
    count: Mapped[int] = mapped_column(Integer, default=1)  # dedup hit count
    context: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime, index=True)
    cooldown_until: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), nullable=False
    )


class AnomalyEvaluation(Base):
    __tablename__ = "anomaly_evaluations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    service: Mapped[str] = mapped_column(String(128), index=True)
    rule: Mapped[str] = mapped_column(String(64), index=True)
    window_label: Mapped[str] = mapped_column(String(16), default="24h")
    window_start: Mapped[datetime] = mapped_column(DateTime)
    window_end: Mapped[datetime] = mapped_column(DateTime, index=True)
    observed_value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    threshold: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_anomaly: Mapped[bool] = mapped_column(Boolean, index=True, default=False)
    severity: Mapped[str] = mapped_column(String(16), default="LOW")
    details: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )


class Notification(Base):
    """A simulated outbound webhook delivery for an alert."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    alert_id: Mapped[int] = mapped_column(ForeignKey("alerts.id"), index=True)
    channel: Mapped[str] = mapped_column(String(32), default="webhook")
    url: Mapped[str] = mapped_column(String(255), default="")
    payload: Mapped[dict] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="sent")  # sent | failed
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True, nullable=False
    )
