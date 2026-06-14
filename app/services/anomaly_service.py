"""Processing & Detection Layer.

Statistical anomaly detection over a configurable observation window. Rules:
  * error_rate   — rolling error fraction vs. threshold
  * error_count  — absolute error volume in the window
  * latency      — average latency vs. per-service SLO
  * error_spike  — z-score of per-bucket error counts (sudden spike detection)
  * health_score — composite 0-100 per-service health (informational)

Each rule writes an AnomalyEvaluation row; anomalous findings raise alerts through
the Alert Manager (which applies dedup + cooldown).

Production mapping: this is the Flink/stream-processing job. Here it runs as an
on-demand batch over SQLite; the rules are identical in spirit.
"""
from __future__ import annotations

from datetime import datetime
from statistics import mean, pstdev
from typing import Dict, List, Optional, Tuple

from sqlalchemy import Integer, cast, func, or_, select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.database_models import AnomalyEvaluation, LogEntry
from app.services import alert_service
from app.utils.time_utils import iter_bucket_starts, to_epoch, utcnow, window_bounds

# An error = explicit ERROR log OR a 5xx status.
_ERROR_FILTER = or_(LogEntry.level == "ERROR", LogEntry.status_code >= 500)


# --------------------------------------------------------------------------- #
# Severity mappings
# --------------------------------------------------------------------------- #
def _sev_error_rate(r: float) -> str:
    if r >= settings.ERROR_RATE_CRIT:
        return "CRITICAL"
    if r >= settings.ERROR_RATE_HIGH:
        return "HIGH"
    if r >= settings.ERROR_RATE_WARN:
        return "MEDIUM"
    return "LOW"


def _sev_error_count(c: int, warn: float, high: float, crit: float) -> str:
    if c >= crit:
        return "CRITICAL"
    if c >= high:
        return "HIGH"
    if c >= warn:
        return "MEDIUM"
    return "LOW"


def _sev_zscore(z: float) -> str:
    if z >= 5:
        return "CRITICAL"
    if z >= 4:
        return "HIGH"
    if z >= settings.ZSCORE_THRESHOLD:
        return "MEDIUM"
    return "LOW"


def _sev_latency(ratio: float) -> str:
    if ratio >= 3:
        return "CRITICAL"
    if ratio >= 2:
        return "HIGH"
    if ratio >= 1:
        return "MEDIUM"
    return "LOW"


def health_score(error_rate: float, avg_latency: float, slo: float) -> float:
    """Composite 0-100. Penalize error rate and latency over SLO."""
    score = 100.0
    score -= min(70.0, error_rate * 140.0)
    if slo > 0 and avg_latency > slo:
        score -= min(40.0, (avg_latency / slo - 1.0) * 40.0)
    return round(max(0.0, score), 1)


# --------------------------------------------------------------------------- #
# Per-service stats
# --------------------------------------------------------------------------- #
def _service_window_stats(db: Session, service: str, start: datetime,
                          end: datetime) -> Dict:
    base = (LogEntry.service == service) & (LogEntry.timestamp >= start) & (
        LogEntry.timestamp <= end)

    total = db.scalar(select(func.count(LogEntry.id)).where(base)) or 0
    errors = db.scalar(select(func.count(LogEntry.id)).where(base, _ERROR_FILTER)) or 0
    avg_latency = db.scalar(select(func.avg(LogEntry.latency_ms)).where(base)) or 0.0

    # Per-bucket error counts for z-score spike detection.
    bucket = settings.ZSCORE_BUCKET_SEC
    bucket_idx = cast(cast(func.strftime("%s", LogEntry.timestamp), Integer) / bucket,
                      Integer)
    rows = db.execute(
        select(bucket_idx.label("b"), func.count(LogEntry.id))
        .where(base, _ERROR_FILTER)
        .group_by("b")
    ).all()
    counts_by_idx = {int(b): int(c) for b, c in rows}

    # Build a dense series (missing buckets = 0) across the whole window.
    series = []
    for bstart in iter_bucket_starts(start, end, bucket):
        idx = to_epoch(bstart) // bucket
        series.append(counts_by_idx.get(idx, 0))

    return {
        "total": total,
        "errors": errors,
        "avg_latency": round(float(avg_latency), 2),
        "error_rate": round(errors / total, 4) if total else 0.0,
        "bucket_series": series,
    }


def _zscore_peak(series: List[int]) -> Tuple[float, int]:
    """z-score of the peak bucket vs. the series. Returns (z, peak_count)."""
    if len(series) < settings.MIN_BUCKETS_FOR_ZSCORE:
        return 0.0, (max(series) if series else 0)
    mu = mean(series)
    sigma = pstdev(series)
    peak = max(series)
    if sigma == 0:
        return 0.0, peak
    return round((peak - mu) / sigma, 2), peak


