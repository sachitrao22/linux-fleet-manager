from typing import Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration comes from environment variables or a .env file.
    `python launch.py` writes the .env for you on first run.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the server runs on any machine with zero setup.
    # For production use: postgresql+asyncpg://user:pass@host:5432/dbname
    database_url: str = "sqlite+aiosqlite:///./fleet.db"

    # JWT signing key. launch.py generates a random one.
    secret_key: str = "dev-insecure-change-me"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440

    # Which monitoring modes this control plane accepts:
    #   agentless — server SSHes into hosts (nothing installed on targets)
    #   agent     — devices run the agent and push over WebSocket
    #   hybrid    — both at once
    fleet_mode: Literal["agent", "agentless", "hybrid"] = "hybrid"

    # Shared secret agents must present to register / connect. None = open (dev only).
    agent_token: Optional[str] = None

    # Agentless SSH settings
    ssh_key_path: Optional[str] = None      # None = use ssh-agent / ~/.ssh default keys
    ssh_strict_host_keys: bool = False      # True = reject hosts not in known_hosts (prod)
    ssh_poll_interval: int = 30             # seconds between collections
    ssh_max_concurrency: int = 20           # parallel SSH sessions
    hosts_file: Optional[str] = None        # optional hosts.yaml imported at startup

    # A device that hasn't reported for this long is marked offline
    offline_after_seconds: int = 90

    # Alert thresholds (percent)
    cpu_warn: float = 80
    cpu_crit: float = 95
    mem_warn: float = 85
    mem_crit: float = 95
    disk_warn: float = 85
    disk_crit: float = 95

    @property
    def accepts_agents(self) -> bool:
        return self.fleet_mode in ("agent", "hybrid")

    @property
    def accepts_agentless(self) -> bool:
        return self.fleet_mode in ("agentless", "hybrid")


settings = Settings()
