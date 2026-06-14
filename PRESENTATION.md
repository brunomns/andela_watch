---
marp: true
theme: default
paginate: true
title: "Andela Watch — Intelligent Observability & Event Watchdog"
---

<!-- _class: lead -->

# 🛰️ Andela Watch

## Intelligent Observability & Event Watchdog

A local, **API-first** observability platform for a distributed microservice fleet.

*Ingest → Detect → Alert → Visualize*

---

# The Problem

Distributed microservice fleets fail in ways that are hard to see:

- 💥 Error spikes hide in millions of log lines
- 🐌 Latency degrades silently until users complain
- 🖥️ Hardware (disk/CPU/memory) fills up independently of app errors
- 🔔 Alert fatigue — the same incident fires over and over

**Andela Watch turns raw telemetry into plain-English root causes and deduplicated alerts.**

---

# What It Does

1. **Ingests** logs / metrics / traces over HTTP
2. **Detects** health degradation & error spikes with statistical rules
3. **Raises** deduplicated alerts via a simulated webhook
4. **Visualizes** everything in a login-protected Streamlit dashboard

🔭 Grafana-style observation windows (**30m → 7d**)
🖥️ Dedicated hardware view (per-server CPU / memory / disk)

---

# A Realistic Local MVP

Intentionally built to **mirror an enterprise architecture** — without the operational weight.

> We do **not** deploy Kafka, Flink, Prometheus, Elasticsearch, or Jaeger…
> but every component **maps to its production counterpart**.

📄 See `docs/architecture.md` and `docs/production_evolution.md`

---

# The Fleet — 6 Microservices

`auth-service` · `checkout-service` · `payment-service`
`inventory-service` · `recommendation-service` · `notification-service`

The telemetry generator emits:

- **≥10,000 log events / hour**
- Per-minute metrics & trace-like spans
- Normal behavior blended with **deterministic anomaly periods**

---

# Architecture — End to End

```
1 · Telemetry Simulation   → logs / metrics / traces (JSONL)
            │
2 · Ingestion API (FastAPI) → /ingest/logs · /metrics · /traces
            │
3 · Storage (SQLite)        → services · logs · metrics · traces
                              alerts · evaluations · notifications
            │
4 · Processing & Detection  → anomaly_service (rate · count · z-score · health)
            │
5 · Watchdog & Alerting     → alert_service (dedup · cooldown · severity)
                              → simulated webhook
            │
6 · Dashboard (Streamlit)   → health · errors · latency · alerts
```

---

# Layered, Modular Code

```
app/
  api/        FastAPI routers (health, ingestion, alerts, ...)
  core/       config, database engine/session, security
  models/     SQLAlchemy ORM models + Pydantic schemas
  services/   telemetry_generator, ingestion, anomaly, alert, dashboard
  utils/      time-window helpers
```

Clean separation of concerns — easy to reason about and extend.

---

# MVP → Enterprise Mapping

| MVP component | Production counterpart |
|---|---|
| `telemetry_generator.py` | OpenTelemetry SDKs / Collectors |
| FastAPI `/ingest/*` | OTel Collector + **Kafka** |
| `anomaly_service.py` (batch) | **Flink** stream processor |
| `logs` table | **Elasticsearch / OpenSearch** |
| `metrics` table | **Prometheus / TimescaleDB** |
| `traces` table | **Jaeger / Tempo** |
| `alert_service.py` | **Alertmanager / PagerDuty** |
| Streamlit dashboard | **Grafana** |

---

# Anomaly Detection Rules

| Rule | Signal | Anomaly when |
|---|---|---|
| **error_rate** | errors / total | ≥10% MED · 25% HIGH · 40% CRIT |
| **error_count** | volume (per-hr) | ≥80/hr … 500/hr |
| **latency** | avg vs. SLO | avg > SLO |
| **error_spike** | z-score (5m buckets) | z ≥ 3.0 **and** peak ≥ 10 |
| **health_score** | composite 0–100 | < 70 |

**Error** = an `ERROR`-level log **or** a `5xx` status.

---

# Why Multiple Rules?

They are **complementary across windows**:

- 🎯 `error_rate` — sharp on short windows
- 📈 `error_spike` (z-score) — localizes a burst regardless of window length
- 📊 `error_count` (rate-scaled) — guards against sustained volume

One rule alone misses too much. Together they tell the whole story.

---

# Built-in Anomaly Scenarios

Deterministically injected so the dashboard always tells a clear story:

| Service | Scenario | When | Visible in |
|---|---|---|---|
| auth | Failed-login burst (401) | 50–35m ago | 1h, 3h, 24h |
| payment | HTTP 500 spike | 130–90m ago | 3h, 24h |
| inventory | Upstream timeouts (504) | 6h–5h20m ago | 24h |
| checkout | Latency degradation | 10h–8.5h ago | 24h |

