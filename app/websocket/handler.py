import uuid
from datetime import datetime, timezone

from fastapi import WebSocket, WebSocketDisconnect
from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.command import Command
from app.models.device import Device
from app.models.metric import Metric
from app.services.ws_manager import manager


async def handle_agent_websocket(device_id: str, websocket: WebSocket):
    await manager.connect(device_id, websocket)

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Device).where(Device.id == device_id))
        device = result.scalar_one_or_none()
        if device:
            device.status = "online"
            device.last_seen = datetime.now(timezone.utc)
            await db.commit()

        # Drain offline command queue on reconnect
        pending = await db.execute(
            select(Command)
            .where(Command.device_id == uuid.UUID(device_id), Command.status == "pending")
            .order_by(Command.created_at.asc())
        )
        for cmd in pending.scalars().all():
            await manager.send_command(device_id, {
                "type": "command",
                "command_id": str(cmd.id),
                "command": cmd.command,
            })
            cmd.status = "delivered"
            cmd.delivered_at = datetime.now(timezone.utc)
        await db.commit()

    try:
        while True:
            data = await websocket.receive_json()
            msg_type = data.get("type")

            async with AsyncSessionLocal() as db:
                if msg_type == "metrics":
                    metric = Metric(
                        device_id=uuid.UUID(device_id),
                        ts=datetime.now(timezone.utc),
                        cpu_pct=data.get("cpu_pct"),
                        mem_pct=data.get("mem_pct"),
                        disk_pct=data.get("disk_pct"),
                        net_in_kb=data.get("net_in_kb"),
                        net_out_kb=data.get("net_out_kb"),
                        load_1m=data.get("load_1m"),
                        load_5m=data.get("load_5m"),
                        load_15m=data.get("load_15m"),
                    )
                    db.add(metric)
                    result = await db.execute(select(Device).where(Device.id == device_id))
                    device = result.scalar_one_or_none()
                    if device:
                        device.last_seen = datetime.now(timezone.utc)
                        device.status = "online"
                    await db.commit()

                elif msg_type == "command_result":
                    cmd_id = data.get("command_id")
                    result = await db.execute(select(Command).where(Command.id == uuid.UUID(cmd_id)))
                    cmd = result.scalar_one_or_none()
                    if cmd:
                        cmd.status = data.get("status", "executed")
                        cmd.result = data.get("result")
                        cmd.executed_at = datetime.now(timezone.utc)
                    await db.commit()

    except WebSocketDisconnect:
        manager.disconnect(device_id)
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Device).where(Device.id == device_id))
            device = result.scalar_one_or_none()
            if device:
                device.status = "offline"
                await db.commit()