# --------------------------------------------------------------------------- #
# Main entry point
# --------------------------------------------------------------------------- #
def evaluate(db: Session, window: str = "24h",
             now: Optional[datetime] = None) -> Dict:
    """Run all rules for all services over the window. Persist evaluations and
    raise alerts for anomalies. Returns a summary dict."""
    now = now or utcnow()
    start, end = window_bounds(window, now)

    # Scale error-count thresholds by window length (per-hour rates).
    hours = max(1.0, (end - start).total_seconds() / 3600.0)
    ec_warn = settings.ERROR_COUNT_WARN_PER_HOUR * hours
    ec_high = settings.ERROR_COUNT_HIGH_PER_HOUR * hours
    ec_crit = settings.ERROR_COUNT_CRIT_PER_HOUR * hours

    evaluations: List[AnomalyEvaluation] = []
    anomalies = 0
    fired = 0

    def record(service, rule, observed, threshold, score, is_anom, severity, details):
        ev = AnomalyEvaluation(
            service=service, rule=rule, window_label=window,
            window_start=start, window_end=end,
            observed_value=observed, threshold=threshold, score=score,
            is_anomaly=is_anom, severity=severity, details=details,
        )
        db.add(ev)
        evaluations.append(ev)
        return ev

    for cfg in settings.SERVICES:
        service = cfg["name"]
        slo = cfg["latency_slo_ms"]
        st = _service_window_stats(db, service, start, end)
        z, peak = _zscore_peak(st["bucket_series"])

        # --- rule: error_rate ---
        er = st["error_rate"]
        er_anom = er >= settings.ERROR_RATE_WARN and st["errors"] > 0
        er_sev = _sev_error_rate(er)
        record(service, "error_rate", er, settings.ERROR_RATE_WARN, er,
               er_anom, er_sev, {"errors": st["errors"], "total": st["total"]})
        if er_anom:
            anomalies += 1
            _, f = alert_service.raise_alert(
                db, service=service, rule="error_rate", kind="rolling_error_rate",
                severity=er_sev,
                title=f"High error rate on {service} ({er:.0%})",
                description=f"{st['errors']}/{st['total']} requests errored in {window}.",
                value=er, score=er, now=now,
                context={"window": window, "errors": st["errors"], "total": st["total"]})
            fired += int(f)

        # --- rule: error_count (window-scaled thresholds) ---
        ec = st["errors"]
        ec_anom = ec >= ec_warn
        ec_sev = _sev_error_count(ec, ec_warn, ec_high, ec_crit)
        record(service, "error_count", float(ec), round(ec_warn, 1), float(ec),
               ec_anom, ec_sev, {"window": window, "threshold": round(ec_warn, 1)})
        if ec_anom:
            anomalies += 1
            _, f = alert_service.raise_alert(
                db, service=service, rule="error_count", kind="error_volume",
                severity=ec_sev,
                title=f"Elevated error volume on {service} ({ec} errors)",
                description=f"{ec} errors in {window} (threshold {ec_warn:.0f}).",
                value=float(ec), score=float(ec), now=now,
                context={"window": window})
            fired += int(f)

        # --- rule: latency ---
        lat = st["avg_latency"]
        ratio = (lat / slo) if slo else 0.0
        lat_anom = lat > slo and st["total"] > 0
        lat_sev = _sev_latency(ratio)
        record(service, "latency", lat, float(slo), round(ratio, 2),
               lat_anom, lat_sev, {"slo_ms": slo})
        if lat_anom:
            anomalies += 1
            _, f = alert_service.raise_alert(
                db, service=service, rule="latency", kind="latency_threshold",
                severity=lat_sev,
                title=f"Latency degradation on {service} ({lat:.0f}ms)",
                description=f"Avg latency {lat:.0f}ms exceeds SLO {slo}ms in {window}.",
                value=lat, score=round(ratio, 2), now=now,
                context={"slo_ms": slo, "window": window})
            fired += int(f)

        # --- rule: error_spike (z-score) ---
        spike_anom = z >= settings.ZSCORE_THRESHOLD and peak >= settings.ZSCORE_MIN_PEAK
        z_sev = _sev_zscore(z)
        record(service, "error_spike", float(peak), settings.ZSCORE_THRESHOLD, z,
               spike_anom, z_sev,
               {"peak_bucket_errors": peak, "bucket_sec": settings.ZSCORE_BUCKET_SEC})
        if spike_anom:
            anomalies += 1
            _, f = alert_service.raise_alert(
                db, service=service, rule="error_spike", kind="zscore_spike",
                severity=z_sev,
                title=f"Sudden error spike on {service} (z={z})",
                description=(f"Peak {peak} errors in a "
                            f"{settings.ZSCORE_BUCKET_SEC//60}m bucket (z-score {z})."),
                value=float(peak), score=z, now=now,
                context={"bucket_sec": settings.ZSCORE_BUCKET_SEC})
            fired += int(f)

        # --- rule: health_score (informational) ---
        hs = health_score(er, lat, slo)
        record(service, "health_score", hs, 70.0, hs, hs < 70.0,
               ("CRITICAL" if hs < 40 else "HIGH" if hs < 60 else
                "MEDIUM" if hs < 70 else "LOW"),
               {"error_rate": er, "avg_latency_ms": lat})

    db.commit()
    for ev in evaluations:
        db.refresh(ev)

    return {
        "window": window,
        "evaluations": len(evaluations),
        "anomalies": anomalies,
        "alerts_triggered": fired,
        "details": [ev for ev in evaluations if ev.is_anomaly],
    }
