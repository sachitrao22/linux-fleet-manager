"""
Threshold alert engine, evaluated every time a sample is written.

For each metric the sample is classified as ok / warning / critical:
  - no open alert + breach      → open a new alert      (one alert per incident)
  - open alert   + still breach → update value/severity (deduplication, escalation)
  - open alert   + back to ok   → resolve it
"""
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Alert, Device
from app.models.base import utcnow


def thresholds():
    return {
        "cpu_pct": (settings.cpu_warn, settings.cpu_crit),
        "mem_pct": (settings.mem_warn, settings.mem_crit),
        "disk_pct": (settings.disk_warn, settings.disk_crit),
    }


def classify(value: Optional[float], warn: float, crit: float) -> Optional[str]:
    if value is None:
        return None
    if value >= crit:
        return "critical"
    if value >= warn:
        return "warning"
    return None


async def _open_alert(db: AsyncSession, device_id, metric: str) -> Optional[Alert]:
    res = await db.execute(
        select(Alert).where(Alert.device_id == device_id, Alert.metric == metric, Alert.status == "open")
    )
    return res.scalars().first()


async def evaluate_sample(db: AsyncSession, device: Device, sample: dict) -> None:
    """Caller commits."""
    for metric, (warn, crit) in thresholds().items():
        if metric not in sample:
            continue
        value = sample[metric]
        severity = classify(value, warn, crit)
        current = await _open_alert(db, device.id, metric)

        if severity and current is None:
            db.add(Alert(device_id=device.id, metric=metric, severity=severity, value=value,
                         threshold=crit if severity == "critical" else warn))
        elif severity and current is not None:
            current.value = value
            current.severity = severity
            current.threshold = crit if severity == "critical" else warn
        elif severity is None and current is not None:
            current.status = "resolved"
            current.resolved_at = utcnow()


async def set_offline_alert(db: AsyncSession, device: Device, offline: bool) -> None:
    """A device going silent is itself an alert. Caller commits."""
    current = await _open_alert(db, device.id, "offline")
    if offline and current is None:
        db.add(Alert(device_id=device.id, metric="offline", severity="critical"))
    elif not offline and current is not None:
        current.status = "resolved"
        current.resolved_at = utcnow()
