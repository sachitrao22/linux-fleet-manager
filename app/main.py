import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse

from app.config import settings
from app.database import init_db
from app.routers import alerts, auth, commands, devices, metrics, ws
from app.services import poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("paramiko").setLevel(logging.WARNING)   # one INFO line per SSH login is noise
log = logging.getLogger("fleet")

STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await poller.mark_agents_offline_on_startup()

    tasks = [asyncio.create_task(poller.sweeper_loop())]
    if settings.accepts_agentless:
        if settings.hosts_file:
            n = await poller.import_hosts_file(settings.hosts_file)
            log.info("imported %d agentless host(s) from %s", n, settings.hosts_file)
        tasks.append(asyncio.create_task(poller.agentless_loop()))

    log.info("Fleet Manager started — mode=%s, database=%s", settings.fleet_mode,
             settings.database_url.split("://")[0])
    if not settings.agent_token and settings.accepts_agents:
        log.warning("AGENT_TOKEN is not set: any machine can register as an agent (dev only)")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Linux Fleet Manager", version="2.0.0", lifespan=lifespan)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(metrics.router)
app.include_router(commands.router)
app.include_router(alerts.router)
app.include_router(ws.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "mode": settings.fleet_mode}


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(STATIC / "index.html")
