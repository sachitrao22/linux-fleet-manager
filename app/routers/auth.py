from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import User
from app.routers.deps import get_current_user, get_optional_user
from app.schemas.auth import Token, UserCreate, UserResponse
from app.services.auth import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/status")
async def auth_status(db: AsyncSession = Depends(get_db)):
    """Lets the dashboard know whether to show 'create first admin' or 'log in'."""
    count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    return {"setup_required": count == 0}


@router.post("/register", status_code=201, response_model=UserResponse)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db),
                   caller: Optional[User] = Depends(get_optional_user)):
    """
    Bootstrap rule: the very first account is created freely and is always admin.
    After that, only an admin can create accounts. (Open self-registration as admin
    would let anyone take over the fleet and run commands on every machine.)
    """
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar_one()
    if user_count == 0:
        role = "admin"
    elif caller is None or caller.role != "admin":
        raise HTTPException(status_code=403, detail="Only an admin can create users")
    else:
        role = payload.role

    if (await db.execute(select(User).where(User.username == payload.username))).scalars().first():
        raise HTTPException(status_code=400, detail="Username already taken")

    user = User(username=payload.username, hashed_password=hash_password(payload.password), role=role)
    db.add(user)
    await db.commit()
    return user


@router.post("/token", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    user = (await db.execute(select(User).where(User.username == form.username))).scalars().first()
    # Same error for "no such user" and "wrong password" — don't reveal which usernames exist
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return Token(access_token=create_access_token(user.username, user.role))


@router.get("/me", response_model=UserResponse)
async def me(user: User = Depends(get_current_user)):
    return user
