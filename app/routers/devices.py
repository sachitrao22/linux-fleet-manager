import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Alert, Device, Metric
from app.routers.deps import get_current_user, require_admin, require_agent_token
from app.schemas.device import AgentlessCreate, AgentRegister, DeviceOverview, DeviceResponse
from app.schemas.metric import MetricResponse

router = APIRouter(prefix="/devices", tags=["devices"])


async def _get_device(db: AsyncSession, device_id: uuid.UUID) -> Device:
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.post("/register", response_model=DeviceResponse, dependencies=[Depends(require_agent_token)])
async def register_agent(payload: AgentRegister, db: AsyncSession = Depends(get_db)):
    """
    Called by the agent on every (re)connect. If the agent sends the id it saved last
    time, that row is updated; otherwise a new device is created and its id returned
    for the agent to save. Identity is the id — hostnames may collide.
    """
    if not settings.accepts_agents:
        raise HTTPException(status_code=403, detail="This control plane runs in agentless-only mode")

    device = await db.get(Device, payload.device_id) if payload.device_id else None
    if device is not None and device.mode != "agent":
        device = None   # never let an agent take over an agentless entry

    fields = payload.model_dump(exclude={"device_id", "tags"}, exclude_none=True)
    if device is None:
        device = Device(mode="agent", tags=payload.tags or {}, **fields)
        db.add(device)
    else:
        for key, value in fields.items():
            setattr(device, key, value)
        if payload.tags:
            device.tags = payload.tags
    await db.commit()
    await db.refresh(device)
    return device


@router.post("/agentless", response_model=DeviceResponse, status_code=201)
async def add_agentless(payload: AgentlessCreate, db: AsyncSession = Depends(get_db),
                        _admin=Depends(require_admin)):
    """Add a host the server will SSH into. Nothing is installed on it."""
    if not settings.accepts_agentless:
        raise HTTPException(status_code=403, detail="This control plane runs in agent-only mode")
    device = Device(mode="agentless", **payload.model_dump())
    db.add(device)
    await db.commit()
    await db.refresh(device)
    return device


@router.get("", response_model=List[DeviceOverview])
async def fleet_overview(db: AsyncSession = Depends(get_db), _user=Depends(get_current_user)):
    """
    Every device with its latest sample and open-alert count — 3 queries total,
    regardless of fleet size (no N+1).
    """
    devices = (await db.execute(select(Device).order_by(Device.hostname))).scalars().all()

    latest_ts = (
        select(Metric.device_id, func.max(Metric.ts).label("ts"))
        .group_by(Metric.device_id).subquery()
    )
    latest_rows = (await db.execute(
        select(Metric).join(latest_ts, (Metric.device_id == latest_ts.c.device_id) & (Metric.ts == latest_ts.c.ts))
    )).scalars().all()
    latest = {m.device_id: m for m in latest_rows}

    alert_counts = dict((await db.execute(
        select(Alert.device_id, func.count()).where(Alert.status == "open").group_by(Alert.device_id)
    )).all())

    out = []
    for d in devices:
        item = DeviceOverview.model_validate(d)
        if d.id in latest:
            item.latest = MetricResponse.model_validate(latest[d.id])
        item.open_alerts = alert_counts.get(d.id, 0)
        out.append(item)
    return out


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                     _user=Depends(get_current_user)):
    return await _get_device(db, device_id)


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        _admin=Depends(require_admin)):
    device = await _get_device(db, device_id)
    await db.delete(device)   # metrics, commands and alerts cascade
    await db.commit()
