# Audit Log — Andela Watch

Chronological record of every prompt used to build this project (per project rules:
no manual edits by the user; all logic provided by the assistant).

---

## Turn 1 — 2026-06-13

**Prompt:**

> Lead Architect mode: ON. We are building a Python-based, API-first Intelligent
> Observability & Event Watchdog using a free database and a dashboard.
>
> Rules:
> ● No Manual Edits: You provide all logic and fixes. I will not edit any code.
> ● Audit Log: You must maintain a file named prompts.md. After every turn, update
>   that file (or provide the text block) with the prompt I just used.
> ● Time-Check: Start a timer. Goal is an MVP in 4-6 hours (Max window: 16h). Report
>   'Elapsed Time' at the end of every response. Acknowledge and let's start.

**Action taken:** Defined the architecture (FastAPI + SQLAlchemy 2.0 + SQLite +
Chart.js). Scaffolded the full MVP: config, DB layer, ORM models (Source/Event/
Metric/Alert), Pydantic schemas, the statistical detector (rolling z-score +
event severity + rate spike), CRUD/orchestration, FastAPI app with ingest/read/
alert APIs, a live single-page dashboard, a demo data seeder, a pytest suite,
`run.sh`, and `README.md`.

---

## Turn 2 — 2026-06-13

**Prompt:**

> Now implement the architecture for the selected project: Intelligent
> Observability & Event Watchdog. I would like to be able to select periods of
> observability, like 1hour, 3 hours, 24 hours. Since this is a MVP, 24 hours is
> enough to create a proof of concept. I need to adjust the baseline architecture
> to the following one.
>
> Build the project as a realistic local MVP that represents a scalable
> enterprise architecture. We should not deploy Kafka, Flink, Prometheus,
> Elasticsearch, or Jaeger, but the code and documentation should clearly explain
> how the MVP maps to those production components.
>
> [Full spec: fictional e-commerce microservices (auth, checkout, payment,
> inventory, recommendation, notification); 8 layers — Telemetry Simulation
> (≥10k events/hour, JSONL logs/metrics/traces, normal + anomaly periods),
> Ingestion (FastAPI + Pydantic + SQLite, bulk file ingestion), Storage (tables
> for logs/metrics/traces/alerts/services/anomaly_evaluations), Processing &
> Detection (rolling error rate, error count, latency threshold, z-score spikes,
> service health score), Watchdog & Alerting (Alert Manager, simulated webhook,
> dedup + cooldown + LOW/MEDIUM/HIGH/CRITICAL), Dashboard (Streamlit, reads
> SQLite), API Layer (full endpoint list + OpenAPI), Documentation (README,
> Mermaid diagram, Production Evolution section). Provided the suggested folder
> structure, implementation expectations, and required deterministic anomaly
> scenarios (payment 500 spike, checkout latency degradation, auth failed logins,
> inventory timeouts). Start with the scaffold, then DB models, generator,
> ingestion API, anomaly engine, alert manager, and dashboard; provide run
> instructions and update prompts.md.]

**Action taken:** Re-architected from the v0 baseline into the full layered
platform. Created the modular package (`app/api`, `app/core`, `app/models`,
`app/services`, `app/utils`), 7-table SQLite schema, deterministic telemetry
generator (240k logs / 43k metrics / 96k traces over 24h with 4 injected anomaly
scenarios), bulk ingestion, the 5-rule anomaly engine with window-scaled
thresholds + a z-score peak guard, the Alert Manager (fingerprint dedup +
cooldown + severity + simulated webhook stored in `notifications`), the full
FastAPI surface (health, ingest, services, alerts, evaluate, dashboard summary,
simulated webhook), a Streamlit dashboard with 1h/3h/24h window selection, four
operational scripts, an 18-test suite (all passing), and docs (`README.md`,
`docs/architecture.md` with Mermaid diagrams, `docs/production_evolution.md`).
Fixed a UTC-vs-local epoch bug in time-bucketing that had zeroed all bucketed
series. Verified end-to-end: generate → ingest → evaluate → dashboard all green.

---

## Turn 3 — 2026-06-13

**Prompt:**

