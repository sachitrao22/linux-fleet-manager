#!/bin/bash
# Run this from inside your project folder (monitoring f...)
# bash setup_fleet.sh

set -e
echo "Creating folder structure..."

mkdir -p app/models app/schemas app/services app/routers app/websocket
mkdir -p agent

# ── __init__.py files ──────────────────────────────────────────────────────
touch app/__init__.py
touch app/models/__init__.py
touch app/schemas/__init__.py
touch app/services/__init__.py
touch app/routers/__init__.py
touch app/websocket/__init__.py

echo "Creating app/config.py..."
cat > app/config.py << 'EOF'
from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://fleet:fleet@localhost:5432/fleetmanager"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    ssh_key_path: Optional[str] = None

    class Config:
        env_file = ".env"


settings = Settings()
EOF

echo "Creating app/database.py..."
cat > app/database.py << 'EOF'
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

engine = create_async_engine(settings.database_url, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
EOF

# ── Models ─────────────────────────────────────────────────────────────────
echo "Creating models..."

cat > app/models/device.py << 'EOF'
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, JSON
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hostname = Column(String, nullable=False, unique=True)
    ip_address = Column(String)
    os = Column(String)
    distro = Column(String)
    arch = Column(String)
    mode = Column(String, nullable=False, default="agent")  # agent | agentless
    ssh_user = Column(String)
    ssh_port = Column(String, default="22")
    status = Column(String, default="offline")              # online | offline | unreachable
    last_seen = Column(DateTime(timezone=True))
    registered_at = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    tags = Column(JSON, default=dict)
EOF

cat > app/models/metric.py << 'EOF'
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, Float, DateTime, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Metric(Base):
    __tablename__ = "metrics"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    ts = Column(DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc))

    # Wide-row schema: one row per device snapshot (not one row per metric)
    cpu_pct    = Column(Float)
    mem_pct    = Column(Float)
    disk_pct   = Column(Float)
    net_in_kb  = Column(Float)
    net_out_kb = Column(Float)
    load_1m    = Column(Float)
    load_5m    = Column(Float)
    load_15m   = Column(Float)

    __table_args__ = (
        Index("ix_metrics_device_ts", "device_id", ts.desc()),
        Index("ix_metrics_ts", ts.desc()),
    )
EOF

cat > app/models/command.py << 'EOF'
import uuid
from datetime import datetime, timezone

from sqlalchemy import Column, String, DateTime, Text, ForeignKey
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class Command(Base):
    __tablename__ = "commands"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    device_id = Column(UUID(as_uuid=True), ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    command = Column(Text, nullable=False)
    status = Column(String, default="pending")   # pending | delivered | executed | failed
    idempotency_key = Column(String, unique=True)
    result = Column(Text)
    created_at   = Column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    delivered_at = Column(DateTime(timezone=True))
    executed_at  = Column(DateTime(timezone=True))
EOF

cat > app/models/user.py << 'EOF'
import uuid

from sqlalchemy import Column, String
from sqlalchemy.dialects.postgresql import UUID

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="viewer")   # admin | viewer
EOF

# ── Schemas ────────────────────────────────────────────────────────────────
echo "Creating schemas..."

cat > app/schemas/device.py << 'EOF'
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel


class DeviceRegister(BaseModel):
    hostname: str
    ip_address: Optional[str] = None
    os: Optional[str] = None
    distro: Optional[str] = None
    arch: Optional[str] = None
    mode: str = "agent"
    ssh_user: Optional[str] = None
    ssh_port: Optional[str] = "22"
    tags: Optional[Dict[str, Any]] = {}


class DeviceResponse(BaseModel):
    id: uuid.UUID
    hostname: str
    ip_address: Optional[str]
    os: Optional[str]
    distro: Optional[str]
    arch: Optional[str]
    mode: str
    status: str
    last_seen: Optional[datetime]
    registered_at: datetime
    tags: Optional[Dict[str, Any]]

    model_config = {"from_attributes": True}
