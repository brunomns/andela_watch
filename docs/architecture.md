# Architecture

Andela Watch is a layered, API-first observability platform. Each layer is a
self-contained module so it can be reasoned about, tested, and later replaced by
its production-grade counterpart without touching the others.

## Layers

```mermaid
flowchart TB
    subgraph L1["1 · Telemetry Simulation"]
      G["telemetry_generator.py\nseeded RNG · injected anomalies"]
    end
    subgraph L2["2 · Ingestion (FastAPI)"]
      R["routes_ingestion.py\nPydantic validation"]
      ISVC["ingestion_service.py\nchunked bulk insert"]
    end
    subgraph L3["3 · Storage (SQLite via SQLAlchemy)"]
      T1[(services)]
      T2[(logs)]
      T3[(metrics)]
      T4[(traces)]
      T5[(alerts)]
      T6[(anomaly_evaluations)]
      T7[(notifications)]
    end
    subgraph L4["4 · Processing & Detection"]
      A["anomaly_service.py\nerror_rate · error_count\nlatency · z-score · health_score"]
    end
    subgraph L5["5 · Watchdog & Alerting"]
      AL["alert_service.py\nfingerprint dedup · cooldown\nLOW/MEDIUM/HIGH/CRITICAL"]
      W(["/webhook/simulated"])
    end
    subgraph L6["6 · Dashboard (Streamlit)"]
      D["streamlit_app.py\ndashboard_service.py aggregations"]
    end

    G --> R
    R --> ISVC --> T2 & T3 & T4 & T1
    T2 --> A
    A --> T6
    A --> AL
    AL --> T5
    AL --> T7
    AL -.payload.-> W
    T2 & T3 & T5 & T6 --> D
```

## Request → alert sequence

```mermaid
sequenceDiagram
    participant Gen as Generator
    participant API as FastAPI /ingest
    participant DB as SQLite
    participant Det as anomaly_service
    participant AM as alert_service
    participant WH as webhook (sim)
    participant UI as Streamlit

    Gen->>API: logs/metrics/traces (JSONL)
    API->>DB: bulk insert (validated)
    Note over Det: POST /evaluate/anomalies?window=24h
    Det->>DB: aggregate per service / window
    Det->>DB: write anomaly_evaluations
    Det->>AM: raise_alert(rule, severity, ...)
    AM->>DB: dedup by fingerprint + cooldown
    AM->>WH: simulated webhook payload
    AM->>DB: store notification
    UI->>DB: dashboard_service.summary(window)
    DB-->>UI: health, errors, latency, alerts, anomalies
```

## Design decisions

- **Detection decoupled from ingestion.** Ingestion only validates + stores;
  detection is a separate pass. This mirrors a stream processor (Flink) reading
  asynchronously from a buffer (Kafka) — ingestion stays fast and cheap.
- **In-DB bucketing.** Time bucketing uses SQLite epoch division
  (`strftime('%s', ts) / bucket`), keeping aggregation in the database. Maps
  directly to Timescale `time_bucket()`. (Epoch math is UTC-consistent on both
  the SQL and Python sides — see `app/utils/time_utils.to_epoch`.)
- **Window-aware thresholds.** Error-count thresholds are per-hour and scaled by
  window length so a 24h view doesn't flag healthy high-traffic services.
- **Complementary rules.** No single rule is robust across all window sizes;
  `error_rate` (sharp on short windows), `error_spike` (z-score, window-robust),
  and `error_count` (rate-scaled volume) together cover the space.
- **Alert fatigue protection at the manager.** Dedup by `fingerprint =
  hash(service|rule|kind)` plus a cooldown window — equivalent to Alertmanager
  grouping + `repeat_interval`.

## Data model

```mermaid
erDiagram
    services ||--o{ logs : emits
    services ||--o{ metrics : emits
    services ||--o{ traces : emits
    alerts ||--o{ notifications : delivers
    services {
      int id PK
      string name
      string tier
    }
    logs {
      int id PK
      string service
      string level
      int status_code
      float latency_ms
      string trace_id
      datetime timestamp
    }
    metrics {
      int id PK
      string service
      string name
      float value
      datetime timestamp
    }
    traces {
      int id PK
      string trace_id
      string span_id
      string parent_span_id
      string service
      string status
      float duration_ms
      datetime timestamp
    }
    alerts {
      int id PK
      string fingerprint
      string service
      string rule
      string severity
      string status
      int count
      datetime cooldown_until
    }
    anomaly_evaluations {
      int id PK
      string service
      string rule
      string window_label
      float observed_value
      float threshold
      bool is_anomaly
    }
    notifications {
      int id PK
      int alert_id FK
      json payload
      string status
    }
```
