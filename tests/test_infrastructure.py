"""Infrastructure / hardware observability tests."""
from __future__ import annotations

from app.core.config import settings
from app.services import dashboard_service, ingestion_service, telemetry_generator


def test_supported_windows_expanded():
    for w in ["30m", "1h", "2h", "3h", "6h", "12h", "24h", "7d"]:
        assert w in settings.SUPPORTED_WINDOWS


def test_generator_emits_host_metrics():
    data = telemetry_generator.generate_in_memory(hours=1, logs_per_hour=200, seed=5)
    assert data["host_metrics"], "generator should emit host hardware metrics"
    names = {m["name"] for m in data["host_metrics"]}
    assert {"cpu_usage", "memory_usage", "disk_usage"} <= names
    # logs now carry a host too
    assert all(log.get("host") for log in data["logs"])


def test_host_metrics_ingest_and_infra_summary(db):
    data = telemetry_generator.generate_in_memory(hours=1, logs_per_hour=100, seed=5)
    n = ingestion_service.ingest_metrics(db, data["host_metrics"])
    assert n == len(data["host_metrics"])

    inf = dashboard_service.infrastructure_summary(db, window="1h")
    assert inf["servers_total"] == len(settings.SERVERS)
    by_host = {h["host"]: h for h in inf["hosts"]}

    # disk-pressure server should be near-full and flagged.
    sd = by_host["shared-db-1"]
    assert sd["disk_latest"] is not None and sd["disk_latest"] >= settings.DISK_USAGE_WARN
    assert sd["status"] in ("WARN", "CRITICAL")
    assert len(sd["series"]) > 0

    # a normal server should be healthy.
    assert by_host["notif-host-1"]["status"] == "OK"


def test_hosts_endpoint(client):
    r = client.get("/hosts")
    assert r.status_code == 200
    hosts = r.json()
    assert len(hosts) == len(settings.SERVERS)
    sd = [h for h in hosts if h["host"] == "shared-db-1"][0]
    assert sd["service"] == "inventory-service" and sd["zone"]


def test_infrastructure_endpoint_and_host_filter(db, client):
    data = telemetry_generator.generate_in_memory(hours=1, logs_per_hour=100, seed=3)
    ingestion_service.ingest_metrics(db, data["host_metrics"])

    r = client.get("/infrastructure/summary?window=1h&host=shared-db-1")
    assert r.status_code == 200
    body = r.json()
    assert body["servers_total"] == 1
    assert body["hosts"][0]["host"] == "shared-db-1"
    assert len(body["hosts"][0]["series"]) > 0


def test_infrastructure_rejects_bad_window(client):
    r = client.get("/infrastructure/summary?window=5y")
    assert r.status_code == 400
