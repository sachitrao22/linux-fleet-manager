"""
Public demo: visitor sandboxes and the one-line installers.

  POST /demo/sessions   → a private sandbox (workspace) + its agent token + a session token
  GET  /install.sh      → macOS/Linux installer:  curl -fsSL <api>/install.sh | sh -s -- <token>
  GET  /install.ps1     → Windows installer:      $env:FLEET_TOKEN="<token>"; irm <api>/install.ps1 | iex
  GET  /agent.py        → the agent itself (the installers download it from here)
"""
import secrets
from datetime import timedelta
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import FileResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Workspace
from app.models.base import utcnow
from app.services.auth import create_demo_token
from app.services.ratelimit import RateLimiter

router = APIRouter(tags=["demo"])
ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).resolve().parents[1] / "templates"
session_limiter = RateLimiter(settings.demo_sessions_per_ip_per_hour, 3600)


def api_base(request: Request) -> str:
    """The public URL agents should connect to."""
    return (settings.public_api_url or str(request.base_url)).rstrip("/")


@router.post("/demo/sessions", status_code=201)
async def create_session(request: Request, db: AsyncSession = Depends(get_db)):
    if not settings.demo_mode:
        raise HTTPException(status_code=404, detail="Not found")
    session_limiter.check(request, "demo sessions")
    expires = utcnow() + timedelta(minutes=settings.demo_session_minutes)
    ws = Workspace(agent_token="demo_" + secrets.token_urlsafe(24), expires_at=expires)
    db.add(ws)
    await db.commit()
    base = api_base(request)
    return {
        "workspace_id": ws.id,
        "agent_token": ws.agent_token,
        "session_token": create_demo_token(ws.id, expires),
        "expires_at": expires,
        "max_devices": settings.demo_max_devices,
        "install": {
            "unix": f"curl -fsSL {base}/install.sh | sh -s -- {ws.agent_token}",
            "windows": f'$env:FLEET_TOKEN="{ws.agent_token}"; irm {base}/install.ps1 | iex',
        },
    }


def _render(name: str, request: Request) -> PlainTextResponse:
    text = (TEMPLATES / name).read_text().replace("__API__", api_base(request))
    return PlainTextResponse(text, headers={"Cache-Control": "no-store"})


@router.get("/install.sh", include_in_schema=False)
async def install_sh(request: Request):
    return _render("install.sh", request)


@router.get("/install.ps1", include_in_schema=False)
async def install_ps1(request: Request):
    return _render("install.ps1", request)


@router.get("/agent.py", include_in_schema=False)
async def agent_py():
    return FileResponse(ROOT / "agent" / "agent.py", media_type="text/x-python",
                        headers={"Cache-Control": "no-store"})
