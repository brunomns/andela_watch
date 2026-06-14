"""Telemetry Simulation Layer.

Generates realistic synthetic logs / metrics / trace-like records for the
fictional e-commerce microservice fleet, including deterministic anomaly
scenarios so the dashboard always tells a clear story.

Production mapping: in a real system this layer does not exist — telemetry comes
from OpenTelemetry SDKs/collectors embedded in each service. Here we synthesize
the same shapes so the rest of the pipeline is identical.

Determinism: values are driven by a seeded RNG, so the *shape* of the data is
reproducible. Timestamps are anchored to "now" so the data always lands inside
the selectable 1h / 3h / 24h windows.

Injected anomaly scenarios (offsets are "seconds before now"):
  * auth-service       — repeated failed logins (401 + ERROR)   ~50m..35m ago  (visible in 1h)
  * payment-service    — HTTP 500 spike                         ~130m..90m ago (visible in 3h)
  * inventory-service  — upstream timeout errors (504)          ~6h..5h20m ago (visible in 24h)
  * checkout-service   — latency degradation (+503s)            ~10h..8.5h ago (visible in 24h)
"""
from __future__ import annotations

import json
import random
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Iterator, List, Optional

from app.core.config import settings
from app.utils.time_utils import utcnow

# --------------------------------------------------------------------------- #
# Scenario catalogue
# --------------------------------------------------------------------------- #
ANOMALY_SCENARIOS = [
    {
        "service": "auth-service",
        "type": "failed_login_burst",
        "start_ago": 3000, "end_ago": 2100,            # 50m .. 35m ago
        "error_status": 401, "error_rate": 0.55, "latency_mult": 1.3,
        "message": "authentication failed: invalid credentials",
        "description": "Repeated failed login attempts (credential stuffing pattern).",
    },
    {
        "service": "payment-service",
        "type": "http_500_spike",
        "start_ago": 7800, "end_ago": 5400,            # 130m .. 90m ago
        "error_status": 500, "error_rate": 0.6, "latency_mult": 1.8,
        "message": "payment gateway returned 500: transaction processor error",
        "description": "Spike in HTTP 500 errors from the payment processor.",
    },
    {
        "service": "inventory-service",
        "type": "timeout_errors",
        "start_ago": 21600, "end_ago": 19200,          # 6h .. 5h20m ago
        "error_status": 504, "error_rate": 0.5, "latency_mult": 3.5,
        "message": "upstream inventory store timeout after 5000ms",
        "description": "Inventory store timeouts (504) under load.",
    },
    {
        "service": "checkout-service",
        "type": "latency_degradation",
        "start_ago": 36000, "end_ago": 30600,          # 10h .. 8.5h ago
        "error_status": 503, "error_rate": 0.12, "latency_mult": 4.5,
        "message": "checkout slow: downstream dependency degraded",
        "description": "Sustained checkout latency degradation with some 503s.",
    },
]

ENDPOINTS = {
    "auth-service": ["/login", "/logout", "/token/refresh", "/verify"],
    "checkout-service": ["/cart", "/checkout", "/checkout/confirm"],
    "payment-service": ["/charge", "/refund", "/payment/status"],
    "inventory-service": ["/stock", "/reserve", "/release"],
    "recommendation-service": ["/recommend", "/similar", "/trending"],
    "notification-service": ["/notify/email", "/notify/sms", "/notify/push"],
}

INFO_MESSAGES = [
    "request completed", "handled ok", "processed successfully",
    "cache hit", "request accepted",
]

METRIC_NAMES = ["request_rate", "error_rate", "latency_p95_ms", "cpu_pct", "memory_pct"]


def _active_scenario(service: str, ts: datetime, now: datetime) -> Optional[dict]:
    """Return the anomaly scenario active for `service` at `ts`, if any."""
    ago = (now - ts).total_seconds()
    for sc in ANOMALY_SCENARIOS:
        if sc["service"] == service and sc["end_ago"] <= ago <= sc["start_ago"]:
            return sc
    return None


def _hosts_for_service(service: str) -> list:
    return [s["host"] for s in settings.SERVERS if s["service"] == service] or ["unknown-host"]


def _pick_host(rng: random.Random, service: str) -> str:
    return rng.choice(_hosts_for_service(service))


def _weighted_service_picker(rng: random.Random):
    names = [s["name"] for s in settings.SERVICES]
    weights = [s["base_rps"] for s in settings.SERVICES]
    total = sum(weights)
    cum, acc = [], 0.0
    for w in weights:
        acc += w / total
        cum.append(acc)

    def pick() -> str:
        r = rng.random()
        for name, c in zip(names, cum):
            if r <= c:
                return name
        return names[-1]

    return pick


def _latency(rng: random.Random, base: float, mult: float = 1.0) -> float:
    """Lognormal-ish latency around the service baseline."""
    factor = rng.lognormvariate(0.0, 0.35)
    return round(base * factor * mult, 2)


