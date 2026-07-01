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
