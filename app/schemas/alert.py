import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class AlertResponse(BaseModel):
    id: int
    device_id: uuid.UUID
    metric: str
    severity: str
    value: Optional[float]
    threshold: Optional[float]
    status: str
    opened_at: datetime
    resolved_at: Optional[datetime]

    model_config = {"from_attributes": True}