> On the application front page, create elements on the front end (dashboard
> service) that make the Observability dashboard more readable.
>
> Include buttons to run the following and update data:
> - scripts/init_db.py
> - scripts/generate_sample_data.py
> - scripts/ingest_sample_data.py
> - scripts/run_anomaly_evaluation.py
>
> - Adjust the charts colors to highlight critical problems, and also include tips
>   that help the user to understand where the problem lies on.
>
> On the API side, adjust the doc to be more intuitive and usable. Right now it is
> hard to understand what each API endpoint is for. It is necessary to add more
> clarification text in each endpoint API documentation.
>
> If you need clarification information don't hesitate to ask

**Action taken:** Dashboard — added sidebar **Data Pipeline** buttons (init /
generate / ingest / evaluate + one-click full pipeline, with inline generation
settings), a green/red **status banner**, severity-colored **Altair charts**
(error-volume red above mean+2σ, error-rate amber→red by threshold, health
red→green scale, interactive latency lines) with tooltips, per-section **tips**,
and a **"Where's the problem?"** panel. Backend — added a window-scoped
`diagnostics` capability to `dashboard_service` (and a `ServiceDiagnostic`
schema + `diagnostics` field on `DashboardSummary`) that identifies each degraded
service's dominant error code from its **peak error bucket** (so a localized
scenario's signature code surfaces instead of accumulated background 500s) and
explains what the detector flagged. API — added `summary`, rich markdown
descriptions, and `response_description` to every endpoint, plus grouped
`openapi_tags` metadata and an expanded app description. Verified: 18 tests pass,
Streamlit AppTest renders (6 buttons, 5 charts, no exceptions), OpenAPI carries
all summaries, diagnostics align across 1h/3h/24h on fresh data.

---

## Turn 4 — 2026-06-13

**Prompt:**

> No[w] that we have a version with more intuitive data. I would like you to check
> the system for the following requisites:
>
> Site Reliability (SRE). Develop a service that parses application or platform
> logs to detect anomalies or "spikes" in errors using AI logic. When thresholds
> are breached, the system must trigger a simulated webhook alert and visualize
> health trends.
>
> ------------
>
> Where is the simulated webhook visualization on the dashboard? we need to have a
> session on the dashboard to analyze and check if and when the webhook was
> triggered.

**Action taken:** Audited the system against the SRE brief (log parsing →
ingestion + generator; anomaly/spike detection → 5-rule engine incl. z-score;
threshold breach → simulated webhook; health-trend visualization → dashboard).
Identified the gap: webhook deliveries were persisted (`notifications` table) and
printed, but neither visualized nor readable via API. Closed it: added a
`NotificationOut` schema + `webhook_notifications`/`webhooks_triggered` fields on
`DashboardSummary`; a `dashboard_service` query for in-window deliveries;
`alert_service.list_notifications`; a new `GET /notifications` endpoint
(filter by `alert_id`/`window`); and a dedicated **📡 Simulated Webhook Alert
Deliveries** dashboard panel — KPIs (count, critical count, last-fired), a
severity-colored timeline of when each fired, a delivery table, and a raw-payload
inspector. Added 2 tests (now 20 passing). Verified via API and Streamlit AppTest
(webhook section + 6 charts render).

---

## Turn 5 — 2026-06-13

**Prompt:**

> regarding the "Observability & Event Watchdog" on the dashboard, instead of only
> display 3 options of time, the user should be able to select different range of
> data, just like the interface at grafana that provide: 30m, 1h, 2h, 3h, 6h, 12h,
> 24h and 7 days.
>
> I miss observability at hardware level (cpu, memory, disk usage). Since we are
> talking of an hipotetical distributed system, the user should be able to select
> the server to observe. This should be a different tab on the dashboard, and show
> the use of cpu, memory and disk among servers. We need to include this data into
> the logs (generated by telemetry generator).

**Action taken:** (1) **Flexible time range** — expanded `SUPPORTED_WINDOWS` to
30m/1h/2h/3h/6h/12h/24h/7d, refined `bucket_seconds_for` for good granularity at
each, and switched the dashboard window control to a Grafana-style dropdown
(window validation already covered the API). (2) **Hardware observability** —
added a 10-server fleet topology to config (each server → service + zone, one
`disk_pressure` server), a `host` column on `logs` and `metrics`, host
hardware-metric generation (`cpu_usage`/`memory_usage`/`disk_usage` per server per
minute, CPU/mem rising under service load, disk trending up — `shared-db-1` toward
~96%) wired into the telemetry generator + ingestion, a `dashboard_service`
`infrastructure_summary` (+`list_hosts`), new `GET /hosts` and
`GET /infrastructure/summary` endpoints, and a dedicated **🖥️ Infrastructure**
dashboard tab (server multiselect, fleet status table, CPU/memory/disk time-series
with warn/critical reference lines, resource-pressure callouts). Logs now also
carry a `host`. Added 6 infrastructure tests (now 26 passing); verified the
Infrastructure tab renders (2 tabs, 9 charts, 10-server multiselect) and the new
APIs return correct data incl. the disk-pressure CRITICAL host.

