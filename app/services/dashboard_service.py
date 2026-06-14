"""Dashboard Layer (data side).

Read-only aggregations over SQLite that power both GET /dashboard/summary and the
Streamlit app. All bucketing is done in-DB (SQLite epoch division) for speed.

Production mapping: these queries would target Prometheus (metrics), Elasticsearch
(logs), and the Alertmanager API. The bucket math maps to Timescale `time_bucket`.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import Integer, case, cast, func, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.database_models import (
    Alert,
    AnomalyEvaluation,
    LogEntry,
    MetricPoint,
    Notification,
)
from app.services.alert_service import SEVERITY_ORDER, max_severity
from app.services.anomaly_service import _ERROR_FILTER, health_score
from app.utils.time_utils import (
    bucket_seconds_for,
    iter_bucket_starts,
    to_epoch,
    utcnow,
    window_bounds,
)

_ACTIVE = ("open", "acknowledged")

# Plain-English meaning of common error status codes, used to build hints.
_STATUS_HINTS = {
    401: "authentication failures — possible credential stuffing or a broken auth dependency",
    403: "authorization failures — check permissions / token scopes",
    429: "rate limiting — the service or an upstream is throttling requests",
    500: "internal server errors (HTTP 500) — likely an unhandled exception or a failing dependency",
    502: "bad gateway (HTTP 502) — an upstream returned an invalid response",
    503: "service unavailable (HTTP 503) — the service or a dependency is overloaded/degraded",
    504: "upstream timeouts (HTTP 504) — a downstream call is too slow or unresponsive",
}


def _severity_label(health: float) -> str:
    if health < 40:
        return "CRITICAL"
    if health < 60:
        return "HIGH"
    if health < 80:
        return "MEDIUM"
    return "LOW"


def _bucket_idx(col, bucket_sec: int):
    return cast(cast(func.strftime("%s", col), Integer) / bucket_sec, Integer)


def _densify(by_idx: Dict[int, float], start: datetime, end: datetime,
             bucket_sec: int) -> List[dict]:
    out = []
    for bstart in iter_bucket_starts(start, end, bucket_sec):
        idx = to_epoch(bstart) // bucket_sec
        out.append({"timestamp": bstart, "value": by_idx.get(idx, 0)})
    return out


def _service_health_rows(db: Session, start, end) -> List[dict]:
    rows = db.execute(
        select(
            LogEntry.service,
            func.count(LogEntry.id),
            func.sum(case((_ERROR_FILTER, 1), else_=0)),
            func.avg(LogEntry.latency_ms),
        )
        .where(LogEntry.timestamp >= start, LogEntry.timestamp <= end)
        .group_by(LogEntry.service)
    ).all()

    seen = {}
    for service, total, errors, avg_lat in rows:
        total = int(total or 0)
        errors = int(errors or 0)
        avg_lat = round(float(avg_lat or 0.0), 2)
        rate = round(errors / total, 4) if total else 0.0
        cfg = settings.service(service)
        slo = cfg["latency_slo_ms"] if cfg else 500
        seen[service] = {
            "service": service,
            "health_score": health_score(rate, avg_lat, slo),
            "error_rate": rate,
            "error_count": errors,
            "request_count": total,
            "avg_latency_ms": avg_lat,
        }

    # Include configured services with no traffic in-window (healthy by default).
    for cfg in settings.SERVICES:
        seen.setdefault(cfg["name"], {
            "service": cfg["name"], "health_score": 100.0, "error_rate": 0.0,
            "error_count": 0, "request_count": 0, "avg_latency_ms": 0.0,
        })
    return list(seen.values())


# What each detection rule means in plain English (for diagnosis hints).
_RULE_PHRASES = {
    "error_spike": "a sudden error spike was detected",
    "latency": "latency rose above the SLO",
    "error_rate": "the error rate is elevated",
    "error_count": "error volume is high",
}


def _peak_bucket_dominant_status(db: Session, svc: str, start, end):
    """Find the error status code dominating the service's PEAK error bucket.

    Using the peak bucket (not the whole window) surfaces a time-localized
    scenario's signature code (e.g. 401 during an auth burst) instead of being
    swamped by accumulated background 500s over a long window.
    """
    bucket = settings.ZSCORE_BUCKET_SEC
    rows = db.execute(
        select(_bucket_idx(LogEntry.timestamp, bucket).label("b"),
               LogEntry.status_code, func.count(LogEntry.id))
        .where(LogEntry.service == svc,
               LogEntry.timestamp >= start, LogEntry.timestamp <= end,
               _ERROR_FILTER, LogEntry.status_code.isnot(None))
        .group_by("b", LogEntry.status_code)
    ).all()
    if not rows:
        return None, 0
    totals, per_bucket = defaultdict(int), defaultdict(dict)
    for b, sc, c in rows:
        totals[b] += int(c)
        per_bucket[b][int(sc)] = int(c)
    peak = max(totals, key=totals.get)
    dominant = max(per_bucket[peak], key=per_bucket[peak].get)
    # Report the dominant code's total occurrences across the window for magnitude.
    window_count = db.scalar(
        select(func.count(LogEntry.id)).where(
            LogEntry.service == svc, LogEntry.status_code == dominant,
            LogEntry.timestamp >= start, LogEntry.timestamp <= end)) or 0
    return dominant, int(window_count)


def _diagnostics(db: Session, start, end, window: str, service_rows: List[dict],
                 limit: int = 6) -> List[dict]:
    """Build plain-English problem hints for the genuinely degraded services.

    Window-scoped: it uses the latest anomaly evaluation for THIS window so the
    diagnosis matches the (window-filtered) charts, rather than global open
    alerts that may have originated in a different window.
    """
    # Latest detection run's anomalous findings for this window (rules+severities).
    latest = db.scalar(
        select(func.max(AnomalyEvaluation.created_at))
        .where(AnomalyEvaluation.window_label == window))
    rules_by_svc, sev_by_svc = defaultdict(set), defaultdict(list)
    if latest is not None:
        evals = db.execute(
            select(AnomalyEvaluation.service, AnomalyEvaluation.rule,
                   AnomalyEvaluation.severity)
            .where(AnomalyEvaluation.window_label == window,
                   AnomalyEvaluation.is_anomaly.is_(True),
                   AnomalyEvaluation.created_at >= latest - timedelta(seconds=30))
        ).all()
        for svc, rule, sev in evals:
            rules_by_svc[svc].add(rule)
            sev_by_svc[svc].append(sev)

    def is_candidate(r) -> bool:
        flagged = (r["service"] in rules_by_svc
                   or r["error_rate"] >= settings.ERROR_RATE_WARN
                   or r["health_score"] < 80)
        return flagged and (r["error_count"] > 0 or r["service"] in rules_by_svc)

    candidates = sorted([r for r in service_rows if is_candidate(r)],
                        key=lambda r: (r["health_score"], -r["error_count"]))[:limit]

    out = []
    for r in candidates:
        svc = r["service"]
        dominant, dominant_count = _peak_bucket_dominant_status(db, svc, start, end)
        sample = None
        if dominant is not None:
            sample = db.scalar(
                select(LogEntry.message).where(
                    LogEntry.service == svc, LogEntry.status_code == dominant,
                    LogEntry.timestamp >= start, LogEntry.timestamp <= end
                ).limit(1))

        # What the detector flagged for this service.
        rules = rules_by_svc.get(svc, set())
        detected = [_RULE_PHRASES[x] for x in
                    ("error_spike", "latency", "error_rate", "error_count")
                    if x in rules]

        parts = []
        if detected:
            parts.append("; ".join(detected))
        if dominant is not None:
            meaning = _STATUS_HINTS.get(dominant, f"HTTP {dominant} errors")
            parts.append(f"most errors are {dominant_count}× {meaning}")
        cfg = settings.service(svc)
        slo = cfg["latency_slo_ms"] if cfg else None
        if slo and r["avg_latency_ms"] > slo and "latency" not in rules:
            parts.append(f"avg latency {r['avg_latency_ms']:.0f}ms exceeds SLO {slo}ms")
        hint = (f"{svc} — " + ". ".join(p.capitalize() for p in parts) + ".") \
            if parts else f"{svc} — degraded health."

        # Severity = worst of health-based and active-alert severities.
        severity = _severity_label(r["health_score"])
        for s in sev_by_svc.get(svc, []):
            if s in SEVERITY_ORDER:
                severity = max_severity(severity, s)

        out.append({
            "service": svc,
            "severity": severity,
            "health_score": r["health_score"],
            "error_count": r["error_count"],
            "error_rate": r["error_rate"],
            "avg_latency_ms": r["avg_latency_ms"],
            "dominant_status": dominant,
            "dominant_status_count": dominant_count,
            "sample_message": sample,
            "hint": hint,
        })
    return out


def summary(db: Session, window: Optional[str] = None,
            now: Optional[datetime] = None) -> dict:
    window = window or settings.DEFAULT_WINDOW
    now = now or utcnow()
    start, end = window_bounds(window, now)
    bucket_sec = bucket_seconds_for(window)

    # --- Totals ---
    base_win = (LogEntry.timestamp >= start) & (LogEntry.timestamp <= end)
    total_requests = db.scalar(select(func.count(LogEntry.id)).where(base_win)) or 0
    total_errors = db.scalar(
        select(func.count(LogEntry.id)).where(base_win, _ERROR_FILTER)) or 0

    # --- Error volume over time ---
    ev_rows = db.execute(
        select(_bucket_idx(LogEntry.timestamp, bucket_sec).label("b"),
               func.count(LogEntry.id))
        .where(base_win, _ERROR_FILTER)
        .group_by("b")
    ).all()
    error_volume = _densify({int(b): int(c) for b, c in ev_rows}, start, end, bucket_sec)

    # --- Per-service health ---
    service_rows = _service_health_rows(db, start, end)
    # Rank "top failing" by lowest health first, then by error rate / volume so a
    # short, intense outage outranks a high-traffic service with benign noise.
    service_rows.sort(
        key=lambda r: (r["health_score"], -r["error_rate"], -r["error_count"]))
    top_failing = [r for r in service_rows
                   if r["health_score"] < 100 or r["error_count"] > 0][:5]

    # --- System health: weighted by request volume ---
    tot_req = sum(r["request_count"] for r in service_rows)
    if tot_req > 0:
        sys_health = round(
            sum(r["health_score"] * r["request_count"] for r in service_rows) / tot_req, 1)
    else:
        sys_health = 100.0

    # --- Latency trends per service (from latency_p95_ms metric) ---
    lat_rows = db.execute(
        select(MetricPoint.service,
               _bucket_idx(MetricPoint.timestamp, bucket_sec).label("b"),
               func.avg(MetricPoint.value))
        .where(MetricPoint.name == "latency_p95_ms",
               MetricPoint.timestamp >= start, MetricPoint.timestamp <= end)
        .group_by(MetricPoint.service, "b")
    ).all()
    lat_tmp: Dict[str, Dict[int, float]] = {}
    for service, b, v in lat_rows:
        lat_tmp.setdefault(service, {})[int(b)] = round(float(v), 2)
    latency_trends = {
        svc: _densify(by_idx, start, end, bucket_sec) for svc, by_idx in lat_tmp.items()
    }

    # --- Alerts ---
    active_alerts = list(db.scalars(
        select(Alert).where(Alert.status.in_(_ACTIVE))
        .order_by(Alert.last_seen.desc()).limit(100)))
    alert_history = list(db.scalars(
        select(Alert).order_by(Alert.last_seen.desc()).limit(50)))

    sev_dist = {"LOW": 0, "MEDIUM": 0, "HIGH": 0, "CRITICAL": 0}
    for a in active_alerts:
        sev_dist[a.severity] = sev_dist.get(a.severity, 0) + 1

    # --- Simulated webhook deliveries triggered in this window ---
    webhook_notifications = list(db.scalars(
        select(Notification)
        .where(Notification.created_at >= start)
        .order_by(Notification.created_at.desc())
        .limit(200)))

    # --- Anomaly timeline (anomalies whose window touches this period) ---
    anomaly_timeline = list(db.scalars(
        select(AnomalyEvaluation)
        .where(AnomalyEvaluation.is_anomaly.is_(True),
               AnomalyEvaluation.window_end >= start)
        .order_by(AnomalyEvaluation.created_at.desc())
        .limit(100)))

    return {
        "window": window,
        "generated_at": now,
        "system_health_score": sys_health,
        "total_requests": total_requests,
        "total_errors": total_errors,
        "active_alerts": len(active_alerts),
        "error_volume_over_time": error_volume,
        "error_rate_by_service": service_rows,
        "latency_trends": latency_trends,
        "top_failing_services": top_failing,
        "active_alert_list": active_alerts,
        "alert_history": alert_history,
        "anomaly_timeline": anomaly_timeline,
        "severity_distribution": sev_dist,
        "diagnostics": _diagnostics(db, start, end, window, service_rows),
        "webhook_notifications": webhook_notifications,
        "webhooks_triggered": len(webhook_notifications),
    }


# --------------------------------------------------------------------------- #
# Infrastructure / hardware (per-server CPU / memory / disk)
# --------------------------------------------------------------------------- #
def list_hosts(db: Session) -> List[dict]:
    """The server fleet (from config), flagged with whether data is present."""
    present = set(db.scalars(
        select(MetricPoint.host).where(MetricPoint.host.isnot(None)).distinct()))
    return [{"host": s["host"], "service": s["service"], "zone": s["zone"],
             "has_data": s["host"] in present} for s in settings.SERVERS]


def _infra_status(cpu, mem, disk) -> str:
    def over(v, t):
        return v is not None and v >= t
    if over(disk, settings.DISK_USAGE_CRIT) or over(cpu, 90) or over(mem, 92):
        return "CRITICAL"
    if over(disk, settings.DISK_USAGE_WARN) or over(cpu, 75) or over(mem, 85):
        return "WARN"
    return "OK"


def infrastructure_summary(db: Session, window: Optional[str] = None,
                           hosts: Optional[List[str]] = None,
                           now: Optional[datetime] = None) -> dict:
    """Per-server hardware (cpu/memory/disk) over the window: bucketed series plus
    latest / average / peak, and a worst-of-three status per host."""
    window = window or settings.DEFAULT_WINDOW
    now = now or utcnow()
    start, end = window_bounds(window, now)
    bsec = bucket_seconds_for(window)

    servers = settings.SERVERS
    if hosts:
        wanted = set(hosts)
        servers = [s for s in servers if s["host"] in wanted]
    host_names = [s["host"] for s in servers]

    rows = db.execute(
        select(MetricPoint.host, MetricPoint.name,
               _bucket_idx(MetricPoint.timestamp, bsec).label("b"),
               func.avg(MetricPoint.value))
        .where(MetricPoint.host.in_(host_names),
               MetricPoint.name.in_(settings.HOST_METRIC_NAMES),
               MetricPoint.timestamp >= start, MetricPoint.timestamp <= end)
        .group_by(MetricPoint.host, MetricPoint.name, "b")
    ).all()

    data: dict = defaultdict(lambda: defaultdict(dict))
    for host, name, b, v in rows:
        data[host][name][int(b)] = round(float(v), 2)

    bucket_starts = iter_bucket_starts(start, end, bsec)
    hosts_out, crit, warn = [], 0, 0

    for s in servers:
        host = s["host"]
        d = data.get(host, {})

        series = []
        for bstart in bucket_starts:
            idx = to_epoch(bstart) // bsec
            series.append({
                "timestamp": bstart,
                "cpu_usage": d.get("cpu_usage", {}).get(idx),
                "memory_usage": d.get("memory_usage", {}).get(idx),
                "disk_usage": d.get("disk_usage", {}).get(idx),
            })

        def stats(name):
            by_idx = d.get(name, {})
            if not by_idx:
                return None, None, None
            latest = by_idx[max(by_idx)]
            vals = list(by_idx.values())
            return latest, round(sum(vals) / len(vals), 2), round(max(vals), 2)

        cpu_l, cpu_a, cpu_m = stats("cpu_usage")
        mem_l, mem_a, mem_m = stats("memory_usage")
        disk_l, disk_a, disk_m = stats("disk_usage")
        status = _infra_status(cpu_l, mem_l, disk_l)
        crit += status == "CRITICAL"
        warn += status == "WARN"

        hosts_out.append({
            "host": host, "service": s["service"], "zone": s["zone"],
            "cpu_latest": cpu_l, "memory_latest": mem_l, "disk_latest": disk_l,
            "cpu_avg": cpu_a, "memory_avg": mem_a, "disk_avg": disk_a,
            "cpu_max": cpu_m, "memory_max": mem_m, "disk_max": disk_m,
            "status": status, "series": series,
        })

    # Worst hosts first.
    order = {"CRITICAL": 0, "WARN": 1, "OK": 2}
    hosts_out.sort(key=lambda h: (order[h["status"]], -(h["disk_latest"] or 0)))

    return {
        "window": window,
        "generated_at": now,
        "servers_total": len(hosts_out),
        "servers_critical": crit,
        "servers_warning": warn,
        "hosts": hosts_out,
    }
