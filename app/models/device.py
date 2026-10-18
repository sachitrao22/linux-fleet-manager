import uuid

from sqlalchemy import JSON, Column, ForeignKey, Integer, String, Uuid

from app.database import Base
from app.models.base import UTCDateTime, utcnow


class Device(Base):
    __tablename__ = "devices"

    # UUID, not 1,2,3… — IDs can't be guessed. Generic Uuid type works on SQLite and Postgres.
    id = Column(Uuid, primary_key=True, default=uuid.uuid4)

    # Not unique: two machines can share a hostname. Identity is the id, which the
    # agent persists locally and sends back when it re-registers.
    hostname = Column(String, nullable=False, index=True)
    ip_address = Column(String)
    os = Column(String)        # "Linux", "Darwin", "Windows"
    distro = Column(String)    # "Ubuntu 22.04.4 LTS", "macOS 15.1", "Windows 11"
    arch = Column(String)      # "x86_64", "arm64"

    # "agent" = device pushes over WebSocket | "agentless" = server pulls over SSH
    mode = Column(String, nullable=False, default="agent")

    # Agentless only. No passwords are stored — SSH keys / ssh-agent only.
    ssh_user = Column(String)
    ssh_port = Column(Integer, default=22)

    # online | offline | unreachable
    status = Column(String, nullable=False, default="offline")
    last_seen = Column(UTCDateTime())
    registered_at = Column(UTCDateTime(), nullable=False, default=utcnow)

    # NULL = the owner's fleet. Set = belongs to a visitor's demo sandbox (deleted with it).
    workspace_id = Column(Uuid, ForeignKey("workspaces.id", ondelete="CASCADE"), nullable=True, index=True)

    # Free-form labels, e.g. {"env": "prod", "team": "infra"} — no schema change per label
    tags = Column(JSON, default=dict)
