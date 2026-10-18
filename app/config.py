from typing import List, Literal, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """
    All configuration comes from environment variables or a .env file.
    `python launch.py` writes the .env for you on first run.
    """

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # SQLite by default so the server runs on any machine with zero setup.
    # For production use: postgresql+asyncpg://user:pass@host:5432/dbname
    # (a plain postgres:// URL from Neon/Render/Supabase works too — see database.py)
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

    # Delete metrics, finished commands and resolved alerts older than this (0 = keep forever)
    retention_hours: int = 0

    # Admin account created/updated at startup (required for the public demo, where
    # the "first visitor becomes admin" bootstrap is switched off)
    admin_username: Optional[str] = None
    admin_password: Optional[str] = None

    # Browsers on these origins may call the API (comma-separated, or *). Needed when the
    # dashboard is hosted somewhere else, e.g. https://your-project.vercel.app
    cors_origins: str = ""

    # Public URL of this API, used in install commands. Default: taken from the request.
    public_api_url: Optional[str] = None

    # ── Public demo ─────────────────────────────────────────────────────────
    # Anyone can look at the "showcase" devices (the owner's fleet) read-only and run
    # built-in read-only actions on them. Each visitor who clicks "Add my device" gets a
    # private sandbox (workspace) with its own agent token, which expires.
    demo_mode: bool = False
    demo_session_minutes: int = 60
    demo_max_devices: int = 3                 # per sandbox
    demo_max_total_devices: int = 100         # across all sandboxes
    demo_sessions_per_ip_per_hour: int = 6
    demo_commands_per_ip_per_minute: int = 20

    @property
    def accepts_agents(self) -> bool:
        return self.fleet_mode in ("agent", "hybrid")

    @property
    def accepts_agentless(self) -> bool:
        return self.fleet_mode in ("agentless", "hybrid")

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip().rstrip("/") for o in self.cors_origins.split(",") if o.strip()]


settings = Settings()
