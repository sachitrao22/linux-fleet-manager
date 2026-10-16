from sqlalchemy import BigInteger, Column, Float, ForeignKey, Index, Integer, Uuid

from app.database import Base
from app.models.base import UTCDateTime, utcnow


class Metric(Base):
    """
    Wide-row schema: ONE row per device per sample, every metric a column.
    A key-value layout (device, metric_name, value) would write 8 rows per sample.
    """

    __tablename__ = "metrics"

    # BIGINT on Postgres (billions of rows); SQLite only auto-increments INTEGER.
    id = Column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True, autoincrement=True)
    device_id = Column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False)

    # When the sample was TAKEN (sent by the agent), not when it arrived.
    # Matters for samples buffered while the agent was offline.
    ts = Column(UTCDateTime(), nullable=False, default=utcnow)

    cpu_pct = Column(Float)
    mem_pct = Column(Float)
    disk_pct = Column(Float)
    net_in_kbps = Column(Float)    # KB/s received, averaged over the sample interval
    net_out_kbps = Column(Float)   # KB/s sent
    load_1m = Column(Float)        # None where the OS has no load average
    load_5m = Column(Float)
    load_15m = Column(Float)

    __table_args__ = (
        # Serves both hot queries: "history for device X" and "latest per device".
        # A B-tree can be scanned in either direction, so no separate DESC index is needed.
        Index("ix_metrics_device_ts", "device_id", "ts"),
    )
