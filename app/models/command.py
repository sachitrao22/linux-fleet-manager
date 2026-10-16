import uuid

from sqlalchemy import Column, ForeignKey, Integer, String, Text, Uuid

from app.database import Base
from app.models.base import UTCDateTime, utcnow


class Command(Base):
    """
    Durable command queue, backed by the database.

    Lifecycle:  pending ──► delivered ──► executed | failed
       pending   – created; device offline or not yet sent
       delivered – handed to the agent / SSH; result not back yet
       executed  – exit code 0        failed – non-zero exit, timeout or SSH error

    Delivery is AT-LEAST-ONCE: on reconnect the server re-sends pending AND delivered
    commands. The agent remembers command ids it already ran and replays the stored
    result instead of running twice (idempotent consumer).
    """

    __tablename__ = "commands"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id = Column(Uuid, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True)
    command = Column(Text, nullable=False)
    status = Column(String, nullable=False, default="pending", index=True)

    # Client-supplied. Retrying the same API request with the same key won't queue it twice.
    idempotency_key = Column(String, unique=True)

    exit_code = Column(Integer)
    result = Column(Text)
    created_by = Column(String)    # username — audit trail for remote execution

    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    delivered_at = Column(UTCDateTime())
    executed_at = Column(UTCDateTime())
