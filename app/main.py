import asyncio
import logging
from contextlib import asynccontextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from fastapi import FastAPI, WebSocket
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.database import AsyncSessionLocal, init_db
from app.models.device import Device
from app.models.metric import Metric
from app.routers import auth, commands, devices, metrics
from app.services.ssh_collector import collect_metrics_ssh
from app.config import settings
from app.websocket.handler import handle_agent_websocket

log = logging.getLogger("fleet-manager")
executor = ThreadPoolExecutor(max_workers=20)

SSH_POLL_INTERVAL = 30  # seconds between SSH metric collections


async def agentless_polling_loop():
    """
    Background loop: every SSH_POLL_INTERVAL seconds, SSH into all agentless
    devices and write their metrics to Postgres.
    Paramiko is blocking, so we offload each device to the thread pool.
    """
    loop = asyncio.get_event_loop()

    while True:
        await asyncio.sleep(SSH_POLL_INTERVAL)
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Device).where(Device.mode == "agentless")
            )
            agentless_devices = result.scalars().all()

        for device in agentless_devices:
            async with AsyncSessionLocal() as db:
                try:
                    # Run blocking SSH in thread pool — doesn't stall the event loop
                    metric_data = await loop.run_in_executor(
                        executor,
                        lambda d=device: collect_metrics_ssh(
                            hostname=d.ip_address or d.hostname,
                            username=d.ssh_user or "ubuntu",
                            port=int(d.ssh_port or 22),
                            key_path=settings.ssh_key_path,
                        ),
                    )
                    metric = Metric(
                        device_id=device.id,
                        ts=datetime.now(timezone.utc),
                        **metric_data,
                    )
                    db.add(metric)
                    device_row = await db.get(Device, device.id)
                    if device_row:
                        device_row.last_seen = datetime.now(timezone.utc)
                        device_row.status = "online"
                    await db.commit()
                    log.info(f"[agentless] collected metrics from {device.hostname}")

                except Exception as e:
                    log.warning(f"[agentless] failed to collect from {device.hostname}: {e}")
                    device_row = await db.get(Device, device.id)
                    if device_row:
                        device_row.status = "unreachable"
                    await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    task = asyncio.create_task(agentless_polling_loop())
    log.info("Fleet Manager started. Agentless polling loop active.")
    yield
    task.cancel()


app = FastAPI(title="Fleet Manager API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten this in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(metrics.router)
app.include_router(commands.router)


@app.websocket("/ws/{device_id}")
async def websocket_endpoint(websocket: WebSocket, device_id: str):
    await handle_agent_websocket(device_id, websocket)


@app.get("/health")
async def health():
    return {"status": "ok"}
