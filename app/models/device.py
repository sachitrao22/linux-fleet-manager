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
