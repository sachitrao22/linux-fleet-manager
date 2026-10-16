from typing import Literal

from pydantic import BaseModel, Field


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64)
    # bcrypt only uses the first 72 bytes, so longer passwords are rejected up front
    password: str = Field(min_length=8, max_length=72)
    role: Literal["admin", "viewer"] = "viewer"


class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


class UserResponse(BaseModel):
    username: str
    role: str

    model_config = {"from_attributes": True}
