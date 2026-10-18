import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, Response

from app.config import settings
from app.database import init_db
from app.routers import alerts, auth, commands, demo, devices, metrics, ws
from app.services import poller

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logging.getLogger("paramiko").setLevel(logging.WARNING)   # one INFO line per SSH login is noise
log = logging.getLogger("fleet")

WEB = Path(__file__).resolve().parents[1] / "web"     # the dashboard (also deployable to Vercel)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await poller.ensure_admin_from_settings()
    await poller.mark_agents_offline_on_startup()

    tasks = [asyncio.create_task(poller.sweeper_loop()), asyncio.create_task(poller.cleanup_loop())]
    if settings.accepts_agentless:
        if settings.hosts_file:
            n = await poller.import_hosts_file(settings.hosts_file)
            log.info("imported %d agentless host(s) from %s", n, settings.hosts_file)
        tasks.append(asyncio.create_task(poller.agentless_loop()))

    log.info("Fleet Manager started — mode=%s, demo=%s, database=%s", settings.fleet_mode,
             settings.demo_mode, settings.database_url.split("://")[0])
    if not settings.agent_token and settings.accepts_agents:
        log.warning("AGENT_TOKEN is not set: %s", "owner agents are refused in demo mode"
                    if settings.demo_mode else "any machine can register as an agent (dev only)")
    if settings.demo_mode and not settings.admin_username:
        log.warning("DEMO_MODE without ADMIN_USERNAME/ADMIN_PASSWORD: nobody can log in as admin")
    yield
    for t in tasks:
        t.cancel()


app = FastAPI(title="Linux Fleet Manager", version="2.1.0", lifespan=lifespan)

if settings.cors_origin_list:
    # The dashboard may be hosted elsewhere (e.g. Vercel). Auth is a Bearer header, not
    # cookies, so credentials stay off and the rules stay simple.
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list,
                       allow_methods=["*"], allow_headers=["*"], allow_credentials=False)

app.include_router(auth.router)
app.include_router(devices.router)
app.include_router(metrics.router)
app.include_router(commands.router)
app.include_router(alerts.router)
app.include_router(ws.router)
app.include_router(demo.router)


@app.get("/health", tags=["meta"])
async def health():
    return {"status": "ok", "mode": settings.fleet_mode, "demo": settings.demo_mode,
            "demo_session_minutes": settings.demo_session_minutes if settings.demo_mode else None,
            "version": app.version}


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(WEB / "index.html")


@app.get("/config.js", include_in_schema=False)
async def dashboard_config():
    # Served by this API, the dashboard talks to the same origin. On Vercel, web/config.js
    # (with the API's URL) is served instead.
    return Response('window.FLEET_API = "";\n', media_type="application/javascript",
                    headers={"Cache-Control": "no-store"})
