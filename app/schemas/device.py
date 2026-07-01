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
