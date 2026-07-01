from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.routers.auth import get_current_user, require_admin
from app.schemas.device import DeviceRegister, DeviceResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceResponse)
async def register_device(payload: DeviceRegister, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.hostname == payload.hostname))
    device = result.scalar_one_or_none()

    if device:
        for k, v in payload.model_dump(exclude_none=True).items():
            setattr(device, k, v)
        device.status = "online"
        device.last_seen = datetime.now(timezone.utc)
    else:
        device = Device(**payload.model_dump(), status="online", last_seen=datetime.now(timezone.utc))
        db.add(device)

    await db.commit()
    await db.refresh(device)
    return device


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Device).order_by(Device.registered_at.desc()))
    return result.scalars().all()


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
