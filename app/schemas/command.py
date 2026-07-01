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
