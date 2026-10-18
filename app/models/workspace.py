import uuid

from sqlalchemy import Column, String, Uuid

from app.database import Base
from app.models.base import UTCDateTime, utcnow


class Workspace(Base):
    """
    A private sandbox in the public demo. Each visitor who clicks "Add my device" gets one,
    with its own agent token. Devices registered with that token belong to it, only that
    visitor can see them or act on them, and everything is deleted when it expires.

    Devices with workspace_id = NULL are the owner's fleet (the self-hosted default, and
    the read-only "showcase" in the public demo).
    """

    __tablename__ = "workspaces"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    agent_token = Column(String, nullable=False, unique=True, index=True)
    created_at = Column(UTCDateTime(), nullable=False, default=utcnow)
    expires_at = Column(UTCDateTime(), nullable=False)
