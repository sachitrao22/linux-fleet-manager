import uuid
from datetime import timedelta
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Metric
from app.models.base import utcnow
from app.routers.deps import Principal, get_principal, visible_device
from app.schemas.metric import MetricResponse

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.get("/{device_id}", response_model=List[MetricResponse])
async def history(device_id: uuid.UUID,
                  minutes: int = Query(default=60, ge=1, le=7 * 24 * 60),
                  limit: int = Query(default=720, ge=1, le=5000),
                  db: AsyncSession = Depends(get_db),
                  p: Principal = Depends(get_principal)):
    """Oldest-first samples for charting. Uses the (device_id, ts) index."""
    await visible_device(db, p, device_id)
    since = utcnow() - timedelta(minutes=minutes)
    rows = (await db.execute(
        select(Metric).where(Metric.device_id == device_id, Metric.ts >= since)
        .order_by(Metric.ts.desc()).limit(limit)
    )).scalars().all()
    return list(reversed(rows))


@router.get("/{device_id}/latest", response_model=MetricResponse)
async def latest(device_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                 p: Principal = Depends(get_principal)):
    await visible_device(db, p, device_id)
    row = (await db.execute(
        select(Metric).where(Metric.device_id == device_id).order_by(Metric.ts.desc()).limit(1)
    )).scalars().first()
    if row is None:
        raise HTTPException(status_code=404, detail="No metrics yet for this device")
    return row
