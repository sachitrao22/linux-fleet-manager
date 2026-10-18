import uuid
from typing import List, Literal, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Alert, Device
from app.routers.deps import Principal, device_scope, get_principal
from app.schemas.alert import AlertResponse

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=List[AlertResponse])
async def list_alerts(status: Literal["open", "resolved", "all"] = "open",
                      device_id: Optional[uuid.UUID] = None,
                      limit: int = Query(default=100, ge=1, le=1000),
                      db: AsyncSession = Depends(get_db),
                      p: Principal = Depends(get_principal)):
    q = (select(Alert).join(Device, Device.id == Alert.device_id).where(device_scope(p))
         .order_by(Alert.opened_at.desc()).limit(limit))
    if status != "all":
        q = q.where(Alert.status == status)
    if device_id:
        q = q.where(Alert.device_id == device_id)
    return (await db.execute(q)).scalars().all()
