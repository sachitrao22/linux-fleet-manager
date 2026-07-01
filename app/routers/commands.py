import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.command import Command
from app.models.device import Device
from app.routers.auth import get_current_user, require_admin
from app.schemas.command import CommandCreate, CommandResponse, CommandResultUpdate
from app.services.ssh_collector import run_command_ssh
from app.services.ws_manager import manager

router = APIRouter(prefix="/commands", tags=["commands"])


async def _run_ssh_command(device: Device, command_id: str, cmd: str, db: AsyncSession):
    status, result_text = "failed", ""
    try:
        result_text = run_command_ssh(
            hostname=device.ip_address or device.hostname,
            username=device.ssh_user or "root",
            command=cmd,
            port=int(device.ssh_port or 22),
            key_path=settings.ssh_key_path,
        )
        status = "executed"
    except Exception as e:
        result_text = str(e)

    db_result = await db.execute(select(Command).where(Command.id == uuid.UUID(command_id)))
    cmd_row = db_result.scalar_one_or_none()
    if cmd_row:
        cmd_row.status = status
        cmd_row.result = result_text
        cmd_row.executed_at = datetime.now(timezone.utc)
        await db.commit()


@router.post("/{device_id}", response_model=CommandResponse)
async def issue_command(
    device_id: str,
    payload: CommandCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    idem_key = payload.idempotency_key or str(uuid.uuid4())
    if payload.idempotency_key:
        existing = await db.execute(select(Command).where(Command.idempotency_key == idem_key))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Duplicate command: idempotency key already used")

    command = Command(
        device_id=uuid.UUID(device_id),
        command=payload.command,
        idempotency_key=idem_key,
        status="pending",
    )
    db.add(command)
    await db.commit()
    await db.refresh(command)

    if device.mode == "agent":
        if manager.is_connected(device_id):
            sent = await manager.send_command(device_id, {
                "type": "command",
                "command_id": str(command.id),
                "command": payload.command,
            })
            if sent:
                command.status = "delivered"
                command.delivered_at = datetime.now(timezone.utc)
                await db.commit()
    elif device.mode == "agentless":
        background_tasks.add_task(_run_ssh_command, device, str(command.id), payload.command, db)
        command.status = "delivered"
        command.delivered_at = datetime.now(timezone.utc)
        await db.commit()

    await db.refresh(command)
    return command


@router.get("/{device_id}", response_model=List[CommandResponse])
async def list_commands(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Command).where(Command.device_id == uuid.UUID(device_id)).order_by(Command.created_at.desc()).limit(50)
    )
    return result.scalars().all()


@router.get("/{device_id}/pending", response_model=List[CommandResponse])
async def get_pending_commands(device_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Command)
        .where(Command.device_id == uuid.UUID(device_id), Command.status == "pending")
        .order_by(Command.created_at.asc())
    )
    return result.scalars().all()


@router.patch("/{command_id}/result")
async def update_command_result(command_id: str, payload: CommandResultUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Command).where(Command.id == uuid.UUID(command_id)))
    command = result.scalar_one_or_none()
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    command.status = payload.status
    command.result = payload.result
    command.executed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Updated"}
