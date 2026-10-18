"""
Who is calling, and what may they see and do?

Every request resolves to a Principal:
  admin      — logged-in user with role admin: everything, including shell commands
  viewer     — logged-in user with role viewer: read-only, every device
  demo       — a public-demo visitor with a sandbox: sees the showcase + their own devices,
               may run built-in read-only actions on them
  anonymous  — no token, public demo only: sees the showcase, may run read-only actions
Outside demo mode, a request without a valid login is rejected (401) as before.
"""
import hmac
import uuid
from dataclasses import dataclass
from typing import Optional, Tuple

from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import or_, select, true
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Device, User, Workspace
from app.models.base import utcnow
from app.services.auth import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


@dataclass
class Principal:
    kind: str                                   # admin | viewer | demo | anonymous
    username: Optional[str] = None
    workspace_id: Optional[uuid.UUID] = None

    @property
    def is_admin(self) -> bool:
        return self.kind == "admin"

    @property
    def label(self) -> str:
        """Recorded as created_by on commands (audit trail)."""
        return self.username or ("demo visitor" if self.kind == "demo" else "anonymous visitor")


async def get_principal(token: Optional[str] = Depends(oauth2_scheme),
                        db: AsyncSession = Depends(get_db)) -> Principal:
    if token:
        payload = decode_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Session expired — please log in again",
                                headers={"WWW-Authenticate": "Bearer"})
        if payload.get("ws"):
            ws = await db.get(Workspace, uuid.UUID(payload["ws"]))
            if ws is None or ws.expires_at <= utcnow():
                raise HTTPException(status_code=401, detail="Your demo session has ended")
            return Principal(kind="demo", workspace_id=ws.id)
        user = (await db.execute(select(User).where(User.username == payload.get("sub")))).scalars().first()
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return Principal(kind=user.role, username=user.username)
    if settings.demo_mode:
        return Principal(kind="anonymous")
    raise HTTPException(status_code=401, detail="Not authenticated", headers={"WWW-Authenticate": "Bearer"})


async def get_optional_user(token: Optional[str] = Depends(oauth2_scheme),
                            db: AsyncSession = Depends(get_db)) -> Optional[User]:
    if not token:
        return None
    payload = decode_token(token)
    if not payload or payload.get("ws"):
        return None
    return (await db.execute(select(User).where(User.username == payload.get("sub")))).scalars().first()


async def get_current_user(user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


# ── what a principal may see / do ────────────────────────────────────────────

def device_scope(p: Principal):
    """SQL condition limiting devices to those this principal may see."""
    if p.kind in ("admin", "viewer"):
        return true()
    if p.kind == "demo":
        return or_(Device.workspace_id.is_(None), Device.workspace_id == p.workspace_id)
    return Device.workspace_id.is_(None)        # anonymous: the showcase only


def can_see(p: Principal, device: Device) -> bool:
    if p.kind in ("admin", "viewer"):
        return True
    return device.workspace_id is None or (p.kind == "demo" and device.workspace_id == p.workspace_id)


async def visible_device(db: AsyncSession, p: Principal, device_id: uuid.UUID) -> Device:
    """Fetch a device, or 404 — also when it exists but isn't visible (don't leak that it exists)."""
    device = await db.get(Device, device_id)
    if device is None or not can_see(p, device):
        raise HTTPException(status_code=404, detail="Device not found")
    return device


def command_rights(p: Principal, device: Device) -> Tuple[bool, bool]:
    """(may run built-in actions, may run arbitrary shell)."""
    if p.is_admin:
        return True, device.workspace_id is None    # never shell into a visitor's machine
    if settings.demo_mode and p.kind in ("demo", "anonymous") and can_see(p, device):
        return True, False
    return False, False


def can_delete(p: Principal, device: Device) -> bool:
    return p.is_admin or (p.kind == "demo" and device.workspace_id == p.workspace_id)


# ── agent authentication ─────────────────────────────────────────────────────

class AgentAuth:
    """Who an agent token belongs to: the owner's fleet, or a visitor's sandbox."""

    def __init__(self, workspace: Optional[Workspace]):
        self.workspace = workspace

    @property
    def workspace_id(self) -> Optional[uuid.UUID]:
        return self.workspace.id if self.workspace else None

    @property
    def is_owner(self) -> bool:
        return self.workspace is None


def owner_token_ok(presented: Optional[str]) -> bool:
    if not settings.agent_token:
        # Open registration is a development convenience — never on a public demo
        return not settings.demo_mode
    # Constant-time comparison so response timing doesn't leak how much of the token matched
    return presented is not None and hmac.compare_digest(presented, settings.agent_token)


async def resolve_agent_token(db: AsyncSession, presented: Optional[str]) -> AgentAuth:
    if owner_token_ok(presented):
        return AgentAuth(None)
    if presented:
        ws = (await db.execute(select(Workspace).where(Workspace.agent_token == presented))).scalars().first()
        if ws is not None:
            if ws.expires_at <= utcnow():
                raise HTTPException(status_code=410, detail="This demo session has ended. "
                                    "Open the dashboard and click 'Add my device' to start a new one.")
            return AgentAuth(ws)
    raise HTTPException(status_code=401, detail="Invalid agent token")


async def require_agent(x_agent_token: Optional[str] = Header(default=None),
                        db: AsyncSession = Depends(get_db)) -> AgentAuth:
    return await resolve_agent_token(db, x_agent_token)


async def token_matches_device(db: AsyncSession, device: Device, presented: Optional[str]) -> bool:
    """A sandbox token can only connect sandbox devices, and the owner token only owner devices."""
    try:
        auth = await resolve_agent_token(db, presented)
    except HTTPException:
        return False
    return auth.workspace_id == device.workspace_id
