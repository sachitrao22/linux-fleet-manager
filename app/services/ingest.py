"""
The single write path for metrics, shared by the WebSocket handler (agent mode)
and the SSH poller (agentless mode), so both modes behave identically downstream.
"""
from datetime import datetime, timedelta, timezone
from typing import Optional

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Device, Metric
from app.models.base import utcnow
from app.schemas.metric import MetricPayload
from app.services.alerts import evaluate_sample, set_offline_alert

MAX_CLOCK_SKEW = timedelta(minutes=5)


def _sample_time(ts: Optional[datetime]) -> datetime:
    """Trust the device's timestamp (important for buffered samples) but never the future."""
    now = utcnow()
    if ts is None:
        return now
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return min(ts, now + MAX_CLOCK_SKEW)


async def record_sample(db: AsyncSession, device: Device, raw: dict, evaluate: bool = True) -> bool:
    """Validate and store one sample, mark the device online. Returns False if invalid. Caller commits."""
    try:
        payload = MetricPayload.model_validate(raw)
    except ValidationError:
        return False

    values = payload.model_dump(exclude={"ts"}, exclude_none=True)
    db.add(Metric(device_id=device.id, ts=_sample_time(payload.ts), **values))

    if device.status != "online":
        await set_offline_alert(db, device, offline=False)
    device.status = "online"
    device.last_seen = utcnow()

    if evaluate:
        await evaluate_sample(db, device, values)
    return True
