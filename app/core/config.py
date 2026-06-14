"""Central configuration.

Everything tunable lives here. Values are overridable via environment variables
so the same code runs locally (SQLite) or, later, against Postgres/Timescale.
"""
from __future__ import annotations

import os
from pathlib import Path

# Project root = .../andela_watch  (this file is app/core/config.py)
BASE_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("WATCHDOG_DATA_DIR", BASE_DIR / "data"))
SAMPLE_DIR = DATA_DIR / "sample_telemetry"
DB_PATH = DATA_DIR / "observability.db"


class Settings:
    # --- Storage -----------------------------------------------------------
    # Default: local SQLite. In production swap to Postgres/Timescale via env:
    #   WATCHDOG_DB_URL=postgresql+psycopg://user:pass@host/db
    DB_URL: str = os.getenv("WATCHDOG_DB_URL", f"sqlite:///{DB_PATH}")

    DATA_DIR: Path = DATA_DIR
    SAMPLE_DIR: Path = SAMPLE_DIR

    # --- Security ----------------------------------------------------------
    # If set, ingest/admin endpoints require header  X-API-Key: <value>.
    API_KEY: str | None = os.getenv("WATCHDOG_API_KEY") or None

    # Dashboard login (demo-grade). Override via env in any real deployment.
    DASH_USER: str = os.getenv("WATCHDOG_DASH_USER", "admin")
    DASH_PASSWORD: str = os.getenv("WATCHDOG_DASH_PASSWORD", "123456")

    # --- Telemetry generation defaults -------------------------------------
    GEN_HOURS: int = int(os.getenv("WATCHDOG_GEN_HOURS", "24"))
    GEN_LOGS_PER_HOUR: int = int(os.getenv("WATCHDOG_GEN_LOGS_PER_HOUR", "10000"))
    GEN_SEED: int = int(os.getenv("WATCHDOG_GEN_SEED", "1337"))

    # --- Observation windows offered by the UI / API (Grafana-style) -------
    SUPPORTED_WINDOWS = ["30m", "1h", "2h", "3h", "6h", "12h", "24h", "7d"]
    DEFAULT_WINDOW = "24h"

    # --- Anomaly detection thresholds (global defaults; per-service in
    #     SERVICES below can override the latency baseline) -----------------
    # Rolling error-rate alert thresholds (errors / total requests).
    ERROR_RATE_WARN = 0.10
    ERROR_RATE_HIGH = 0.25
    ERROR_RATE_CRIT = 0.40
    # Error-count thresholds are expressed PER HOUR and scaled by the window
    # length at evaluation time. This keeps a 24h window from flagging healthy
    # high-traffic services purely on accumulated background errors.
    ERROR_COUNT_WARN_PER_HOUR = 80
    ERROR_COUNT_HIGH_PER_HOUR = 200
    ERROR_COUNT_CRIT_PER_HOUR = 500
    # z-score (per-bucket error count spike vs. window baseline).
    ZSCORE_THRESHOLD = 3.0
    MIN_BUCKETS_FOR_ZSCORE = 6
    # Detection bucket size for the z-score rule (seconds).
    ZSCORE_BUCKET_SEC = 300  # 5 minutes
    # Ignore "spikes" whose peak bucket is below this absolute count — prevents
    # low-variance baselines from flagging tiny random clusters of errors.
    ZSCORE_MIN_PEAK = 10

    # --- Alert fatigue protection -----------------------------------------
    ALERT_COOLDOWN_SEC = int(os.getenv("WATCHDOG_ALERT_COOLDOWN_SEC", "600"))  # 10 min

    # --- Simulated webhook -------------------------------------------------
    WEBHOOK_URL = os.getenv("WATCHDOG_WEBHOOK_URL", "local://simulated-webhook")

    # --- The fictional microservice fleet ---------------------------------
    # base_latency_ms : healthy p95-ish latency
    # latency_slo_ms  : alert when window latency exceeds this
    # base_error_rate : healthy background error fraction
    SERVICES = [
        {"name": "auth-service",          "tier": "edge",     "base_latency_ms": 80,  "latency_slo_ms": 250,  "base_error_rate": 0.01, "base_rps": 1.4},
        {"name": "checkout-service",      "tier": "core",     "base_latency_ms": 180, "latency_slo_ms": 500,  "base_error_rate": 0.015,"base_rps": 1.0},
        {"name": "payment-service",       "tier": "core",     "base_latency_ms": 220, "latency_slo_ms": 600,  "base_error_rate": 0.02, "base_rps": 0.9},
        {"name": "inventory-service",     "tier": "core",     "base_latency_ms": 130, "latency_slo_ms": 400,  "base_error_rate": 0.015,"base_rps": 1.1},
        {"name": "recommendation-service","tier": "support",  "base_latency_ms": 90,  "latency_slo_ms": 350,  "base_error_rate": 0.01, "base_rps": 1.3},
        {"name": "notification-service",  "tier": "support",  "base_latency_ms": 70,  "latency_slo_ms": 300,  "base_error_rate": 0.01, "base_rps": 1.0},
    ]

    # --- The (hypothetical) distributed server fleet ----------------------
    # Each server hosts one service. Hardware metrics (cpu/memory/disk) are
    # generated per server. `disk_pressure` servers trend toward a full disk so
    # there is a clear infra anomaly to observe.
    SERVERS = [
        {"host": "auth-host-1",      "service": "auth-service",           "zone": "us-east-1a", "cpu_base": 32, "mem_base": 54, "disk_base": 47},
        {"host": "auth-host-2",      "service": "auth-service",           "zone": "us-east-1b", "cpu_base": 30, "mem_base": 51, "disk_base": 44},
        {"host": "checkout-host-1",  "service": "checkout-service",       "zone": "us-east-1a", "cpu_base": 40, "mem_base": 60, "disk_base": 52},
        {"host": "payment-host-1",   "service": "payment-service",        "zone": "us-east-1a", "cpu_base": 45, "mem_base": 63, "disk_base": 55},
        {"host": "payment-host-2",   "service": "payment-service",        "zone": "us-east-1b", "cpu_base": 43, "mem_base": 61, "disk_base": 58},
        {"host": "inventory-host-1", "service": "inventory-service",      "zone": "us-east-1a", "cpu_base": 38, "mem_base": 57, "disk_base": 50},
        {"host": "inventory-host-2", "service": "inventory-service",      "zone": "us-east-1c", "cpu_base": 36, "mem_base": 55, "disk_base": 49},
        {"host": "reco-host-1",      "service": "recommendation-service", "zone": "us-east-1a", "cpu_base": 50, "mem_base": 66, "disk_base": 60},
        {"host": "notif-host-1",     "service": "notification-service",   "zone": "us-east-1b", "cpu_base": 28, "mem_base": 48, "disk_base": 42},
        {"host": "shared-db-1",      "service": "inventory-service",      "zone": "us-east-1a", "cpu_base": 55, "mem_base": 72, "disk_base": 85, "disk_pressure": True},
    ]

    # Host hardware metric names (kept distinct from service-level cpu_pct etc.).
    HOST_METRIC_NAMES = ["cpu_usage", "memory_usage", "disk_usage"]
    DISK_USAGE_WARN = 80.0
    DISK_USAGE_CRIT = 90.0

    @classmethod
    def service_names(cls) -> list[str]:
        return [s["name"] for s in cls.SERVICES]

    @classmethod
    def service(cls, name: str) -> dict | None:
        for s in cls.SERVICES:
            if s["name"] == name:
                return s
        return None

    @classmethod
    def server_names(cls) -> list[str]:
        return [s["host"] for s in cls.SERVERS]

    @classmethod
    def server(cls, host: str) -> dict | None:
        for s in cls.SERVERS:
            if s["host"] == host:
                return s
        return None


settings = Settings()
