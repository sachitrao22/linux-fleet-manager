"""Public demo mode: what anonymous visitors and sandboxed visitors can and can't do."""
import time
from datetime import timedelta

import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.models.base import utcnow
from app.routers import commands as commands_router
from app.routers import demo as demo_router
from app.services import poller
from conftest import AGENT_HEADERS

SHOWCASE_ID = "6f1f5c2a-0d4e-4c8b-9a51-3b7e2f9d1c00"


@pytest.fixture
def demo(client, admin_headers):
    """Turn demo mode on for one test (after the shared admin account exists)."""
    saved = (settings.demo_mode, settings.fleet_mode, settings.demo_max_devices)
    settings.demo_mode, settings.fleet_mode = True, "agent"
    demo_router.session_limiter.hits.clear()
    commands_router.demo_command_limiter.hits.clear()
    # The owner's showcase device, with a pinned id
    r = client.post("/devices/register", headers=AGENT_HEADERS,
                    json={"device_id": SHOWCASE_ID, "hostname": "live-demo-server"})
    assert r.status_code == 200 and r.json()["id"] == SHOWCASE_ID
    yield client
    settings.demo_mode, settings.fleet_mode, settings.demo_max_devices = saved


def new_session(client):
    r = client.post("/demo/sessions")
    assert r.status_code == 201, r.text
    s = r.json()
    s["headers"] = {"Authorization": "Bearer " + s["session_token"]}
    return s


def add_device(client, session, name):
    r = client.post("/devices/register", headers={"X-Agent-Token": session["agent_token"]}, json={"hostname": name})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def visible(client, headers=None):
    return {d["id"] for d in client.get("/devices", headers=headers or {}).json()}


def test_anonymous_sees_showcase_only_and_cannot_become_admin(demo):
    a = new_session(demo)
    mine = add_device(demo, a, "visitor-laptop")
    anon = visible(demo)
    assert SHOWCASE_ID in anon and mine not in anon
    assert demo.get("/auth/whoami").json()["kind"] == "anonymous"
    assert demo.get("/auth/status").json() == {"setup_required": False}
    assert demo.post("/auth/register", json={"username": "visitor", "password": "password123"}).status_code == 403


def test_sandboxes_are_isolated(demo):
    a, b = new_session(demo), new_session(demo)
    a_dev = add_device(demo, a, "a-laptop")
    assert a_dev in visible(demo, a["headers"]) and SHOWCASE_ID in visible(demo, a["headers"])
    assert a_dev not in visible(demo, b["headers"])
    assert demo.get(f"/devices/{a_dev}", headers=b["headers"]).status_code == 404
    assert demo.get(f"/metrics/{a_dev}", headers=b["headers"]).status_code == 404
    assert demo.post(f"/commands/{a_dev}", headers=b["headers"], json={"command": "@uptime"}).status_code == 404
    assert demo.delete(f"/devices/{a_dev}", headers=b["headers"]).status_code == 404
    assert demo.delete(f"/devices/{SHOWCASE_ID}", headers=a["headers"]).status_code == 403
    assert demo.delete(f"/devices/{a_dev}", headers=a["headers"]).status_code == 204


def test_only_builtin_actions_from_visitors_and_never_shell_into_sandboxes(demo, admin_headers):
    a = new_session(demo)
    a_dev = add_device(demo, a, "a-laptop")
    assert demo.post(f"/commands/{SHOWCASE_ID}", json={"command": "@uptime"}).status_code == 201
    assert demo.post(f"/commands/{SHOWCASE_ID}", json={"command": "rm -rf /"}).status_code == 403
    assert demo.post(f"/commands/{a_dev}", headers=a["headers"], json={"command": "whoami"}).status_code == 403
    assert demo.post(f"/commands/{a_dev}", headers=a["headers"], json={"command": "@top"}).status_code == 201
    # Even the admin can't run shell on a visitor's machine
    assert demo.post(f"/commands/{a_dev}", headers=admin_headers, json={"command": "whoami"}).status_code == 403
    # The list of built-in actions is public
    assert "@uptime" in [x["command"] for x in demo.get("/commands").json()]


