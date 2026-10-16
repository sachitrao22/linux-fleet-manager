import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field

from app.schemas.metric import MetricResponse


class AgentRegister(BaseModel):
    """Sent by the agent on every (re)connect."""

    device_id: Optional[uuid.UUID] = None   # the id the agent saved last time, if any
    hostname: str = Field(min_length=1, max_length=255)
    ip_address: Optional[str] = None
    os: Optional[str] = None
    distro: Optional[str] = None
    arch: Optional[str] = None
    tags: Dict[str, Any] = Field(default_factory=dict)


class AgentlessCreate(BaseModel):
    """Added by an admin. Nothing is installed on the host — the server SSHes in."""

    hostname: str = Field(min_length=1, max_length=255)
    ip_address: str
    ssh_user: str
    ssh_port: int = Field(default=22, ge=1, le=65535)
    tags: Dict[str, Any] = Field(default_factory=dict)


class DeviceResponse(BaseModel):
    id: uuid.UUID
    hostname: str
    ip_address: Optional[str]
    os: Optional[str]
    distro: Optional[str]
    arch: Optional[str]
    mode: str
    ssh_user: Optional[str]
    ssh_port: Optional[int]
    status: str
    last_seen: Optional[datetime]
    registered_at: datetime
    tags: Optional[Dict[str, Any]]

    model_config = {"from_attributes": True}


class DeviceOverview(DeviceResponse):
    latest: Optional[MetricResponse] = None
    open_alerts: int = 0