# --------------------------------------------------------------------------- #
# Generators (memory-light: yield one record at a time)
# --------------------------------------------------------------------------- #
def iter_logs(rng: random.Random, start: datetime, end: datetime,
              total_logs: int, now: datetime) -> Iterator[dict]:
    pick = _weighted_service_picker(rng)
    span = (end - start).total_seconds()
    for i in range(total_logs):
        service = pick()
        svc = settings.service(service)
        ts = start + timedelta(seconds=rng.random() * span)
        scenario = _active_scenario(service, ts, now)

        endpoint = rng.choice(ENDPOINTS[service])
        host = _pick_host(rng, service)
        trace_id = f"tr-{rng.getrandbits(48):012x}"

        if scenario and rng.random() < scenario["error_rate"]:
            status = scenario["error_status"]
            level = "ERROR" if status >= 500 or status == 401 else "WARN"
            message = scenario["message"]
            latency = _latency(rng, svc["base_latency_ms"], scenario["latency_mult"])
        else:
            # Healthy traffic with a little background noise.
            roll = rng.random()
            lat_mult = scenario["latency_mult"] if scenario else 1.0
            if roll < svc["base_error_rate"]:
                status, level = 500, "ERROR"
                message = "internal error"
            elif roll < svc["base_error_rate"] + 0.02:
                status, level = 400, "WARN"
                message = "bad request"
            else:
                status, level = 200, "INFO"
                message = rng.choice(INFO_MESSAGES)
            latency = _latency(rng, svc["base_latency_ms"], lat_mult)

        yield {
            "service": service,
            "host": host,
            "level": level,
            "message": message,
            "endpoint": endpoint,
            "status_code": status,
            "latency_ms": latency,
            "trace_id": trace_id,
            "attributes": {"zone": settings.server(host)["zone"]
                           if settings.server(host) else "us-east-1", "env": "prod"},
            "timestamp": ts.isoformat(),
        }


def iter_metrics(rng: random.Random, start: datetime, end: datetime,
                 now: datetime) -> Iterator[dict]:
    """Per-minute gauges per service (request_rate, error_rate, latency_p95, cpu, mem)."""
    step = timedelta(minutes=1)
    for svc in settings.SERVICES:
        ts = start
        while ts <= end:
            scenario = _active_scenario(svc["name"], ts, now)
            err_rate = svc["base_error_rate"]
            lat_mult = 1.0
            if scenario:
                err_rate = scenario["error_rate"]
                lat_mult = scenario["latency_mult"]

            values = {
                "request_rate": round(svc["base_rps"] * 60 * rng.uniform(0.8, 1.2), 2),
                "error_rate": round(min(1.0, err_rate * rng.uniform(0.85, 1.15)), 4),
                "latency_p95_ms": _latency(rng, svc["base_latency_ms"] * 1.4, lat_mult),
                "cpu_pct": round(min(100, 35 * rng.uniform(0.7, 1.3) * (lat_mult ** 0.5)), 2),
                "memory_pct": round(min(100, 55 * rng.uniform(0.85, 1.15)), 2),
            }
            units = {"request_rate": "rpm", "error_rate": "ratio",
                     "latency_p95_ms": "ms", "cpu_pct": "%", "memory_pct": "%"}
            for name in METRIC_NAMES:
                yield {
                    "service": svc["name"],
                    "name": name,
                    "value": values[name],
                    "unit": units[name],
                    "timestamp": ts.isoformat(),
                }
            ts += step


def iter_host_metrics(rng: random.Random, start: datetime, end: datetime,
                      now: datetime) -> Iterator[dict]:
    """Per-minute hardware metrics (cpu/memory/disk %) for every server.

    - CPU/memory rise on hosts whose service is in an anomaly window (load).
    - Disk usage trends upward over the window; `disk_pressure` hosts climb
      steeply toward a near-full disk so there's a clear infra anomaly to watch.
    """
    step = timedelta(minutes=1)
    span = max(1.0, (end - start).total_seconds())
    for srv in settings.SERVERS:
        ts = start
        while ts <= end:
            frac = (ts - start).total_seconds() / span  # 0..1 across the window
            scenario = _active_scenario(srv["service"], ts, now)
            load_mult = 1.0 + (0.6 if scenario else 0.0)

            cpu = srv["cpu_base"] * rng.uniform(0.8, 1.2) * load_mult
            mem = srv["mem_base"] * rng.uniform(0.92, 1.08) + (10 if scenario else 0)
            disk_growth = (12.0 if srv.get("disk_pressure") else 5.0) * frac
            disk = srv["disk_base"] + disk_growth + rng.uniform(-1.0, 1.0)

            values = {
                "cpu_usage": round(min(100.0, max(0.0, cpu)), 2),
                "memory_usage": round(min(100.0, max(0.0, mem)), 2),
                "disk_usage": round(min(99.9, max(0.0, disk)), 2),
            }
            for name in settings.HOST_METRIC_NAMES:
                yield {
                    "service": srv["service"],
                    "host": srv["host"],
                    "name": name,
                    "value": values[name],
                    "unit": "%",
                    "timestamp": ts.isoformat(),
                }
            ts += step


