import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.models.metric import Metric
from app.routers.auth import get_current_user
from app.schemas.metric import MetricPayload, MetricResponse

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("/{device_id}", response_model=MetricResponse)
async def ingest_metric(device_id: str, payload: MetricPayload, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device:
        device.last_seen = datetime.now(timezone.utc)
        device.status = "online"

    metric = Metric(device_id=uuid.UUID(device_id), ts=datetime.now(timezone.utc), **payload.model_dump())
    db.add(metric)
    await db.commit()
    await db.refresh(metric)
    return metric


@router.get("/{device_id}/latest", response_model=MetricResponse)
async def get_latest(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Metric).where(Metric.device_id == uuid.UUID(device_id)).order_by(desc(Metric.ts)).limit(1)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="No metrics yet for this device")
    return metric


@router.get("/{device_id}", response_model=List[MetricResponse])
async def get_metrics(
    device_id: str,
    hours: int = Query(default=1, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(Metric)
        .where(Metric.device_id == uuid.UUID(device_id), Metric.ts >= since)
        .order_by(desc(Metric.ts))
        .limit(1000)
    )
    return result.scalars().all()
