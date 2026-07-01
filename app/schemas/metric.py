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
