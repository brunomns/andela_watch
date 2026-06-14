"""Watchdog & Alerting Layer — the Alert Manager.

Responsibilities:
  * Create alerts from anomaly findings.
  * Alert-fatigue protection: deduplication by fingerprint + cooldown window.
  * Severity levels: LOW / MEDIUM / HIGH / CRITICAL.
  * Simulated webhook delivery (stored in `notifications` + printed to stdout).

Production mapping: Alertmanager / PagerDuty. Dedup+cooldown here mirror
Alertmanager's grouping, inhibition, and repeat_interval.
"""
from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import List, Optional, Tuple

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.database_models import Alert, Notification
from app.utils.time_utils import parse_window, utcnow

SEVERITY_ORDER = {"LOW": 0, "MEDIUM": 1, "HIGH": 2, "CRITICAL": 3}


def max_severity(a: str, b: str) -> str:
    return a if SEVERITY_ORDER.get(a, 0) >= SEVERITY_ORDER.get(b, 0) else b


def fingerprint(service: Optional[str], rule: str, kind: str) -> str:
    raw = f"{service or '-'}|{rule}|{kind}"
    return hashlib.sha1(raw.encode()).hexdigest()[:16]


def raise_alert(
    db: Session,
    *,
    service: Optional[str],
    rule: str,
    kind: str,
    severity: str,
    title: str,
    description: str = "",
    value: Optional[float] = None,
    score: Optional[float] = None,
    context: Optional[dict] = None,
    now=None,
) -> Tuple[Alert, bool]:
    """Create or dedup an alert. Returns (alert, fired).

    `fired` is True when a notification was actually dispatched (i.e. a brand-new
    alert, or an existing one whose cooldown has elapsed). False = suppressed by
    the cooldown window (fatigue protection).
    """
    now = now or utcnow()
    fp = fingerprint(service, rule, kind)

    existing = db.scalar(
        select(Alert)
        .where(Alert.fingerprint == fp, Alert.status != "resolved")
        .order_by(Alert.created_at.desc())
    )

    if existing is not None:
        existing.count += 1
        existing.last_seen = now
        existing.value = value
        existing.score = score
        existing.severity = max_severity(existing.severity, severity)
        existing.description = description or existing.description
        if context:
            existing.context = context

        suppressed = existing.cooldown_until is not None and now < existing.cooldown_until
        if suppressed:
            db.commit()
            return existing, False

        # Cooldown elapsed -> re-notify and restart the window.
        existing.cooldown_until = now + timedelta(seconds=settings.ALERT_COOLDOWN_SEC)
        db.commit()
        _send_notification(db, existing, now)
        return existing, True

    alert = Alert(
        fingerprint=fp,
        service=service,
        rule=rule,
        kind=kind,
        severity=severity,
        title=title,
        description=description,
        value=value,
        score=score,
        status="open",
        count=1,
        context=context,
        first_seen=now,
        last_seen=now,
        cooldown_until=now + timedelta(seconds=settings.ALERT_COOLDOWN_SEC),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    _send_notification(db, alert, now)
    return alert, True


def _send_notification(db: Session, alert: Alert, now) -> Notification:
    """Simulate an outbound webhook: persist the payload and print it."""
    payload = {
        "alert_id": alert.id,
        "service": alert.service,
        "severity": alert.severity,
        "title": alert.title,
        "description": alert.description,
        "rule": alert.rule,
        "kind": alert.kind,
        "value": alert.value,
        "score": alert.score,
        "count": alert.count,
        "fired_at": now.isoformat(),
    }
    notif = Notification(
        alert_id=alert.id,
        channel="webhook",
        url=settings.WEBHOOK_URL,
        payload=payload,
        status="sent",
    )
    db.add(notif)
    db.commit()
    db.refresh(notif)
    print(f"[WEBHOOK→{settings.WEBHOOK_URL}] {alert.severity} {alert.title} "
          f"(service={alert.service}, value={alert.value}, count={alert.count})")
    return notif


# --------------------------------------------------------------------------- #
# Queries / lifecycle
# --------------------------------------------------------------------------- #
def list_alerts(db: Session, status: Optional[str] = None,
                severity: Optional[str] = None, limit: int = 100) -> List[Alert]:
    q = select(Alert).order_by(Alert.last_seen.desc()).limit(limit)
    if status:
        q = q.where(Alert.status == status)
    if severity:
        q = q.where(Alert.severity == severity.upper())
    return list(db.scalars(q))


def update_alert_status(db: Session, alert_id: int, status: str) -> Optional[Alert]:
    alert = db.get(Alert, alert_id)
    if alert is None:
        return None
    alert.status = status
    db.commit()
    db.refresh(alert)
    return alert


def list_notifications(db: Session, alert_id: Optional[int] = None,
                       window: Optional[str] = None,
                       limit: int = 100) -> List[Notification]:
    """Recorded simulated-webhook deliveries, most recent first."""
    q = select(Notification).order_by(Notification.created_at.desc()).limit(limit)
    if alert_id is not None:
        q = q.where(Notification.alert_id == alert_id)
    if window:
        since = utcnow() - parse_window(window)
        q = q.where(Notification.created_at >= since)
    return list(db.scalars(q))