---

## Turn 6 — 2026-06-13

**Prompt:**

> Please, update the readme.md file with the new requirements.
>
> Make sure to include the usage of the system. Include clear Instructions to a
> user or an AI agent to configure the server side to send logs to be ingested by
> Observability system (Andela Watch). Explain how it works.
>
> On Readme.md the use of the API to send the log data to the observer server is
> essential to "close" the full cicle of ingestion and process of log data. How
> the x-api-key must be sent with the log data.
>
> Regarding security issues, on the dashboard platform, We can also add a login
> page set with a default and "easy" login with login="admin" passcode="123456"
> just to show a secure level to access the system logs.

**Action taken:** (1) **Dashboard login** — added a demo-grade sign-in gate to the
Streamlit app (`require_login()`, defaults `admin` / `123456`, overridable via
`WATCHDOG_DASH_USER` / `WATCHDOG_DASH_PASSWORD`), plus a sidebar "signed in as / Log
out" control; added the two credential settings to config. (2) **README overhaul**
— rewrote the intro/TOC; added a major **"Usage — send your logs to Andela Watch"**
section with an end-to-end sequence diagram, the API-key flow (which endpoints need
`X-API-Key` and how the header travels with every request), the log payload field
reference + error semantics, `curl` (single + batch) examples, a reusable Python
client, a drop-in `logging.Handler`, a machine-readable contract for AI agents, and
host/metric ingestion; added a **"Security & access control"** section (API key +
dashboard login table); marked protected endpoints with 🔑 in the API reference;
updated the dashboard/limitations notes. Verified: 26 tests pass; AppTest confirms
the gate blocks wrong creds (0 tabs + error) and admits admin/123456 (2 tabs + Log
out).

---

## Turn 7 — 2026-06-13

**Prompt:**

> Perfect, now, make changes on the Readme.md. We need to include the prints of the
> systems that are inside the @system_prints folder enumerated by 1 to . The readme
> file needs to work as a deck that highlight the features of the system using the
> prints to illustrate the overview of the system.

**Action taken (between this and the prior turn, also fixed a dashboard bug):**
First fixed a Streamlit "magic" bug — five chart lines written as ternary
*expressions* (`st.altair_chart(x) if ... else st.info(...)`) caused Streamlit to
render the returned `DeltaGenerator`'s docstring onto the page; converted all five
to `if/else` statements (verified 0 stray doc elements, 9 charts). Then turned the
README into a visual **deck**: added a "📸 System tour" section (and TOC entry)
that embeds all 8 screenshots from `system_prints/` (1 login → 2 service overview →
3 latency/alerts → 4 webhook deliveries → 5 infra overview → 6 hardware charts →
7 API docs → 8 ingest/X-API-Key endpoint), each with a feature-highlighting
caption. Verified all 8 image paths resolve to real files.

---

## Turn 8 — 2026-06-14

**Prompt:**

> add the subfolders of @data folder into a .gitignore file and I will provide a
> google docs link to share inside the project. The google docs link must be
> included on the readme file.
>
> the google docs link is at:
> https://drive.google.com/drive/folders/1ZUA6MCVR5aRBWQmhLEv9IHWlo5gJ1Kzt?usp=sharing

**Action taken:** The project is now a git repo at `andela_watch/andela_watch/`
with no `.gitignore`. Created a `.gitignore` ignoring Python artifacts, local
`*.db`, and the **`data/` subfolders** (`data/*/`, `data/sample_telemetry/`) plus
`data/*.db` — verified via `git check-ignore` that `data/sample_telemetry/`,
`data/observability.db`, and `data/.DS_Store` are ignored and no `data/` paths show
as untracked. Added the **Google Drive link** to the README in two places: a new
"📦 Shared data & assets (Google Drive)" callout after Quickstart (notes the data
isn't versioned, shows the expected `data/` layout) and a cross-reference in the
"Generating & ingesting sample telemetry" section.