🔍 Short window → recent ones · 24h → see them all

---

# The Dashboard — Service Observability

- 🚦 **Status banner** — green nominal / red when CRITICAL
- 📇 **KPI cards** — health, requests, errors, active alerts
- 🧭 **"Where's the problem?"** — plain-English root causes
  *"auth-service — sudden error spike. Most errors are 319× auth failures (HTTP 401)."*
- 📈 Interactive **p95 latency**, **Top Failing Services**, **Active Alerts**

---

# The Dashboard — Infrastructure

- 🖥️ Select one or more **servers** to observe
- 📊 **CPU / memory / disk** time-series with 80% warn / 90% critical lines
- 🟢🟡🔴 Fleet **status table** (OK / WARN / CRITICAL)
- A `shared-db-1` server trends toward a full disk (~97%) —
  **a hardware problem surfaced independently of app errors**

---

# 📡 Alerting Audit Trail

The watchdog's firing record answers: *"did the system alert, and when?"*

- How many webhooks triggered & how many were CRITICAL
- Severity-colored **timeline** of *when* each fired
- Delivery log + **raw-payload inspector**
- Same diagnostics available via `GET /dashboard/summary`

🛡️ Dedup + cooldown prevent alert storms.

---

# Closing the Loop — Send Your Own Logs

The generator is just for the demo. In real use, **your services are the source of truth.**

```bash
curl -X POST http://localhost:8000/ingest/logs \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: your-secret' \
  -d '{"service":"payment-service","level":"ERROR",
       "status_code":500,"endpoint":"/charge",
       "message":"payment gateway returned 500"}'
```

Accepts **one object or a batch array**. Returns `{accepted, kind, alerts_triggered}`.

---

# Drop-in Python Logging Handler

```python
class AndelaWatchHandler(logging.Handler):
    """Forward Python log records to Andela Watch."""
    def emit(self, record):
        try:
            self.session.post(f"{self.url}/ingest/logs", timeout=2, json={
                "service": self.service,
                "level": record.levelname,
                "message": record.getMessage(),
            })
        except Exception:
            pass  # telemetry must never break the application
```

> Production tip: buffer & send on a background thread — exactly what an OTel Collector does.

---

# Security & Access Control

Two independent layers — demo-grade, but the right shape:

**1 · API (server-to-server) — shared API key**
Set `WATCHDOG_API_KEY` → require `X-API-Key` on all write/ingest endpoints.
Read-only endpoints stay open.

**2 · Dashboard (human) — login gate**
Sign in with **admin / 123456** (override via env vars). Log out in the sidebar.

⚠️ Presentation-grade. Production → TLS, OAuth/OIDC, per-source keys, RBAC.

---

# Quickstart

```bash
python -m pip install -r requirements.txt

# One-shot demo pipeline
python scripts/init_db.py
python scripts/generate_sample_data.py   # 24h @ 10k logs/hr (~240k)
python scripts/ingest_sample_data.py
python scripts/run_anomaly_evaluation.py # evaluates 1h, 3h, 24h

# Then, in two terminals:
uvicorn app.main:app --reload            # API → :8000/docs
streamlit run dashboard/streamlit_app.py # UI  → :8501
```

Data anchors to "now" — the dashboard always shows fresh data.

---

# Data Model — 7 Tables

- **services** — registry (name, tier)
- **logs** — level, message, endpoint, status_code, latency_ms, trace_id…
- **metrics** — service/host metrics (cpu/memory/disk carry a `host`)
- **traces** — span_id, parent, operation, duration_ms…
- **alerts** — fingerprint, rule, severity, status, cooldown_until…
- **anomaly_evaluations** — rule, window, observed/threshold, is_anomaly
- **notifications** — simulated webhook deliveries

---

# Tested & Trustworthy

```bash
python -m pytest -q
```

Coverage spans:

- ✅ **Ingestion** — single / batch / file
- ✅ **Detection** — error rate, z-score spike, healthy baseline
- ✅ **Alerting** — dedup, cooldown, severity escalation, webhook

---

# Limitations & Future Work

**Today (by design):**
- Batch, on-demand detection (not streaming)
- Single-node SQLite · statistical rules only · simulated webhook · demo auth

**Roadmap:**
- ⏱️ Continuous evaluation worker
- 📐 EWMA / Holt-Winters detectors
- 🔕 Alert flap-suppression
- 🔑 Per-source API keys · retention / downsampling

---

<!-- _class: lead -->

# Thank You 🛰️

**Andela Watch** — turn raw telemetry into answers.

*Ingest → Detect → Alert → Visualize*

📄 `README.md` · `docs/architecture.md` · `docs/production_evolution.md`
🔌 API docs: `http://localhost:8000/docs`
