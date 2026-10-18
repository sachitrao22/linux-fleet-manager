import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Alert, Device, Metric
from app.routers.deps import (AgentAuth, Principal, can_delete, device_scope, get_principal,
                              require_admin, require_agent, visible_device)
from app.schemas.device import AgentlessCreate, AgentRegister, DeviceOverview, DeviceResponse
from app.schemas.metric import MetricResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceResponse)
async def register_agent(payload: AgentRegister, db: AsyncSession = Depends(get_db),
                         auth: AgentAuth = Depends(require_agent)):
    """
    Called by the agent on every (re)connect. If the agent sends the id it saved last
    time, that row is updated; otherwise a new device is created and its id returned
    for the agent to save. Identity is the id — hostnames may collide.
    The agent token decides where the device belongs: the owner's fleet, or a visitor's
    demo sandbox.
    """
    if not settings.accepts_agents:
        raise HTTPException(status_code=403, detail="This control plane runs in agentless-only mode")

    existing = await db.get(Device, payload.device_id) if payload.device_id else None
    device = existing
    if device is not None and (device.mode != "agent" or device.workspace_id != auth.workspace_id):
        # Never let an agent take over an agentless entry, or a device in another workspace
        device = None

    if device is None and auth.workspace is not None:
        count = (await db.execute(select(func.count()).select_from(Device)
                                  .where(Device.workspace_id == auth.workspace_id))).scalar_one()
        if count >= settings.demo_max_devices:
            raise HTTPException(status_code=429, detail=f"This demo session already has {count} devices "
                                "(the limit). Remove one from the dashboard to add another.")
        total = (await db.execute(select(func.count()).select_from(Device)
                                  .where(Device.workspace_id.is_not(None)))).scalar_one()
        if total >= settings.demo_max_total_devices:
            raise HTTPException(status_code=429, detail="The public demo is full right now — please try again later.")

    fields = payload.model_dump(exclude={"device_id", "tags"}, exclude_none=True)
    if device is None:
        device = Device(mode="agent", workspace_id=auth.workspace_id, tags=payload.tags or {}, **fields)
        # The owner may pin an id (e.g. the demo server's own agent, whose local state is wiped
        # on every container restart); a sandbox agent always gets a fresh one.
        if auth.is_owner and payload.device_id and existing is None:
            device.id = payload.device_id
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
async def fleet_overview(db: AsyncSession = Depends(get_db), p: Principal = Depends(get_principal)):
    """
    Every visible device with its latest sample and open-alert count — 3 queries total,
    regardless of fleet size (no N+1).
    """
    scope = device_scope(p)
    devices = (await db.execute(select(Device).where(scope).order_by(Device.hostname))).scalars().all()
    ids = [d.id for d in devices]
    if not ids:
        return []

    latest_ts = (
        select(Metric.device_id, func.max(Metric.ts).label("ts"))
        .where(Metric.device_id.in_(ids)).group_by(Metric.device_id).subquery()
    )
    latest_rows = (await db.execute(
        select(Metric).join(latest_ts, (Metric.device_id == latest_ts.c.device_id) & (Metric.ts == latest_ts.c.ts))
    )).scalars().all()
    latest = {m.device_id: m for m in latest_rows}

    alert_counts = dict((await db.execute(
        select(Alert.device_id, func.count()).where(Alert.status == "open", Alert.device_id.in_(ids))
        .group_by(Alert.device_id)
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
                     p: Principal = Depends(get_principal)):
    return await visible_device(db, p, device_id)


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(get_principal)):
    device = await visible_device(db, p, device_id)
    if not can_delete(p, device):
        raise HTTPException(status_code=403, detail="You can only remove your own devices")
    await db.delete(device)   # metrics, commands and alerts cascade
    await db.commit()
