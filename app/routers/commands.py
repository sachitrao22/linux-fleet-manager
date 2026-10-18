import uuid
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.actions import ACTIONS, is_action
from app.config import settings
from app.database import get_db
from app.models import Command
from app.models.base import utcnow
from app.routers.deps import Principal, command_rights, get_principal, visible_device
from app.schemas.command import CommandCreate, CommandResponse
from app.services.poller import run_agentless_command
from app.services.ratelimit import RateLimiter
from app.services.ws_manager import manager

router = APIRouter(prefix="/commands", tags=["commands"])
demo_command_limiter = RateLimiter(settings.demo_commands_per_ip_per_minute, 60)


def command_message(cmd: Command) -> dict:
    return {"type": "command", "command_id": str(cmd.id), "command": cmd.command}


@router.post("/{device_id}", response_model=CommandResponse, status_code=201)
async def issue_command(device_id: uuid.UUID, payload: CommandCreate, request: Request,
                        background: BackgroundTasks,
                        db: AsyncSession = Depends(get_db),
                        p: Principal = Depends(get_principal)):
    device = await visible_device(db, p, device_id)
    may_act, may_shell = command_rights(p, device)
    if not may_act:
        raise HTTPException(status_code=403, detail="Admin role required to run commands")
    command = payload.command.strip()
    if not may_shell and not is_action(command):
        raise HTTPException(status_code=403, detail="Only the built-in read-only actions can be run here: "
                            + ", ".join(ACTIONS))
    if device.mode != "agent" and is_action(command):
        raise HTTPException(status_code=400, detail="Built-in actions need the agent; "
                            "for SSH hosts, run a shell command instead")
    if not p.is_admin:
        demo_command_limiter.check(request, "commands")

    if payload.idempotency_key:
        existing = (await db.execute(
            select(Command).where(Command.idempotency_key == payload.idempotency_key)
        )).scalars().first()
        if existing:
            if existing.device_id != device.id:
                raise HTTPException(status_code=409, detail="Idempotency key already used for another device")
            # Same key = same logical request (e.g. a client retry). Return the original.
            return existing

    # 1. Persist FIRST. If the server crashes after this line, the command still exists.
    cmd = Command(device_id=device.id, command=command,
                  idempotency_key=payload.idempotency_key, created_by=p.label)
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
                        p: Principal = Depends(get_principal)):
    await visible_device(db, p, device_id)
    return (await db.execute(
        select(Command).where(Command.device_id == device_id)
        .order_by(Command.created_at.desc()).limit(20 if p.kind == "anonymous" else 50)
    )).scalars().all()


@router.get("", tags=["commands"])
async def list_actions():
    """The built-in read-only actions (shown as buttons in the dashboard)."""
    return [{"command": k, "label": v} for k, v in ACTIONS.items()]
