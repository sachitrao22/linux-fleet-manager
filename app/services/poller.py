"""
Background loops started by main.py:

  agentless_loop – every SSH_POLL_INTERVAL, SSH into every agentless host in parallel
                   (bounded by a semaphore) and record the sample.
  sweeper_loop   – marks devices offline when they stop reporting (covers half-open
                   TCP connections and server restarts, which never fire a disconnect).

Paramiko is blocking. Each SSH call runs in a worker thread via asyncio.to_thread so
one slow host never stalls the event loop that serves the API and WebSockets.
"""
import asyncio
import logging
import uuid
from datetime import timedelta
from pathlib import Path

import yaml
from sqlalchemy import select

from app.config import settings
from app.database import AsyncSessionLocal
from app.models import Command, Device
from app.models.base import utcnow
from app.services.alerts import set_offline_alert
from app.services.ingest import record_sample
from app.services.ssh_collector import collect_metrics_ssh, run_command_ssh
from app.services.ws_manager import manager

log = logging.getLogger("fleet.poller")


async def poll_one(device_id: uuid.UUID, sem: asyncio.Semaphore) -> None:
    async with sem:
        async with AsyncSessionLocal() as db:
            device = await db.get(Device, device_id)
            if device is None:
                return
            try:
                metrics, info = await asyncio.to_thread(
                    collect_metrics_ssh, device.ip_address or device.hostname,
                    device.ssh_port or 22, device.ssh_user,
                )
                for key in ("os", "distro", "arch"):
                    if info.get(key):
                        setattr(device, key, info[key])
                if not metrics:
                    raise RuntimeError("host returned no metrics (unsupported OS?)")
                await record_sample(db, device, metrics)
            except Exception as exc:
                log.warning("agentless poll failed for %s: %s", device.hostname, exc)
                if device.status != "unreachable":
                    device.status = "unreachable"
                    await set_offline_alert(db, device, offline=True)
            await db.commit()


async def poll_agentless_once() -> int:
    async with AsyncSessionLocal() as db:
        ids = (await db.execute(select(Device.id).where(Device.mode == "agentless"))).scalars().all()
    sem = asyncio.Semaphore(settings.ssh_max_concurrency)
    await asyncio.gather(*(poll_one(i, sem) for i in ids))
    return len(ids)


async def agentless_loop() -> None:
    while True:
        try:
            await poll_agentless_once()
        except Exception:
            log.exception("agentless loop iteration failed")
        await asyncio.sleep(settings.ssh_poll_interval)


async def sweep_once() -> None:
    cutoff = utcnow() - timedelta(seconds=settings.offline_after_seconds)
    async with AsyncSessionLocal() as db:
        stale = (await db.execute(
            select(Device).where(Device.status == "online", Device.last_seen < cutoff)
        )).scalars().all()
        for device in stale:
            if device.mode == "agent" and manager.is_connected(str(device.id)):
                continue   # socket alive but quiet; ping/pong will catch a dead link
            device.status = "offline" if device.mode == "agent" else "unreachable"
            await set_offline_alert(db, device, offline=True)
            log.info("marked %s %s (no data since %s)", device.hostname, device.status, device.last_seen)
        await db.commit()


async def sweeper_loop() -> None:
    while True:
        await asyncio.sleep(15)
        try:
            await sweep_once()
        except Exception:
            log.exception("sweeper iteration failed")


async def mark_agents_offline_on_startup() -> None:
    """After a restart no sockets exist yet, so any 'online' agent status is stale."""
    async with AsyncSessionLocal() as db:
        for device in (await db.execute(
            select(Device).where(Device.mode == "agent", Device.status == "online")
        )).scalars():
            device.status = "offline"
        await db.commit()


async def import_hosts_file(path: str) -> int:
    """Upsert agentless hosts from YAML, matched on (ip_address, ssh_port)."""
    data = yaml.safe_load(Path(path).read_text()) or {}
    count = 0
    async with AsyncSessionLocal() as db:
        for h in data.get("hosts", []):
            ip, port = h["ip"], int(h.get("ssh_port", 22))
            existing = (await db.execute(select(Device).where(
                Device.mode == "agentless", Device.ip_address == ip, Device.ssh_port == port
            ))).scalars().first()
            device = existing or Device(mode="agentless", ip_address=ip, ssh_port=port)
            device.hostname = h.get("hostname", ip)
            device.ssh_user = h["ssh_user"]
            device.tags = h.get("tags", {})
            if existing is None:
                db.add(device)
            count += 1
        await db.commit()
    return count


async def run_agentless_command(command_id: uuid.UUID) -> None:
    """Execute a queued command over SSH. Opens its OWN session: the request's session is closed by now."""
    async with AsyncSessionLocal() as db:
        cmd = await db.get(Command, command_id)
        device = await db.get(Device, cmd.device_id) if cmd else None
        if cmd is None or device is None:
            return
        try:
            code, output = await asyncio.to_thread(
                run_command_ssh, device.ip_address or device.hostname,
                device.ssh_port or 22, device.ssh_user, cmd.command,
            )
        except Exception as exc:
            code, output = -1, f"SSH error: {exc}"
        cmd.exit_code = code
        cmd.result = output
        cmd.status = "executed" if code == 0 else "failed"
        cmd.executed_at = utcnow()
        await db.commit()
