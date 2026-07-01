from typing import Optional
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://fleet:fleet@localhost:5432/fleetmanager"
    secret_key: str = "change-me-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    ssh_key_path: Optional[str] = None

    class Config:
        env_file = ".env"


settings = Settings()
