"""Watchdog & Alerting Layer tests: dedup, cooldown, severity, webhook."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import func, select

from app.core.config import settings
from app.models.database_models import Alert, Notification
from app.services import alert_service
from app.utils.time_utils import utcnow


def _raise(db, severity="MEDIUM", now=None):
    return alert_service.raise_alert(
        db, service="payment-service", rule="error_rate", kind="rolling_error_rate",
        severity=severity, title="High error rate", description="d",
        value=0.3, score=0.3, now=now)


def test_new_alert_fires_and_notifies(db):
    alert, fired = _raise(db, now=utcnow())
    assert fired is True
    assert alert.count == 1
    n = db.scalar(select(func.count(Notification.id)).where(
        Notification.alert_id == alert.id))
    assert n == 1


def test_dedup_within_cooldown_suppresses(db):
    now = utcnow()
    a1, f1 = _raise(db, now=now)
    a2, f2 = _raise(db, now=now + timedelta(seconds=5))  # within cooldown
    assert f1 is True and f2 is False
    assert a1.id == a2.id
    assert a2.count == 2
    # Only one alert row, only one notification.
    assert db.scalar(select(func.count(Alert.id))) == 1
    assert db.scalar(select(func.count(Notification.id))) == 1


def test_cooldown_elapsed_refires(db):
    now = utcnow()
    _raise(db, now=now)
    after = now + timedelta(seconds=settings.ALERT_COOLDOWN_SEC + 1)
    alert, fired = _raise(db, now=after)
    assert fired is True
    assert alert.count == 2
    assert db.scalar(select(func.count(Notification.id))) == 2


def test_severity_escalates(db):
    now = utcnow()
    a1, _ = _raise(db, severity="LOW", now=now)
    a2, _ = _raise(db, severity="CRITICAL", now=now + timedelta(seconds=2))
    assert a2.severity == "CRITICAL"


def test_status_update_and_listing(db, client):
    alert, _ = _raise(db, now=utcnow())
    r = client.patch(f"/alerts/{alert.id}", json={"status": "resolved"})
    assert r.status_code == 200 and r.json()["status"] == "resolved"
    assert all(a["id"] != alert.id for a in client.get("/alerts?status=open").json())


def test_resolved_alert_does_not_dedup(db):
    """A resolved alert should not absorb a new occurrence; a fresh alert opens."""
    now = utcnow()
    a1, _ = _raise(db, now=now)
    alert_service.update_alert_status(db, a1.id, "resolved")
    a2, fired = _raise(db, now=now + timedelta(seconds=5))
    assert a2.id != a1.id
    assert fired is True
    assert db.scalar(select(func.count(Alert.id))) == 2


def test_notifications_recorded_and_listed(db, client):
    a1, _ = _raise(db, now=utcnow())
    # New alert -> exactly one webhook delivery recorded.
    notifs = alert_service.list_notifications(db, alert_id=a1.id)
    assert len(notifs) == 1
    assert notifs[0].payload["service"] == "payment-service"
    assert notifs[0].status == "sent"

    # Exposed via the API.
    r = client.get("/notifications")
    assert r.status_code == 200
    body = r.json()
    assert len(body) == 1
    assert body[0]["alert_id"] == a1.id
    assert body[0]["payload"]["severity"] == "MEDIUM"


def test_dashboard_summary_includes_webhooks(db, client):
    _raise(db, now=utcnow())
    s = client.get("/dashboard/summary?window=24h").json()
    assert s["webhooks_triggered"] >= 1
    assert len(s["webhook_notifications"]) >= 1
    assert s["webhook_notifications"][0]["channel"] == "webhook"


def test_simulated_webhook_endpoint(client):
    r = client.post("/webhook/simulated", json={
        "alert_id": 1, "service": "payment-service", "severity": "HIGH",
        "title": "test"})
    assert r.status_code == 200
    body = r.json()
    assert body["received"] is True
    assert body["echo"]["service"] == "payment-service"
