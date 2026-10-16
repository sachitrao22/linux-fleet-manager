import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MetricPayload(BaseModel):
    """One sample. Validated before it touches the database."""

    ts: Optional[datetime] = None   # when the sample was taken; server time if omitted
    cpu_pct: Optional[float] = Field(default=None, ge=0, le=100)
    mem_pct: Optional[float] = Field(default=None, ge=0, le=100)
    disk_pct: Optional[float] = Field(default=None, ge=0, le=100)
    net_in_kbps: Optional[float] = Field(default=None, ge=0)
    net_out_kbps: Optional[float] = Field(default=None, ge=0)
    load_1m: Optional[float] = Field(default=None, ge=0)
    load_5m: Optional[float] = Field(default=None, ge=0)
    load_15m: Optional[float] = Field(default=None, ge=0)


class MetricResponse(MetricPayload):
    device_id: uuid.UUID
    ts: datetime

    model_config = {"from_attributes": True}
