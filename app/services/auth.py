from datetime import timedelta
from typing import Optional

import bcrypt
import jwt

from app.config import settings
from app.models.base import utcnow


def hash_password(password: str) -> str:
    # bcrypt salts automatically and is deliberately slow (work factor) to resist brute force
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
    except ValueError:
        return False


def create_access_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": utcnow() + timedelta(minutes=settings.access_token_expire_minutes),
    }
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def create_demo_token(workspace_id, expires_at) -> str:
    """Session token for a public-demo visitor: scoped to one sandbox, expires with it."""
    payload = {"sub": f"demo:{workspace_id}", "ws": str(workspace_id), "exp": expires_at}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[dict]:
    try:
        # algorithms=[...] is pinned so an attacker can't pick "none" or a different algorithm
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except jwt.PyJWTError:
        return None
