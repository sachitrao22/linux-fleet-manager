import uuid

from sqlalchemy import Column, String, Uuid

from app.database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Uuid, primary_key=True, default=uuid.uuid4)
    username = Column(String, unique=True, nullable=False)
    hashed_password = Column(String, nullable=False)   # bcrypt hash, never the password
    role = Column(String, nullable=False, default="viewer")   # admin | viewer
