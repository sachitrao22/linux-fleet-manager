import os
import sys
import tempfile
from pathlib import Path

import pytest

# Configure BEFORE the app is imported (settings are read at import time).
_tmp = tempfile.mkdtemp(prefix="fleet-test-")
os.environ.update({
    # Default: throwaway SQLite. Set TEST_DATABASE_URL to run the same suite on Postgres.
    "DATABASE_URL": os.environ.get("TEST_DATABASE_URL", f"sqlite+aiosqlite:///{_tmp}/test.db"),
    "SECRET_KEY": "test-secret-key-that-is-at-least-32-bytes",
    "AGENT_TOKEN": "test-agent-token",
    "FLEET_MODE": "hybrid",
    "SSH_POLL_INTERVAL": "3600",
})
os.chdir(Path(__file__).resolve().parent)   # keep the repo's own .env out of tests
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402

AGENT_HEADERS = {"X-Agent-Token": "test-agent-token"}


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client):
    r = client.post("/auth/register", json={"username": "admin", "password": "password123"})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "admin"
    tok = client.post("/auth/token", data={"username": "admin", "password": "password123"}).json()
    return {"Authorization": f"Bearer {tok['access_token']}"}


@pytest.fixture
def agent_device(client):
    def make(hostname="test-host"):
        r = client.post("/devices/register", headers=AGENT_HEADERS,
                        json={"hostname": hostname, "os": "Linux", "arch": "x86_64"})
        assert r.status_code == 200, r.text
        return r.json()["id"]
    return make
