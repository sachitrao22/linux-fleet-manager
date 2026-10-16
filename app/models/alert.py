from sqlalchemy import BigInteger, Column, Float, ForeignKey, Index, Integer, String, Uuid

from app.database import Base
from app.models.base import UTCDateTime, utcnow


class Alert(Base):
    """
    One row per incident, not per sample. While CPU stays above the threshold the
    same open alert is reused (deduplication); it resolves when the value drops back.
    """

    __tablename__ = "alerts"

    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_id = Column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)
    metric = Column(String, nullable=False)        # cpu_pct | mem_pct | disk_pct | offline
    severity = Column(String, nullable=False)      # warning | critical
    value = Column(Float)                          # latest value that kept it open
    threshold = Column(Float)
    status = Column(String, nullable=False, default="open")   # open | resolved
    opened_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    resolved_at = Column(UTCDateTime())

    __table_args__ = (Index("ix_alerts_device_metric_status", "device_id", "metric", "status"),)
