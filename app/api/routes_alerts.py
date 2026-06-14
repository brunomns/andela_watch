"""Alerts, on-demand anomaly evaluation, and the simulated webhook receiver."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import require_api_key
from app.models.schemas import (
    AlertOut,
    AlertStatusUpdate,
    AnomalyEvaluationOut,
    EvaluateRequest,
    EvaluateResult,
    NotificationOut,
    WebhookPayload,
)
from app.services import alert_service, anomaly_service

router = APIRouter(tags=["alerts"])


@router.get("/alerts", response_model=List[AlertOut],
            summary="List alerts",
            response_description="Alerts, most recently seen first.")
def get_alerts(status: Optional[str] = None, severity: Optional[str] = None,
               limit: int = Query(100, le=1000), db: Session = Depends(get_db)):
    """List alerts raised by the watchdog, most recently seen first.

    Filter by `status` (`open` / `acknowledged` / `resolved`) and/or `severity`
    (`LOW` / `MEDIUM` / `HIGH` / `CRITICAL`). Each alert carries its triggering
    `rule`, a dedup `fingerprint`, a `count` of how many times it recurred, and
    the observed `value`/`score`.
    """
    return alert_service.list_alerts(db, status=status, severity=severity, limit=limit)


@router.patch("/alerts/{alert_id}", response_model=AlertOut,
              summary="Update an alert's status",
              response_description="The updated alert.")
def patch_alert(alert_id: int, body: AlertStatusUpdate,
                db: Session = Depends(get_db), _: None = Depends(require_api_key)):
    """Acknowledge or resolve an alert.

    Set `status` to `acknowledged` (someone is on it) or `resolved` (incident
    over). Resolving lets a future occurrence open a fresh alert instead of
    being deduplicated into this one. Returns 404 if the alert does not exist.
    """
    alert = alert_service.update_alert_status(db, alert_id, body.status)
    if alert is None:
        raise HTTPException(status_code=404, detail="Alert not found")
    return alert


@router.get("/notifications", response_model=List[NotificationOut],
            summary="List simulated webhook deliveries",
            response_description="Recorded webhook deliveries, most recent first.")
def get_notifications(alert_id: Optional[int] = None, window: Optional[str] = None,
                      limit: int = Query(100, le=1000),
                      db: Session = Depends(get_db)):
    """List the simulated webhook alerts the watchdog has dispatched.

    Every time an alert fires (a threshold is breached), the Alert Manager
    records a webhook delivery here with the full payload sent. Filter by
    `alert_id` (deliveries for one alert) or `window` (e.g. `1h`, `24h`) to see
    **if and when** the system triggered alerts. This is the audit trail behind
    the dashboard's "Webhook Alert Deliveries" panel.
    """
    return alert_service.list_notifications(db, alert_id=alert_id, window=window,
                                            limit=limit)


@router.post("/evaluate/anomalies", response_model=EvaluateResult,
             summary="Run anomaly detection",
             response_description="Counts of evaluations/anomalies/alerts plus the "
                                  "anomalous findings.")
def evaluate_anomalies(body: EvaluateRequest = EvaluateRequest(),
                       db: Session = Depends(get_db),
                       _: None = Depends(require_api_key)):
    """Run the detection engine over an observation window and raise alerts.

    Provide `{"window": "1h" | "3h" | "24h"}` (defaults to `24h`). For every
    service it evaluates five rules — **error_rate**, **error_count**,
    **latency**, **error_spike** (z-score), and **health_score** — writes an
    `anomaly_evaluations` row per rule, and raises deduplicated alerts for
    anomalous findings. The response includes the anomalous evaluations only.
    """
    result = anomaly_service.evaluate(db, window=body.window)
    result["details"] = [
        AnomalyEvaluationOut.model_validate(e) for e in result["details"]
    ]
    return EvaluateResult(**result)


@router.post("/webhook/simulated",
             summary="Simulated webhook receiver",
             response_description="Acknowledgement echoing the received payload.")
def simulated_webhook(payload: WebhookPayload):
    """Stand-in for a real Slack / PagerDuty / Alertmanager webhook.

    The Alert Manager posts here (and stores the payload in `notifications`)
    whenever an alert fires. This receiver simply acknowledges and echoes the
    payload so the end-to-end alerting loop can be demonstrated locally.
    """
    print(f"[SIMULATED-WEBHOOK RECEIVED] {payload.severity} :: {payload.title}")
    return {"received": True, "echo": payload.model_dump(mode="json")}
