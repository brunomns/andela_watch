"""Ingestion Layer tests."""
from __future__ import annotations

from app.services import ingestion_service
from app.services.telemetry_generator import write_sample_files


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_services_registered(client):
    r = client.get("/services")
    names = {s["name"] for s in r.json()}
    assert "payment-service" in names
    assert len(names) == 6


def test_ingest_single_log(client):
    r = client.post("/ingest/logs", json={
        "service": "auth-service", "level": "INFO", "message": "ok",
        "status_code": 200, "latency_ms": 12.0})
    assert r.status_code == 200
    assert r.json() == {"accepted": 1, "kind": "logs", "alerts_triggered": 0}
    assert len(client.get("/logs?service=auth-service").json()) == 1


def test_ingest_log_batch(client):
    payload = [{"service": "checkout-service", "level": "INFO", "message": f"m{i}"}
               for i in range(5)]
    r = client.post("/ingest/logs", json=payload)
    assert r.json()["accepted"] == 5


def test_ingest_metrics_and_traces(client):
    assert client.post("/ingest/metrics", json={
        "service": "payment-service", "name": "cpu_pct", "value": 42.0,
        "unit": "%"}).json()["accepted"] == 1
    assert client.post("/ingest/traces", json={
        "trace_id": "tr-1", "span_id": "sp-1", "service": "payment-service",
        "operation": "/charge", "status": "OK", "duration_ms": 100}).json()["accepted"] == 1
    assert len(client.get("/metrics?service=payment-service").json()) == 1
    assert len(client.get("/traces?service=payment-service").json()) == 1


def test_invalid_log_rejected(client):
    # missing required 'service'
    r = client.post("/ingest/logs", json={"level": "INFO"})
    assert r.status_code == 422


def test_sample_file_generation_and_ingest(db, tmp_path):
    counts = write_sample_files(out_dir=tmp_path, hours=1, logs_per_hour=300, seed=7)
    assert counts["logs"] == 300
    assert counts["metrics"] > 0
    assert counts["traces"] > 0

    res = ingestion_service.ingest_sample_data(db, sample_dir=tmp_path)
    assert res["logs"] == 300
    assert res["metrics"] == counts["metrics"]
    assert res["traces"] == counts["traces"]
