"""Dashboard Layer (UI) — Streamlit.

Reads directly from SQLite via the dashboard_service aggregations. Two tabs:
  * 🛰️ Service Observability — health, errors, latency, alerts, anomalies, webhooks.
  * 🖥️ Infrastructure — per-server CPU / memory / disk usage.

Shared sidebar controls: a Grafana-style observation-window selector
(30m … 7d) and data-pipeline buttons (init / generate / ingest / evaluate).

Run:
    streamlit run dashboard/streamlit_app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import altair as alt
import pandas as pd
import streamlit as st

from app.core.config import settings
from app.core.database import SessionLocal, init_db
from app.services import dashboard_service
from app.services.anomaly_service import evaluate
from app.services.ingestion_service import ingest_sample_data
from app.services.telemetry_generator import write_sample_files

st.set_page_config(page_title="Andela Watch", page_icon="🛰️", layout="wide")


# --------------------------------------------------------------------------- #
# Login gate (demo-grade access control)
# --------------------------------------------------------------------------- #
def require_login() -> None:
    """Block the dashboard behind a simple username/passcode form.

    Demo credentials default to admin / 123456 (override with the
    WATCHDOG_DASH_USER / WATCHDOG_DASH_PASSWORD env vars). This is a
    presentation-grade gate to show access control — not production auth.
    """
    if st.session_state.get("authenticated"):
        return

    st.title("🔒 Andela Watch")
    st.caption("Observability & Event Watchdog — sign in to view system logs.")
    with st.form("login_form"):
        username = st.text_input("Username")
        passcode = st.text_input("Passcode", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if (username == settings.DASH_USER
                and passcode == settings.DASH_PASSWORD):
            st.session_state["authenticated"] = True
            st.session_state["user"] = username
            st.rerun()
        else:
            st.error("Invalid credentials. Please try again.")
    st.info("Demo credentials → username **admin**, passcode **123456**.")
    st.stop()


require_login()

SEV_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEV_COLORS = {"LOW": "#3fb950", "MEDIUM": "#d29922", "HIGH": "#f85149",
              "CRITICAL": "#ff5c8a"}
OK_BLUE = "#388bfd"
ALERT_RED = "#f85149"
WARN_AMBER = "#f0a020"


# --------------------------------------------------------------------------- #
# Data loading
# --------------------------------------------------------------------------- #
def load_summary(window: str) -> dict:
    init_db(seed_services=True)
    db = SessionLocal()
    try:
        s = dashboard_service.summary(db, window=window)
        return {
            "window": s["window"],
            "generated_at": s["generated_at"],
            "system_health_score": s["system_health_score"],
            "total_requests": s["total_requests"],
            "total_errors": s["total_errors"],
            "active_alerts": s["active_alerts"],
            "error_volume_over_time": s["error_volume_over_time"],
            "error_rate_by_service": s["error_rate_by_service"],
            "latency_trends": s["latency_trends"],
            "top_failing_services": s["top_failing_services"],
            "severity_distribution": s["severity_distribution"],
            "diagnostics": s["diagnostics"],
            "webhooks_triggered": s["webhooks_triggered"],
            "webhook_notifications": [_notif_row(n) for n in s["webhook_notifications"]],
            "active_alert_list": [_alert_row(a) for a in s["active_alert_list"]],
            "alert_history": [_alert_row(a) for a in s["alert_history"]],
            "anomaly_timeline": [_anomaly_row(e) for e in s["anomaly_timeline"]],
        }
    finally:
        db.close()


def load_infra(window: str, hosts: list) -> dict:
    init_db(seed_services=True)
    db = SessionLocal()
    try:
        return dashboard_service.infrastructure_summary(
            db, window=window, hosts=hosts or None)
    finally:
        db.close()


def _alert_row(a) -> dict:
    return {
        "id": a.id, "severity": a.severity, "service": a.service, "rule": a.rule,
        "title": a.title, "status": a.status, "count": a.count,
        "value": round(a.value, 3) if a.value is not None else None,
        "last_seen": a.last_seen,
    }


def _anomaly_row(e) -> dict:
    return {
        "service": e.service, "rule": e.rule, "severity": e.severity,
        "observed": e.observed_value, "threshold": e.threshold, "score": e.score,
        "window_end": e.window_end,
    }


def _notif_row(n) -> dict:
    p = n.payload or {}
    return {
        "fired_at": n.created_at, "severity": p.get("severity"),
        "service": p.get("service"), "rule": p.get("rule"), "title": p.get("title"),
        "value": p.get("value"), "count": p.get("count"), "status": n.status,
        "channel": n.channel, "url": n.url, "alert_id": n.alert_id, "payload": p,
    }


def health_emoji(score: float) -> str:
    if score >= 90:
        return "🟢"
    if score >= 70:
        return "🟡"
    if score >= 50:
        return "🟠"
    return "🔴"


def er_severity(rate: float) -> str:
    if rate >= settings.ERROR_RATE_CRIT:
        return "CRITICAL"
    if rate >= settings.ERROR_RATE_HIGH:
        return "HIGH"
    if rate >= settings.ERROR_RATE_WARN:
        return "MEDIUM"
    return "LOW"


# --------------------------------------------------------------------------- #
# Charts — service observability
# --------------------------------------------------------------------------- #
def chart_error_volume(points: list):
    df = pd.DataFrame(points)
    if df.empty or df["value"].sum() == 0:
        return None
    vals = df["value"]
    thr = float(vals.mean() + 2 * vals.std(ddof=0)) if len(vals) > 1 else float(vals.max())
    bars = alt.Chart(df).mark_bar().encode(
        x=alt.X("timestamp:T", title=None),
        y=alt.Y("value:Q", title="Errors per bucket"),
        color=alt.condition(alt.datum.value > thr,
                            alt.value(ALERT_RED), alt.value(OK_BLUE)),
        tooltip=[alt.Tooltip("timestamp:T", title="Time"),
                 alt.Tooltip("value:Q", title="Errors")],
    )
    rule = alt.Chart(pd.DataFrame({"y": [thr]})).mark_rule(
        color=WARN_AMBER, strokeDash=[5, 4]).encode(
        y="y:Q", tooltip=[alt.Tooltip("y:Q", title="Elevated threshold")])
    return (bars + rule).properties(height=260, width="container")


def chart_severity(sev: dict):
    df = pd.DataFrame({"severity": SEV_ORDER,
                       "count": [sev.get(k, 0) for k in SEV_ORDER]})
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("severity:N", sort=SEV_ORDER, title=None),
        y=alt.Y("count:Q", title="Active alerts"),
        color=alt.Color("severity:N",
                        scale=alt.Scale(domain=SEV_ORDER,
                                        range=[SEV_COLORS[s] for s in SEV_ORDER]),
                        legend=None),
        tooltip=["severity", "count"],
    ).properties(height=260, width="container")


def chart_error_rate(rows: list):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    df["error_rate_pct"] = (df["error_rate"] * 100).round(2)
    df["sev"] = df["error_rate"].apply(er_severity)
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("service:N", sort="-y", title=None),
        y=alt.Y("error_rate_pct:Q", title="Error rate (%)"),
        color=alt.Color("sev:N",
                        scale=alt.Scale(domain=SEV_ORDER,
                                        range=[SEV_COLORS[s] for s in SEV_ORDER]),
                        legend=alt.Legend(title="Severity")),
        tooltip=[alt.Tooltip("service:N", title="Service"),
                 alt.Tooltip("error_rate_pct:Q", title="Error rate %"),
                 alt.Tooltip("error_count:Q", title="Errors"),
                 alt.Tooltip("request_count:Q", title="Requests")],
    ).properties(height=260, width="container")


def chart_health(rows: list):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    return alt.Chart(df).mark_bar().encode(
        x=alt.X("service:N", sort="y", title=None),
        y=alt.Y("health_score:Q", title="Health (0-100)",
                scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("health_score:Q",
                        scale=alt.Scale(scheme="redyellowgreen", domain=[0, 100]),
                        legend=None),
        tooltip=[alt.Tooltip("service:N", title="Service"),
                 alt.Tooltip("health_score:Q", title="Health"),
                 alt.Tooltip("avg_latency_ms:Q", title="Avg latency ms")],
    ).properties(height=260, width="container")


def chart_latency(latency_trends: dict):
    frames = []
    for svc, pts in latency_trends.items():
        if not pts:
            continue
        d = pd.DataFrame(pts)
        d["service"] = svc
        frames.append(d)
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    return alt.Chart(df).mark_line().encode(
        x=alt.X("timestamp:T", title=None),
        y=alt.Y("value:Q", title="p95 latency (ms)"),
        color=alt.Color("service:N", legend=alt.Legend(title="Service")),
        tooltip=[alt.Tooltip("service:N", title="Service"),
                 alt.Tooltip("timestamp:T", title="Time"),
                 alt.Tooltip("value:Q", title="p95 ms")],
    ).properties(height=300, width="container").interactive()


def chart_webhook_timeline(rows: list):
    df = pd.DataFrame(rows)
    if df.empty:
        return None
    return alt.Chart(df).mark_circle(size=140, opacity=0.85).encode(
        x=alt.X("fired_at:T", title="Time fired"),
        y=alt.Y("service:N", title=None),
        color=alt.Color("severity:N",
                        scale=alt.Scale(domain=SEV_ORDER,
                                        range=[SEV_COLORS[s] for s in SEV_ORDER]),
                        legend=alt.Legend(title="Severity")),
        tooltip=[alt.Tooltip("fired_at:T", title="Fired at"),
                 alt.Tooltip("severity:N", title="Severity"),
                 alt.Tooltip("service:N", title="Service"),
                 alt.Tooltip("rule:N", title="Rule"),
                 alt.Tooltip("title:N", title="Alert"),
                 alt.Tooltip("count:Q", title="Occurrences")],
    ).properties(height=220, width="container").interactive()


# --------------------------------------------------------------------------- #
# Charts — infrastructure
# --------------------------------------------------------------------------- #
def _infra_long(hosts: list, metric: str) -> pd.DataFrame:
    recs = []
    for h in hosts:
        for p in h["series"]:
            if p.get(metric) is not None:
                recs.append({"timestamp": p["timestamp"], "host": h["host"],
                             "value": p[metric]})
    return pd.DataFrame(recs)


def chart_infra(hosts: list, metric: str, refs=None):
    df = _infra_long(hosts, metric)
    if df.empty:
        return None
    line = alt.Chart(df).mark_line().encode(
        x=alt.X("timestamp:T", title=None),
        y=alt.Y("value:Q", title="% used", scale=alt.Scale(domain=[0, 100])),
        color=alt.Color("host:N", legend=alt.Legend(title="Server")),
        tooltip=[alt.Tooltip("host:N", title="Server"),
                 alt.Tooltip("timestamp:T", title="Time"),
                 alt.Tooltip("value:Q", title="% used")],
    )
    layers = [line]
    for level, color in (refs or []):
        layers.append(alt.Chart(pd.DataFrame({"y": [level]})).mark_rule(
            color=color, strokeDash=[5, 4]).encode(
            y="y:Q", tooltip=[alt.Tooltip("y:Q", title="Threshold")]))
    return alt.layer(*layers).properties(height=260, width="container").interactive()


# --------------------------------------------------------------------------- #
# Sidebar — window + data pipeline controls (shared across tabs)
# --------------------------------------------------------------------------- #
st.sidebar.title("🛰️ Andela Watch")
st.sidebar.caption("Observability & Event Watchdog")

_who = st.session_state.get("user", "?")
_lo1, _lo2 = st.sidebar.columns([2, 1])
_lo1.caption(f"👤 Signed in as **{_who}**")
if _lo2.button("Log out"):
    st.session_state.clear()
    st.rerun()

window = st.sidebar.selectbox(
    "Observation window", settings.SUPPORTED_WINDOWS,
    index=settings.SUPPORTED_WINDOWS.index(settings.DEFAULT_WINDOW),
    help="Grafana-style range: filters every panel to the last 30m … 7d.")
if st.sidebar.button("🔄 Refresh data"):
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("⚙️ Data Pipeline")
st.sidebar.caption("Run the same steps as the CLI scripts, then refresh the views.")

with st.sidebar.expander("Generation settings", expanded=False):
    gen_hours = st.number_input("Hours", 1, 168, settings.GEN_HOURS,
                                help="Up to 168 (7 days). Larger = slower to ingest.")
    gen_lph = st.number_input("Logs / hour", 100, 50000, settings.GEN_LOGS_PER_HOUR,
                              step=100)
    gen_seed = st.number_input("Seed", 0, 999999, settings.GEN_SEED)


def _run_init():
    init_db(seed_services=True)


def _run_generate():
    return write_sample_files(hours=int(gen_hours), logs_per_hour=int(gen_lph),
                              seed=int(gen_seed))


def _run_ingest():
    db = SessionLocal()
    try:
        return ingest_sample_data(db)
    finally:
        db.close()


def _run_evaluate():
    db = SessionLocal()
    try:
        return [evaluate(db, w) for w in settings.SUPPORTED_WINDOWS]
    finally:
        db.close()


if st.sidebar.button("1 · init_db", help="scripts/init_db.py — create tables + register services"):
    with st.spinner("Initializing database..."):
        _run_init()
    st.sidebar.success("Database initialized.")

if st.sidebar.button("2 · generate_sample_data", help="scripts/generate_sample_data.py"):
    with st.spinner(f"Generating {gen_hours}h @ {gen_lph}/hr..."):
        counts = _run_generate()
    st.sidebar.success(f"Generated {counts['logs']:,} logs · "
                       f"{counts['metrics']:,} metrics · {counts['traces']:,} traces.")

if st.sidebar.button("3 · ingest_sample_data", help="scripts/ingest_sample_data.py"):
    with st.spinner("Ingesting telemetry into SQLite..."):
        res = _run_ingest()
    st.sidebar.success(f"Ingested {res['logs']:,} logs · "
                       f"{res['metrics']:,} metrics · {res['traces']:,} traces.")

if st.sidebar.button("4 · run_anomaly_evaluation", help="scripts/run_anomaly_evaluation.py"):
    with st.spinner("Running anomaly detection..."):
        results = _run_evaluate()
    fired = sum(r["alerts_triggered"] for r in results)
    anoms = sum(r["anomalies"] for r in results)
    st.sidebar.success(f"Found {anoms} anomalies · fired {fired} alerts.")

st.sidebar.markdown("")
if st.sidebar.button("▶ Run full pipeline", type="primary",
                     help="generate → ingest → evaluate, end to end"):
    with st.spinner("Running full pipeline (generate → ingest → evaluate)..."):
        _run_init()
        c = _run_generate()
        _run_ingest()
        ev = _run_evaluate()
    st.sidebar.success(
        f"Pipeline done: {c['logs']:,} logs ingested, "
        f"{sum(x['alerts_triggered'] for x in ev)} alerts fired. Views updated.")

# --------------------------------------------------------------------------- #
# Tabs
# --------------------------------------------------------------------------- #
tab_services, tab_infra = st.tabs(["🛰️ Service Observability", "🖥️ Infrastructure"])

# =========================== SERVICE OBSERVABILITY ========================== #
with tab_services:
    data = load_summary(window)

    st.title("System Overview")
    st.caption(f"Window: **{window}**  ·  generated "
               f"{data['generated_at']:%Y-%m-%d %H:%M:%S} UTC")

    sev_dist = data["severity_distribution"]
    crit = sev_dist.get("CRITICAL", 0)
    high = sev_dist.get("HIGH", 0)
    if data["total_requests"] == 0:
        st.warning("📭 No telemetry in this window. Use the **Data Pipeline** "
                   "controls in the sidebar to generate and ingest data.")
    elif crit:
        st.error(f"🔴 **{crit} CRITICAL** and {high} HIGH active alert(s). "
                 "Check **Where's the problem?** below.")
    elif high:
        st.warning(f"🟠 {high} HIGH-severity active alert(s) need attention.")
    else:
        st.success("🟢 All systems nominal — no high-severity alerts in this window.")

    c1, c2, c3, c4 = st.columns(4)
    hs = data["system_health_score"]
    c1.metric("System Health", f"{health_emoji(hs)} {hs}/100")
    c2.metric("Total Requests", f"{data['total_requests']:,}")
    err_pct = (data["total_errors"] / data["total_requests"] * 100) \
        if data["total_requests"] else 0
    c3.metric("Total Errors", f"{data['total_errors']:,}",
              f"{err_pct:.1f}% of traffic", delta_color="inverse")
    c4.metric("Active Alerts", data["active_alerts"],
              f"{crit} critical" if crit else "none critical", delta_color="inverse")

    st.subheader("🔎 Where's the problem?")
    st.caption("Auto-generated, window-scoped hints from the worst services.")
    diags = data["diagnostics"]
    if not diags:
        st.success("No degraded services detected in this window. 🟢")
    else:
        for d in diags:
            msg = d["hint"]
            if d.get("sample_message"):
                msg += f"  \n_Example log:_ “{d['sample_message']}”"
            if d["severity"] in ("CRITICAL", "HIGH"):
                st.error(f"🔴 **[{d['severity']}]** {msg}")
            elif d["severity"] == "MEDIUM":
                st.warning(f"🟠 **[{d['severity']}]** {msg}")
            else:
                st.info(f"ℹ️ {msg}")

    left, right = st.columns([2, 1])
    with left:
        st.subheader("Error Volume Over Time")
        st.caption("Red bars exceed the elevated threshold (dashed = mean + 2σ).")
        ch = chart_error_volume(data["error_volume_over_time"])
        if ch is not None:
            st.altair_chart(ch)
        else:
            st.info("No errors in this window. 🟢")
    with right:
        st.subheader("Alert Severity")
        st.caption("Active alerts by severity. Pink = CRITICAL.")
        if sum(sev_dist.values()) > 0:
            st.altair_chart(chart_severity(sev_dist))
        else:
            st.success("No active alerts. 🟢")

    st.subheader("Error Rate & Health by Service")
    st.caption("Bars turn amber → red as error rate crosses 10% / 25% / 40%. "
               "Health bars go red as the score drops.")
    col_a, col_b = st.columns(2)
    with col_a:
        erc = chart_error_rate(data["error_rate_by_service"])
        if erc is not None:
            st.altair_chart(erc)
    with col_b:
        hc = chart_health(data["error_rate_by_service"])
        if hc is not None:
            st.altair_chart(hc)

    with st.expander("Per-service detail table"):
        svc_df = pd.DataFrame(data["error_rate_by_service"])
        if not svc_df.empty:
            svc_df = svc_df.sort_values("error_count", ascending=False).copy()
            svc_df["error_rate"] = (svc_df["error_rate"] * 100).round(2).astype(str) + "%"
            st.dataframe(svc_df, width="stretch", hide_index=True)

    st.subheader("Latency Trends (p95, ms)")
    st.caption("Watch for one service climbing above the others. Drag to zoom.")
    lc = chart_latency(data["latency_trends"])
    if lc is not None:
        st.altair_chart(lc)
    else:
        st.info("No latency metrics in this window.")

    st.subheader("Top Failing Services")
    top = pd.DataFrame(data["top_failing_services"])
    if not top.empty:
        st.dataframe(top[["service", "error_count", "error_rate", "health_score",
                          "avg_latency_ms"]], width="stretch", hide_index=True)
    else:
        st.success("No failing services in this window. 🟢")

    st.subheader("Active Alerts")
    aa = pd.DataFrame(data["active_alert_list"])
    if not aa.empty:
        st.dataframe(aa, width="stretch", hide_index=True)
    else:
        st.success("No active alerts. 🟢")

    with st.expander("Alert History (recent 50)"):
        ah = pd.DataFrame(data["alert_history"])
        st.dataframe(ah if not ah.empty else pd.DataFrame([{"info": "no alerts yet"}]),
                     width="stretch", hide_index=True)

    st.markdown("---")
    st.subheader("📡 Simulated Webhook Alert Deliveries")
    st.caption("Audit trail of the watchdog firing: every breached threshold POSTs "
               "a simulated webhook recorded here — see **if and when** alerts "
               "triggered, and inspect each payload.")
    notifs = data["webhook_notifications"]
    wk1, wk2, wk3 = st.columns(3)
    wk1.metric("Webhooks triggered", data["webhooks_triggered"],
               help=f"Within the selected {window} window.")
    if notifs:
        n_crit = sum(1 for n in notifs if n["severity"] == "CRITICAL")
        last_fired = max(n["fired_at"] for n in notifs)
        wk2.metric("Critical webhooks", n_crit, delta_color="inverse")
        wk3.metric("Last fired", f"{last_fired:%H:%M:%S} UTC")
    else:
        wk2.metric("Critical webhooks", 0)
        wk3.metric("Last fired", "—")

    if not notifs:
        st.info("No webhooks triggered in this window. Run "
                "**4 · run_anomaly_evaluation** on data with anomalies.")
    else:
        ch = chart_webhook_timeline(notifs)
        if ch is not None:
            st.caption("When each webhook fired (colored by severity):")
            st.altair_chart(ch)
        nf_df = pd.DataFrame(notifs)
        st.dataframe(
            nf_df[["fired_at", "severity", "service", "rule", "title", "count",
                   "status", "channel", "url"]], width="stretch", hide_index=True)
        with st.expander("🔬 Inspect raw webhook payloads (what was POSTed)"):
            for n in notifs[:25]:
                st.markdown(f"**{n['severity']} · {n['service']} · "
                            f"{n['fired_at']:%Y-%m-%d %H:%M:%S} UTC** → `{n['url']}`")
                st.json(n["payload"])

    st.subheader("Anomaly Timeline")
    at = pd.DataFrame(data["anomaly_timeline"])
    if not at.empty:
        st.dataframe(at.sort_values("window_end", ascending=False),
                     width="stretch", hide_index=True)
    else:
        st.info("No anomalies recorded. Run **4 · run_anomaly_evaluation** (sidebar).")

# ============================== INFRASTRUCTURE ============================= #
with tab_infra:
    st.title("Infrastructure / Hardware")
    st.caption(f"Per-server CPU, memory & disk usage · window **{window}**. "
               "Disk ≥ 80% warns, ≥ 90% is critical.")

    all_hosts = settings.server_names()
    selected = st.multiselect("Servers to observe", all_hosts, default=all_hosts,
                              help="Pick one or more servers to focus the charts.")
    infra = load_infra(window, selected)
    hosts = infra["hosts"]

    m1, m2, m3 = st.columns(3)
    m1.metric("Servers observed", infra["servers_total"])
    m2.metric("Critical", infra["servers_critical"], delta_color="inverse")
    m3.metric("Warning", infra["servers_warning"], delta_color="inverse")

    if not hosts or all(h["cpu_latest"] is None for h in hosts):
        st.info("No hardware metrics in this window. Run the **Data Pipeline** "
                "(sidebar) to generate and ingest host telemetry.")
    else:
        # Resource-pressure callouts.
        for h in hosts:
            if h["status"] == "CRITICAL":
                st.error(f"🔴 **{h['host']}** ({h['service']}, {h['zone']}) — "
                         f"disk {h['disk_latest']}%, cpu {h['cpu_latest']}%, "
                         f"mem {h['memory_latest']}%")
            elif h["status"] == "WARN":
                st.warning(f"🟠 **{h['host']}** ({h['service']}, {h['zone']}) — "
                           f"disk {h['disk_latest']}%, cpu {h['cpu_latest']}%, "
                           f"mem {h['memory_latest']}%")

        st.subheader("Fleet status (latest readings)")
        fleet = pd.DataFrame([{
            "server": h["host"], "service": h["service"], "zone": h["zone"],
            "status": h["status"],
            "cpu %": h["cpu_latest"], "memory %": h["memory_latest"],
            "disk %": h["disk_latest"],
            "disk peak %": h["disk_max"],
        } for h in hosts])
        st.dataframe(fleet, width="stretch", hide_index=True)

        st.subheader("CPU usage (%)")
        cpu_ch = chart_infra(hosts, "cpu_usage", refs=[(75, WARN_AMBER), (90, ALERT_RED)])
        if cpu_ch is not None:
            st.altair_chart(cpu_ch)
        else:
            st.info("No CPU data.")

        st.subheader("Memory usage (%)")
        mem_ch = chart_infra(hosts, "memory_usage",
                             refs=[(85, WARN_AMBER), (92, ALERT_RED)])
        if mem_ch is not None:
            st.altair_chart(mem_ch)
        else:
            st.info("No memory data.")

        st.subheader("Disk usage (%)")
        st.caption("Dashed lines mark the 80% warning and 90% critical thresholds.")
        disk_ch = chart_infra(hosts, "disk_usage",
                              refs=[(settings.DISK_USAGE_WARN, WARN_AMBER),
                                    (settings.DISK_USAGE_CRIT, ALERT_RED)])
        if disk_ch is not None:
            st.altair_chart(disk_ch)
        else:
            st.info("No disk data.")
