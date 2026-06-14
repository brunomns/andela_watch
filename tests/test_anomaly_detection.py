"""Processing & Detection Layer tests."""
from __future__ import annotations

from datetime import timedelta

from app.services import anomaly_service, ingestion_service
from app.services.anomaly_service import health_score
from app.utils.time_utils import utcnow


def _logs(service, n, status, level, base_ts, spread_sec):
    return [{
        "service": service, "level": level, "status_code": status,
        "message": "x", "latency_ms": 100.0,
        "timestamp": (base_ts - timedelta(seconds=(i * spread_sec / n))).isoformat(),
    } for i in range(n)]


def test_health_score_monotonic():
    assert health_score(0.0, 100, 500) == 100.0
    degraded = health_score(0.5, 1500, 500)
    assert degraded < 50
    assert 0.0 <= degraded <= 100.0


def test_error_rate_and_spike_detected(db):
    now = utcnow()
    # Healthy background for one service.
    ingestion_service.ingest_logs(db, _logs("auth-service", 200, 200, "INFO",
                                            now - timedelta(minutes=2), 3000))
    # Error spike concentrated into a single detection bucket for payment-service.
    ingestion_service.ingest_logs(db, _logs("payment-service", 100, 200, "INFO",
                                            now - timedelta(minutes=30), 3000))
    ingestion_service.ingest_logs(db, _logs("payment-service", 250, 500, "ERROR",
                                            now - timedelta(seconds=120), 0))

    res = anomaly_service.evaluate(db, window="1h", now=now)
    assert res["anomalies"] > 0
    assert res["alerts_triggered"] > 0

    rules = {e.rule for e in res["details"] if e.service == "payment-service"}
    # error_rate, error_count, and z-score spike should all flag payment-service.
    assert "error_rate" in rules
    assert "error_count" in rules
    assert "error_spike" in rules


def test_healthy_service_no_anomaly(db):
    now = utcnow()
    ingestion_service.ingest_logs(db, _logs("notification-service", 300, 200, "INFO",
                                            now - timedelta(minutes=1), 3500))
    res = anomaly_service.evaluate(db, window="1h", now=now)
    anomalous = {e.rule for e in res["details"] if e.service == "notification-service"}
    assert "error_rate" not in anomalous
    assert "error_count" not in anomalous


def test_alerts_persisted_after_evaluation(db, client):
    now = utcnow()
    ingestion_service.ingest_logs(db, _logs("inventory-service", 300, 504, "ERROR",
                                            now - timedelta(seconds=30), 600))
    anomaly_service.evaluate(db, window="1h", now=now)

    alerts = client.get("/alerts?status=open").json()
    assert any(a["service"] == "inventory-service" for a in alerts)
