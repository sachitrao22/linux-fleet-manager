import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class CommandCreate(BaseModel):
    command: str = Field(min_length=1, max_length=4096)
    idempotency_key: Optional[str] = Field(default=None, max_length=128)


class CommandResponse(BaseModel):
    id: uuid.UUID
    device_id: uuid.UUID
    command: str
    status: str
    exit_code: Optional[int]
    result: Optional[str]
    created_by: Optional[str]
    created_at: datetime
    delivered_at: Optional[datetime]
    executed_at: Optional[datetime]

    model_config = {"from_attributes": True}