def test_sandbox_token_cannot_take_over_or_connect_as_other_devices(demo):
    a = new_session(demo)
    r = demo.post("/devices/register", headers={"X-Agent-Token": a["agent_token"]},
                  json={"device_id": SHOWCASE_ID, "hostname": "hijack"}).json()
    assert r["id"] != SHOWCASE_ID
    assert demo.get(f"/devices/{SHOWCASE_ID}").json()["hostname"] == "live-demo-server"
    with pytest.raises(WebSocketDisconnect) as exc:
        with demo.websocket_connect(f"/ws/{SHOWCASE_ID}", headers={"X-Agent-Token": a["agent_token"]}) as ws:
            ws.receive_text()
    assert exc.value.code == 4401


def test_device_limit_per_sandbox(demo):
    settings.demo_max_devices = 2
    a = new_session(demo)
    add_device(demo, a, "one"), add_device(demo, a, "two")
    r = demo.post("/devices/register", headers={"X-Agent-Token": a["agent_token"]}, json={"hostname": "three"})
    assert r.status_code == 429


def test_expired_sandbox_is_refused_then_deleted(demo):
    from app.database import AsyncSessionLocal
    from app.models import Workspace

    a = new_session(demo)
    a_dev = add_device(demo, a, "expiring-laptop")

    async def expire():
        async with AsyncSessionLocal() as db:
            ws = await db.get(Workspace, __import__("uuid").UUID(a["workspace_id"]))
            ws.expires_at = utcnow() - timedelta(minutes=1)
            await db.commit()

    demo.portal.call(expire)
    r = demo.post("/devices/register", headers={"X-Agent-Token": a["agent_token"]}, json={"hostname": "again"})
    assert r.status_code == 410
    assert demo.get("/devices", headers=a["headers"]).status_code == 401      # session over
    demo.portal.call(poller.cleanup_once)
    assert demo.get(f"/devices/{a_dev}", headers=a["headers"]).status_code == 401
    assert a_dev not in visible(demo)


def test_session_rate_limit(demo):
    for _ in range(settings.demo_sessions_per_ip_per_hour):
        assert demo.post("/demo/sessions").status_code == 201
    assert demo.post("/demo/sessions").status_code == 429


def test_installers_and_agent_are_served_with_this_api_url(demo):
    sh = demo.get("/install.sh").text
    assert "__API__" not in sh and 'API="http://testserver"' in sh and "--safe" in sh
    ps = demo.get("/install.ps1").text
    assert "__API__" not in ps and "--safe" in ps
    assert "ACTIONS = {" in demo.get("/agent.py").text
    s = new_session(demo)
    assert s["install"]["unix"].endswith(s["agent_token"])
    assert s["agent_token"] in s["install"]["windows"]


def test_admin_account_comes_from_settings(client):
    saved = (settings.admin_username, settings.admin_password)
    settings.admin_username, settings.admin_password = "owner-from-env", "env-password-123"
    try:
        client.portal.call(poller.ensure_admin_from_settings)
        r = client.post("/auth/token", data={"username": "owner-from-env", "password": "env-password-123"})
        assert r.status_code == 200
        me = client.get("/auth/whoami", headers={"Authorization": "Bearer " + r.json()["access_token"]}).json()
        assert me["kind"] == "admin"
    finally:
        settings.admin_username, settings.admin_password = saved


def test_demo_endpoints_are_off_outside_demo_mode(client):
    assert client.post("/demo/sessions").status_code == 404
    assert client.get("/devices").status_code == 401


def test_neon_style_database_url_is_normalized():
    from app.database import normalize_database_url
    url, args = normalize_database_url(
        "postgres://u:p@ep-cool-123-pooler.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require")
    assert url.drivername == "postgresql+asyncpg"
    assert "sslmode" not in url.query and "channel_binding" not in url.query
    assert args["ssl"] == "require" and args["statement_cache_size"] == 0
    url2, args2 = normalize_database_url("sqlite+aiosqlite:///./fleet.db")
    assert url2.drivername == "sqlite+aiosqlite" and args2 == {}
