"""
Agent WebSocket: /ws/{device_id}   (agent token in the X-Agent-Token header)

Lifecycle of one connection:
  1. Authenticate (agent token) and check the device exists and is an agent device.
  2. Register the socket, mark the device online.
  3. Re-send every command still pending OR delivered-but-unanswered (at-least-once).
  4. Loop: store metric samples, store command results.
  5. On disconnect, mark offline — but only if this is still the device's current socket.
"""
import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Command, Device
from app.models.base import utcnow
from app.routers.commands import command_message
from app.routers.deps import token_matches_device
from app.services.alerts import set_offline_alert
from app.services.ingest import record_sample
from app.services.ws_manager import manager

log = logging.getLogger("fleet.ws")
router = APIRouter()

MAX_BATCH = 1000


async def _redeliver(device_id: uuid.UUID, key: str) -> int:
    async with AsyncSessionLocal() as db:
        cmds = (await db.execute(
            select(Command)
            .where(Command.device_id == device_id, Command.status.in_(["pending", "delivered"]))
            .order_by(Command.created_at)
        )).scalars().all()
        sent = 0
        for cmd in cmds:
            # Record first, then send. If the socket dies right after the send, the row
            # already says "delivered" — and since delivered-but-unanswered commands are
            # re-sent on every reconnect, nothing is lost either way.
            cmd.status = "delivered"
            cmd.delivered_at = utcnow()
            await db.commit()
            if not await manager.send(key, command_message(cmd)):
                break   # socket died mid-flush; the rest stay pending for next time
            sent += 1
        return sent


async def _handle_message(device_id: uuid.UUID, msg: dict) -> None:
    kind = msg.get("type")
    async with AsyncSessionLocal() as db:
        device = await db.get(Device, device_id)
        if device is None:
            return

        if kind == "metrics":
            await record_sample(db, device, msg)

        elif kind == "metrics_batch":
            # Samples buffered while the agent was offline. Store them all with their
            # original timestamps, but only alert on the newest (avoids open/resolve churn).
            samples = msg.get("samples", [])[-MAX_BATCH:]
            for i, sample in enumerate(samples):
                await record_sample(db, device, sample, evaluate=(i == len(samples) - 1))

        elif kind == "command_result":
            try:
                cmd = await db.get(Command, uuid.UUID(str(msg.get("command_id"))))
            except ValueError:
                cmd = None
            if cmd is not None and cmd.device_id == device_id and cmd.status != "executed":
                code = msg.get("exit_code")
                cmd.exit_code = code if isinstance(code, int) else None
                cmd.result = str(msg.get("result", ""))[:64 * 1024]
                cmd.status = "executed" if cmd.exit_code == 0 else "failed"
                cmd.executed_at = utcnow()

        await db.commit()


@router.websocket("/ws/{device_id}")
async def agent_socket(websocket: WebSocket, device_id: uuid.UUID,
                       token: Optional[str] = Query(default=None)):
    # Header, not query string: URLs end up in access logs and proxies; headers don't.
    # (?token= is still accepted for clients that can't set headers, e.g. browsers.)
    presented = websocket.headers.get("x-agent-token") or token
    if not settings.accepts_agents:
        await websocket.close(code=4401)
        return

    async with AsyncSessionLocal() as db:
        device = await db.get(Device, device_id)
        if device is None or device.mode != "agent":
            await websocket.close(code=4404)   # agent will re-register and get a fresh id
            return
        # The token must belong to this device's owner: the owner token for the owner's
        # fleet, the sandbox token for a sandbox device. One sandbox can't hijack another.
        if not await token_matches_device(db, device, presented):
            await websocket.close(code=4401)
            return
        hostname = device.hostname

    key = str(device_id)
    await manager.connect(key, websocket)
    log.info("agent connected: %s (%s)", hostname, key)

    try:
        async with AsyncSessionLocal() as db:
            device = await db.get(Device, device_id)
            device.status = "online"
            device.last_seen = utcnow()
            await set_offline_alert(db, device, offline=False)
            await db.commit()

        sent = await _redeliver(device_id, key)
        if sent:
            log.info("re-sent %d queued command(s) to %s", sent, hostname)

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue   # ignore garbage rather than dropping the connection
            if isinstance(msg, dict):
                await _handle_message(device_id, msg)

    except WebSocketDisconnect:
        pass
    finally:
        if manager.disconnect(key, websocket):
            # Shielded: if this handler is being cancelled (server shutdown, client gone
            # abruptly), the status update still completes instead of being cut off mid-write.
            await asyncio.shield(_mark_offline(device_id))
            log.info("agent disconnected: %s", hostname)


async def _mark_offline(device_id: uuid.UUID) -> None:
    async with AsyncSessionLocal() as db:
        device = await db.get(Device, device_id)
        if device is not None:
            device.status = "offline"
            await set_offline_alert(db, device, offline=True)
            await db.commit()
