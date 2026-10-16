"""Shared FastAPI dependencies: who is calling, and are they allowed?"""
import hmac
from typing import Optional

from fastapi import Depends, Header, HTTPException
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import User
from app.services.auth import decode_token

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token", auto_error=False)


async def get_optional_user(token: Optional[str] = Depends(oauth2_scheme),
                            db: AsyncSession = Depends(get_db)) -> Optional[User]:
    if not token:
        return None
    payload = decode_token(token)
    if not payload:
        return None
    return (await db.execute(select(User).where(User.username == payload["sub"]))).scalars().first()


async def get_current_user(user: Optional[User] = Depends(get_optional_user)) -> User:
    if user is None:
        raise HTTPException(status_code=401, detail="Not authenticated",
                            headers={"WWW-Authenticate": "Bearer"})
    return user


async def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return user


def agent_token_ok(presented: Optional[str]) -> bool:
    if not settings.agent_token:
        return True   # open registration (development only)
    # Constant-time comparison so response timing doesn't leak how much of the token matched
    return presented is not None and hmac.compare_digest(presented, settings.agent_token)


async def require_agent_token(x_agent_token: Optional[str] = Header(default=None)) -> None:
    if not agent_token_ok(x_agent_token):
        raise HTTPException(status_code=401, detail="Invalid agent token")
