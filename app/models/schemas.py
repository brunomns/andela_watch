"""Pydantic v2 schemas — request validation + response shapes."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, ConfigDict, Field


# ===========================================================================
# Ingestion payloads
# ===========================================================================
class LogIn(BaseModel):
    service: str = Field(..., max_length=128)
    host: Optional[str] = Field(None, max_length=128)
    level: str = Field("INFO", max_length=16)
    message: str = Field("", max_length=1000)
    endpoint: Optional[str] = None
    status_code: Optional[int] = None
    latency_ms: Optional[float] = None
    trace_id: Optional[str] = None
    attributes: Optional[Dict[str, Any]] = None
    timestamp: Optional[datetime] = None


class MetricIn(BaseModel):
    service: str = Field(..., max_length=128)
    host: Optional[str] = Field(None, max_length=128)
    name: str = Field(..., max_length=128)
    value: float
    unit: Optional[str] = None
    timestamp: Optional[datetime] = None


class TraceIn(BaseModel):
    trace_id: str = Field(..., max_length=64)
    span_id: str = Field(..., max_length=64)
    parent_span_id: Optional[str] = None
    service: str = Field(..., max_length=128)
    operation: str = Field("", max_length=255)
    status: str = Field("OK", max_length=16)
    http_status: Optional[int] = None
    duration_ms: float = 0.0
    timestamp: Optional[datetime] = None


LogBatch = Union[LogIn, List[LogIn]]
MetricBatch = Union[MetricIn, List[MetricIn]]
TraceBatch = Union[TraceIn, List[TraceIn]]


# ===========================================================================
# Generic responses
# ===========================================================================
class IngestResult(BaseModel):
    accepted: int
    kind: str
    alerts_triggered: int = 0


class SampleIngestResult(BaseModel):
    logs: int
    metrics: int
    traces: int
    source_dir: str


class MessageOut(BaseModel):
    message: str


# ===========================================================================
# Entity read models
# ===========================================================================
class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    tier: str
    description: str
    created_at: datetime


class LogOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service: str
    host: Optional[str]
    level: str
    message: str
    endpoint: Optional[str]
    status_code: Optional[int]
    latency_ms: Optional[float]
    trace_id: Optional[str]
    timestamp: datetime


class MetricOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service: str
    host: Optional[str]
    name: str
    value: float
    unit: Optional[str]
    timestamp: datetime


class TraceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    trace_id: str
    span_id: str
    parent_span_id: Optional[str]
    service: str
    operation: str
    status: str
    http_status: Optional[int]
    duration_ms: float
    timestamp: datetime


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    fingerprint: str
    service: Optional[str]
    rule: str
    kind: str
    severity: str
    title: str
    description: str
    value: Optional[float]
    score: Optional[float]
    status: str
    count: int
    context: Optional[dict]
    first_seen: datetime
    last_seen: datetime
    created_at: datetime


class NotificationOut(BaseModel):
    """A simulated webhook delivery recorded when an alert fired."""

    model_config = ConfigDict(from_attributes=True)

    id: int
    alert_id: int
    channel: str
    url: str
    status: str
    payload: dict
    created_at: datetime


class AnomalyEvaluationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    service: str
    rule: str
    window_label: str
    window_start: datetime
    window_end: datetime
    observed_value: Optional[float]
    threshold: Optional[float]
    score: Optional[float]
    is_anomaly: bool
    severity: str
    details: Optional[dict]
    created_at: datetime


# ===========================================================================
# Detection / alerting requests
# ===========================================================================
class EvaluateRequest(BaseModel):
    window: str = Field("24h", description="Observation window, e.g. 1h / 3h / 24h")


class EvaluateResult(BaseModel):
    window: str
    evaluations: int
    anomalies: int
    alerts_triggered: int
    details: List[AnomalyEvaluationOut] = []


class AlertStatusUpdate(BaseModel):
    status: str = Field(..., pattern="^(open|acknowledged|resolved)$")


class WebhookPayload(BaseModel):
    """Shape the simulated /webhook/simulated receiver accepts."""

    alert_id: Optional[int] = None
    service: Optional[str] = None
    severity: Optional[str] = None
    title: Optional[str] = None
    description: Optional[str] = None
    rule: Optional[str] = None
    value: Optional[float] = None
    fired_at: Optional[datetime] = None
    extra: Optional[Dict[str, Any]] = None


# ===========================================================================
# Dashboard summary
# ===========================================================================
class TimeBucket(BaseModel):
    timestamp: datetime
    value: float


class ServiceHealth(BaseModel):
    service: str
    health_score: float
    error_rate: float
    error_count: int
    request_count: int
    avg_latency_ms: float


class ServiceDiagnostic(BaseModel):
    """A plain-English explanation of what is wrong with a service."""

    service: str
    severity: str
    health_score: float
    error_count: int
    error_rate: float
    avg_latency_ms: float
    dominant_status: Optional[int] = None
    dominant_status_count: int = 0
    sample_message: Optional[str] = None
    hint: str


class DashboardSummary(BaseModel):
    window: str
    generated_at: datetime
    system_health_score: float
    total_requests: int
    total_errors: int
    active_alerts: int
    error_volume_over_time: List[TimeBucket]
    error_rate_by_service: List[ServiceHealth]
    latency_trends: Dict[str, List[TimeBucket]]
    top_failing_services: List[ServiceHealth]
    active_alert_list: List[AlertOut]
    alert_history: List[AlertOut]
    anomaly_timeline: List[AnomalyEvaluationOut]
    severity_distribution: Dict[str, int]
    diagnostics: List[ServiceDiagnostic] = []
    webhook_notifications: List[NotificationOut] = []
    webhooks_triggered: int = 0


# ===========================================================================
# Infrastructure / hardware (per-server)
# ===========================================================================
class HostOut(BaseModel):
    host: str
    service: str
    zone: str


class HostResourcePoint(BaseModel):
    timestamp: datetime
    cpu_usage: Optional[float] = None
    memory_usage: Optional[float] = None
    disk_usage: Optional[float] = None


class HostInfra(BaseModel):
    host: str
    service: str
    zone: str
    cpu_latest: Optional[float] = None
    memory_latest: Optional[float] = None
    disk_latest: Optional[float] = None
    cpu_avg: Optional[float] = None
    memory_avg: Optional[float] = None
    disk_avg: Optional[float] = None
    cpu_max: Optional[float] = None
    memory_max: Optional[float] = None
    disk_max: Optional[float] = None
    status: str = "OK"  # OK | WARN | CRITICAL (worst of the three resources)
    series: List[HostResourcePoint] = []


class InfrastructureSummary(BaseModel):
    window: str
    generated_at: datetime
    servers_total: int
    servers_critical: int
    servers_warning: int
    hosts: List[HostInfra]