EOF

cat > app/schemas/metric.py << 'EOF'
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class MetricPayload(BaseModel):
    cpu_pct:    Optional[float] = None
    mem_pct:    Optional[float] = None
    disk_pct:   Optional[float] = None
    net_in_kb:  Optional[float] = None
    net_out_kb: Optional[float] = None
    load_1m:    Optional[float] = None
    load_5m:    Optional[float] = None
    load_15m:   Optional[float] = None


class MetricResponse(MetricPayload):
    id: uuid.UUID
    device_id: uuid.UUID
    ts: datetime

    model_config = {"from_attributes": True}
EOF

cat > app/schemas/command.py << 'EOF'
import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class CommandCreate(BaseModel):
    command: str
    idempotency_key: Optional[str] = None


class CommandResponse(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    command: str
    status: str
    result: Optional[str]
    created_at:   datetime
    delivered_at: Optional[datetime]
    executed_at:  Optional[datetime]

    model_config = {"from_attributes": True}


class CommandResultUpdate(BaseModel):
    status: str
    result: Optional[str] = None
EOF

# ── Services ───────────────────────────────────────────────────────────────
echo "Creating services..."

cat > app/services/auth.py << 'EOF'
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt
from passlib.context import CryptContext

from app.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, settings.secret_key, algorithm=settings.algorithm)


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
EOF

cat > app/services/ws_manager.py << 'EOF'
from typing import Dict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[device_id] = websocket

    def disconnect(self, device_id: str):
        self.connections.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self.connections

    async def send_command(self, device_id: str, payload: dict) -> bool:
        ws = self.connections.get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            self.disconnect(device_id)
            return False

    def active_devices(self) -> list:
        return list(self.connections.keys())


manager = ConnectionManager()
EOF

# ── Routers ────────────────────────────────────────────────────────────────
echo "Creating routers..."

cat > app/routers/auth.py << 'EOF'
from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.user import User
from app.services.auth import create_access_token, hash_password, verify_password, decode_token

router = APIRouter(prefix="/auth", tags=["auth"])
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


class UserCreate(BaseModel):
    username: str
    password: str
    role: str = "viewer"


class Token(BaseModel):
    access_token: str
    token_type: str


@router.post("/register", status_code=201)
async def register(payload: UserCreate, db: AsyncSession = Depends(get_db)):
    existing = await db.execute(select(User).where(User.username == payload.username))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(
        username=payload.username,
        hashed_password=hash_password(payload.password),
        role=payload.role,
    )
    db.add(user)
    await db.commit()
    return {"message": "User created", "username": user.username, "role": user.role}


@router.post("/token", response_model=Token)
async def login(form: OAuth2PasswordRequestForm = Depends(), db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(User).where(User.username == form.username))
    user = result.scalar_one_or_none()
    if not user or not verify_password(form.password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    token = create_access_token({"sub": user.username, "role": user.role})
    return {"access_token": token, "token_type": "bearer"}


async def get_current_user(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)) -> User:
    payload = decode_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="Invalid or expired token")
    result = await db.execute(select(User).where(User.username == payload["sub"]))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="Admin role required")
    return current_user
EOF

cat > app/routers/devices.py << 'EOF'
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.routers.auth import get_current_user, require_admin
from app.schemas.device import DeviceRegister, DeviceResponse

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post("/register", response_model=DeviceResponse)
async def register_device(payload: DeviceRegister, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.hostname == payload.hostname))
    device = result.scalar_one_or_none()

    if device:
        for k, v in payload.model_dump(exclude_none=True).items():
            setattr(device, k, v)
        device.status = "online"
        device.last_seen = datetime.now(timezone.utc)
    else:
        device = Device(**payload.model_dump(), status="online", last_seen=datetime.now(timezone.utc))
        db.add(device)

    await db.commit()
    await db.refresh(device)
    return device