def iter_traces(rng: random.Random, start: datetime, end: datetime,
                total_traces: int, now: datetime) -> Iterator[dict]:
    """Trace-like request flows: a root span + one downstream child span."""
    pick = _weighted_service_picker(rng)
    span_seconds = (end - start).total_seconds()
    downstream = {
        "checkout-service": "payment-service",
        "payment-service": "inventory-service",
        "auth-service": "notification-service",
        "inventory-service": "notification-service",
        "recommendation-service": "inventory-service",
        "notification-service": "auth-service",
    }
    for _ in range(total_traces):
        root = pick()
        svc = settings.service(root)
        ts = start + timedelta(seconds=rng.random() * span_seconds)
        trace_id = f"tr-{rng.getrandbits(48):012x}"
        scenario = _active_scenario(root, ts, now)

        is_error = bool(scenario and rng.random() < scenario["error_rate"])
        http = (scenario["error_status"] if is_error else 200)
        dur = _latency(rng, svc["base_latency_ms"],
                       scenario["latency_mult"] if scenario else 1.0)

        root_span_id = f"sp-{rng.getrandbits(32):08x}"
        yield {
            "trace_id": trace_id,
            "span_id": root_span_id,
            "parent_span_id": None,
            "service": root,
            "operation": rng.choice(ENDPOINTS[root]),
            "status": "ERROR" if is_error else "OK",
            "http_status": http,
            "duration_ms": dur,
            "timestamp": ts.isoformat(),
        }
        # one downstream child span
        child = downstream[root]
        csvc = settings.service(child)
        cdur = _latency(rng, csvc["base_latency_ms"])
        yield {
            "trace_id": trace_id,
            "span_id": f"sp-{rng.getrandbits(32):08x}",
            "parent_span_id": root_span_id,
            "service": child,
            "operation": rng.choice(ENDPOINTS[child]),
            "status": "ERROR" if is_error and rng.random() < 0.5 else "OK",
            "http_status": http if is_error else 200,
            "duration_ms": cdur,
            "timestamp": (ts + timedelta(milliseconds=cdur)).isoformat(),
        }


# --------------------------------------------------------------------------- #
# Public API
# --------------------------------------------------------------------------- #
def write_sample_files(
    out_dir: Optional[Path] = None,
    hours: Optional[int] = None,
    logs_per_hour: Optional[int] = None,
    seed: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Dict[str, int]:
    """Stream synthetic telemetry to data/sample_telemetry/*.jsonl.

    Returns a dict of record counts.
    """
    out_dir = Path(out_dir or settings.SAMPLE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    hours = hours or settings.GEN_HOURS
    logs_per_hour = logs_per_hour or settings.GEN_LOGS_PER_HOUR
    seed = settings.GEN_SEED if seed is None else seed
    now = now or utcnow()
    start = now - timedelta(hours=hours)

    total_logs = hours * logs_per_hour
    total_traces = max(1000, logs_per_hour // 5) * hours

    rng = random.Random(seed)
    counts = {"logs": 0, "metrics": 0, "traces": 0}

    with (out_dir / "logs.jsonl").open("w") as f:
        for rec in iter_logs(rng, start, now, total_logs, now):
            f.write(json.dumps(rec) + "\n")
            counts["logs"] += 1

    with (out_dir / "metrics.jsonl").open("w") as f:
        for rec in iter_metrics(rng, start, now, now):
            f.write(json.dumps(rec) + "\n")
            counts["metrics"] += 1
        # Host hardware metrics share the metrics stream (tagged with `host`).
        for rec in iter_host_metrics(rng, start, now, now):
            f.write(json.dumps(rec) + "\n")
            counts["metrics"] += 1

    with (out_dir / "traces.jsonl").open("w") as f:
        for rec in iter_traces(rng, start, now, total_traces, now):
            f.write(json.dumps(rec) + "\n")
            counts["traces"] += 1

    return counts


def generate_in_memory(
    hours: int = 1,
    logs_per_hour: int = 500,
    seed: int = 1337,
    now: Optional[datetime] = None,
) -> Dict[str, List[dict]]:
    """Small in-memory generation, handy for tests."""
    now = now or utcnow()
    start = now - timedelta(hours=hours)
    rng = random.Random(seed)
    return {
        "logs": list(iter_logs(rng, start, now, hours * logs_per_hour, now)),
        "metrics": list(iter_metrics(rng, start, now, now)),
        "host_metrics": list(iter_host_metrics(rng, start, now, now)),
        "traces": list(iter_traces(rng, start, now, hours * (logs_per_hour // 5), now)),
    }
