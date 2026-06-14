"""Time-window helpers shared by the detection and dashboard layers."""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import List, Tuple

_WINDOW_RE = re.compile(r"^\s*(\d+)\s*([smhd])\s*$", re.IGNORECASE)
_UNIT_SECONDS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_EPOCH = datetime(1970, 1, 1)


def utcnow() -> datetime:
    """Naive UTC now (we store all timestamps as naive UTC for SQLite simplicity)."""
    return datetime.utcnow()


def to_epoch(dt: datetime) -> int:
    """Unix epoch for a NAIVE UTC datetime.

    NB: we deliberately avoid datetime.timestamp(), which interprets naive
    datetimes as *local* time. SQLite's strftime('%s', ...) treats stored
    timestamps as UTC, so bucket indices must be computed the same way to match.
    """
    return int((dt.replace(tzinfo=None) - _EPOCH).total_seconds())


def parse_window(label: str) -> timedelta:
    """'1h' -> timedelta(hours=1), '30m', '24h', '7d', '90s'."""
    m = _WINDOW_RE.match(label or "")
    if not m:
        raise ValueError(f"Invalid window '{label}'. Use forms like '1h', '3h', '24h'.")
    qty, unit = int(m.group(1)), m.group(2).lower()
    return timedelta(seconds=qty * _UNIT_SECONDS[unit])


def window_bounds(label: str, now: datetime | None = None) -> Tuple[datetime, datetime]:
    """Return (start, end) for a window label ending at `now`."""
    end = now or utcnow()
    return end - parse_window(label), end


def bucket_seconds_for(label: str) -> int:
    """Pick a sensible chart/aggregation bucket so a window has ~30-90 points."""
    span = parse_window(label).total_seconds()
    if span <= 3600:           # <= 1h   -> 1-min   (30-60 pts)
        return 60
    if span <= 3 * 3600:       # <= 3h   -> 2-min   (60-90 pts)
        return 120
    if span <= 6 * 3600:       # <= 6h   -> 5-min   (72 pts)
        return 300
    if span <= 12 * 3600:      # <= 12h  -> 10-min  (72 pts)
        return 600
    if span <= 24 * 3600:      # <= 24h  -> 30-min  (48 pts)
        return 1800
    if span <= 2 * 86400:      # <= 2d   -> 1-hour
        return 3600
    return 3 * 3600            # else (7d) -> 3-hour (56 pts)


def floor_epoch(dt: datetime, bucket_seconds: int) -> int:
    """Floor a (naive UTC) datetime to its bucket start, as a unix epoch int."""
    epoch = to_epoch(dt)
    return epoch - (epoch % bucket_seconds)


def iter_bucket_starts(start: datetime, end: datetime, bucket_seconds: int) -> List[datetime]:
    """All bucket-start datetimes covering [start, end] (UTC-consistent)."""
    cur = floor_epoch(start, bucket_seconds)
    stop = to_epoch(end)
    out: List[datetime] = []
    while cur <= stop:
        out.append(datetime.utcfromtimestamp(cur))
        cur += bucket_seconds
    return out