@router.get("/", response_model=List[DeviceResponse])
async def list_devices(db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Device).order_by(Device.registered_at.desc()))
    return result.scalars().all()


@router.get("/{device_id}", response_model=DeviceResponse)
async def get_device(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    return device


@router.delete("/{device_id}", status_code=204)
async def delete_device(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")
    await db.delete(device)
    await db.commit()
EOF

cat > app/routers/metrics.py << 'EOF'
import uuid
from datetime import datetime, timedelta, timezone
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models.device import Device
from app.models.metric import Metric
from app.routers.auth import get_current_user
from app.schemas.metric import MetricPayload, MetricResponse

router = APIRouter(prefix="/metrics", tags=["metrics"])


@router.post("/{device_id}", response_model=MetricResponse)
async def ingest_metric(device_id: str, payload: MetricPayload, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if device:
        device.last_seen = datetime.now(timezone.utc)
        device.status = "online"

    metric = Metric(device_id=uuid.UUID(device_id), ts=datetime.now(timezone.utc), **payload.model_dump())
    db.add(metric)
    await db.commit()
    await db.refresh(metric)
    return metric


@router.get("/{device_id}/latest", response_model=MetricResponse)
async def get_latest(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Metric).where(Metric.device_id == uuid.UUID(device_id)).order_by(desc(Metric.ts)).limit(1)
    )
    metric = result.scalar_one_or_none()
    if not metric:
        raise HTTPException(status_code=404, detail="No metrics yet for this device")
    return metric


@router.get("/{device_id}", response_model=List[MetricResponse])
async def get_metrics(
    device_id: str,
    hours: int = Query(default=1, ge=1, le=24),
    db: AsyncSession = Depends(get_db),
    _=Depends(get_current_user),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    result = await db.execute(
        select(Metric)
        .where(Metric.device_id == uuid.UUID(device_id), Metric.ts >= since)
        .order_by(desc(Metric.ts))
        .limit(1000)
    )
    return result.scalars().all()
EOF

cat > app/routers/commands.py << 'EOF'
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.command import Command
from app.models.device import Device
from app.routers.auth import get_current_user, require_admin
from app.schemas.command import CommandCreate, CommandResponse, CommandResultUpdate
from app.services.ssh_collector import run_command_ssh
from app.services.ws_manager import manager

router = APIRouter(prefix="/commands", tags=["commands"])


async def _run_ssh_command(device: Device, command_id: str, cmd: str, db: AsyncSession):
    status, result_text = "failed", ""
    try:
        result_text = run_command_ssh(
            hostname=device.ip_address or device.hostname,
            username=device.ssh_user or "root",
            command=cmd,
            port=int(device.ssh_port or 22),
            key_path=settings.ssh_key_path,
        )
        status = "executed"
    except Exception as e:
        result_text = str(e)

    db_result = await db.execute(select(Command).where(Command.id == uuid.UUID(command_id)))
    cmd_row = db_result.scalar_one_or_none()
    if cmd_row:
        cmd_row.status = status
        cmd_row.result = result_text
        cmd_row.executed_at = datetime.now(timezone.utc)
        await db.commit()


@router.post("/{device_id}", response_model=CommandResponse)
async def issue_command(
    device_id: str,
    payload: CommandCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    result = await db.execute(select(Device).where(Device.id == device_id))
    device = result.scalar_one_or_none()
    if not device:
        raise HTTPException(status_code=404, detail="Device not found")

    idem_key = payload.idempotency_key or str(uuid.uuid4())
    if payload.idempotency_key:
        existing = await db.execute(select(Command).where(Command.idempotency_key == idem_key))
        if existing.scalar_one_or_none():
            raise HTTPException(status_code=409, detail="Duplicate command: idempotency key already used")

    command = Command(
        device_id=uuid.UUID(device_id),
        command=payload.command,
        idempotency_key=idem_key,
        status="pending",
    )
    db.add(command)
    await db.commit()
    await db.refresh(command)

    if device.mode == "agent":
        if manager.is_connected(device_id):
            sent = await manager.send_command(device_id, {
                "type": "command",
                "command_id": str(command.id),
                "command": payload.command,
            })
            if sent:
                command.status = "delivered"
                command.delivered_at = datetime.now(timezone.utc)
                await db.commit()
    elif device.mode == "agentless":
        background_tasks.add_task(_run_ssh_command, device, str(command.id), payload.command, db)
        command.status = "delivered"
        command.delivered_at = datetime.now(timezone.utc)
        await db.commit()

    await db.refresh(command)
    return command


@router.get("/{device_id}", response_model=List[CommandResponse])
async def list_commands(device_id: str, db: AsyncSession = Depends(get_db), _=Depends(get_current_user)):
    result = await db.execute(
        select(Command).where(Command.device_id == uuid.UUID(device_id)).order_by(Command.created_at.desc()).limit(50)
    )
    return result.scalars().all()


@router.get("/{device_id}/pending", response_model=List[CommandResponse])
async def get_pending_commands(device_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(Command)
        .where(Command.device_id == uuid.UUID(device_id), Command.status == "pending")
        .order_by(Command.created_at.asc())
    )
    return result.scalars().all()


@router.patch("/{command_id}/result")
async def update_command_result(command_id: str, payload: CommandResultUpdate, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Command).where(Command.id == uuid.UUID(command_id)))
    command = result.scalar_one_or_none()
    if not command:
        raise HTTPException(status_code=404, detail="Command not found")
    command.status = payload.status
    command.result = payload.result
    command.executed_at = datetime.now(timezone.utc)
    await db.commit()
    return {"message": "Updated"}
EOF

# ── WebSocket handler ──────────────────────────────────────────────────────
echo "Creating websocket handler..."

cat > app/websocket/handler.py << 'EOF'
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
EOF

# ── Requirements ───────────────────────────────────────────────────────────
echo "Creating requirements files..."

cat > requirements.txt << 'EOF'
fastapi==0.115.0
uvicorn[standard]==0.30.6
sqlalchemy[asyncio]==2.0.35
asyncpg==0.29.0
pydantic==2.9.2
pydantic-settings==2.5.2
python-jose[cryptography]==3.3.0
passlib[bcrypt]==1.7.4
python-multipart==0.0.9
paramiko==3.4.0
httpx==0.27.2
websockets==13.0
EOF

cat > agent/requirements.txt << 'EOF'
websockets==13.0
httpx==0.27.2
EOF

# ── .env.example ───────────────────────────────────────────────────────────
echo "Creating .env.example..."

cat > .env.example << 'EOF'
DATABASE_URL=postgresql+asyncpg://fleet:fleet@localhost:5432/fleetmanager
SECRET_KEY=change-me-generate-with-openssl-rand-hex-32
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
SSH_KEY_PATH=/home/ubuntu/.ssh/id_rsa
EOF

# ── Move existing files into correct locations ──────────────────────────────
echo "Moving existing files..."

[ -f main.py ]          && mv main.py app/main.py          && echo "  moved main.py → app/main.py"
[ -f ssh_collector.py ] && mv ssh_collector.py app/services/ssh_collector.py && echo "  moved ssh_collector.py → app/services/ssh_collector.py"
[ -f agent.py ]         && mv agent.py agent/agent.py      && echo "  moved agent.py → agent/agent.py"

echo ""
echo "Done. Final structure:"
find . -not -path './.git/*' -not -path './venv/*' -not -name '*.pyc' -not -path './__pycache__/*' | sort

echo ""
echo "Next steps:"
echo "  1. cp .env.example .env  (then edit SECRET_KEY)"
echo "  2. python -m venv venv && source venv/bin/activate"
echo "  3. pip install -r requirements.txt"
echo "  4. uvicorn app.main:app --reload"
echo "  5. open http://localhost:8000/docs"
