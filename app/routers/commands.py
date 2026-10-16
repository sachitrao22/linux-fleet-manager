import uuid
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Command, Device, User
from app.models.base import utcnow
from app.routers.deps import get_current_user, require_admin
from app.schemas.command import CommandCreate, CommandResponse
from app.services.poller import run_agentless_command
from app.services.ws_manager import manager

router = APIRouter(prefix="/commands", tags=["commands"])


def command_message(cmd: Command) -> dict:
    return {"type": "command", "command_id": str(cmd.id), "command": cmd.command}


@router.post("/{device_id}", response_model=CommandResponse, status_code=201)
async def issue_command(device_id: uuid.UUID, payload: CommandCreate,
                        background: BackgroundTasks,
                        db: AsyncSession = Depends(get_db),
                        admin: User = Depends(require_admin)):
    device = await db.get(Device, device_id)
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found")

    if payload.idempotency_key:
        existing = (await db.execute(
            select(Command).where(Command.idempotency_key == payload.idempotency_key)
        )).scalars().first()
        if existing:
            # Same key = same logical request (e.g. a client retry). Return the original.
            return existing

    # 1. Persist FIRST. If the server crashes after this line, the command still exists.
    cmd = Command(device_id=device.id, command=payload.command,
                  idempotency_key=payload.idempotency_key, created_by=admin.username)
    db.add(cmd)
    try:
        await db.commit()
    except IntegrityError:
        # Two requests with the same key raced past the check above; the UNIQUE
        # constraint let exactly one win. Return the winner's row.
        await db.rollback()
        return (await db.execute(
            select(Command).where(Command.idempotency_key == payload.idempotency_key)
        )).scalars().one()

    # 2. Then try to deliver.
    if device.mode == "agent":
        if await manager.send(str(device.id), command_message(cmd)):
            cmd.status = "delivered"
            cmd.delivered_at = utcnow()
            await db.commit()
        # else: stays "pending"; the WebSocket handler sends it when the agent reconnects
    else:
        cmd.status = "delivered"
        cmd.delivered_at = utcnow()
        await db.commit()
        # Runs after the response is sent, with its own DB session, SSH in a thread
        background.add_task(run_agentless_command, cmd.id)

    await db.refresh(cmd)
    return cmd


@router.get("/{device_id}", response_model=List[CommandResponse])
async def list_commands(device_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                        _user=Depends(get_current_user)):
    return (await db.execute(
        select(Command).where(Command.device_id == device_id)
        .order_by(Command.created_at.desc()).limit(50)
    )).scalars().all()
