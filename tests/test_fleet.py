import subprocess
import time
from datetime import datetime, timedelta, timezone

import pytest
from starlette.websockets import WebSocketDisconnect

from app.config import settings
from app.services.ssh_collector import METRICS_SCRIPT, parse_output
from conftest import AGENT_HEADERS

TOKEN_Q = "?token=test-agent-token"   # tests use the query fallback; the real agent sends a header


def wait_until(fn, timeout=5.0):
    """WebSocket sends are asynchronous; poll the API until the server has caught up."""
    deadline = time.time() + timeout
    while True:
        result = fn()
        if result or time.time() > deadline:
            return result
        time.sleep(0.05)


def ws_url(device_id):
    return f"/ws/{device_id}{TOKEN_Q}"


# ── auth ─────────────────────────────────────────────────────────────────────

def test_first_user_is_admin_then_registration_is_locked(client, admin_headers):
    # Anyone trying to self-register after bootstrap is refused
    r = client.post("/auth/register", json={"username": "mallory", "password": "password123", "role": "admin"})
    assert r.status_code == 403
    # An admin can create a viewer, and a viewer cannot run commands
    r = client.post("/auth/register", headers=admin_headers,
                    json={"username": "viewer1", "password": "password123", "role": "viewer"})
    assert r.status_code == 201
    tok = client.post("/auth/token", data={"username": "viewer1", "password": "password123"}).json()["access_token"]
    viewer = {"Authorization": f"Bearer {tok}"}
    assert client.get("/devices", headers=viewer).status_code == 200
    r = client.post("/devices/agentless", headers=viewer,
                    json={"hostname": "x", "ip_address": "10.0.0.1", "ssh_user": "u"})
    assert r.status_code == 403


def test_wrong_password_and_no_token(client, admin_headers):
    assert client.post("/auth/token", data={"username": "admin", "password": "nope-nope"}).status_code == 401
    assert client.get("/devices").status_code == 401


# ── agent registration & tokens ──────────────────────────────────────────────

def test_agent_token_required(client):
    r = client.post("/devices/register", json={"hostname": "intruder"})
    assert r.status_code == 401
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/00000000-0000-0000-0000-000000000000?token=wrong") as ws:
            ws.receive_text()


def test_ws_accepts_token_header(client, agent_device):
    dev = agent_device("header-host")
    with client.websocket_connect(f"/ws/{dev}", headers=AGENT_HEADERS) as ws:
        ws.send_json({"type": "metrics", "cpu_pct": 1})


def test_reregister_keeps_identity_even_with_same_hostname(client, agent_device):
    first = agent_device("dup-name")
    # Same machine re-registers with its saved id → same row
    r = client.post("/devices/register", headers=AGENT_HEADERS, json={"device_id": first, "hostname": "dup-name"})
    assert r.json()["id"] == first
    # A different machine with the same hostname → a different device
    assert agent_device("dup-name") != first


# ── metrics over WebSocket ───────────────────────────────────────────────────

def test_metrics_flow_and_overview(client, admin_headers, agent_device):
    dev = agent_device("metrics-host")
    with client.websocket_connect(ws_url(dev)) as ws:
        ws.send_json({"type": "metrics", "cpu_pct": 12.5, "mem_pct": 40, "disk_pct": 30, "load_1m": 0.4})
        ws.send_json({"type": "metrics", "cpu_pct": 200})   # invalid (>100) → ignored, connection survives
        ws.send_text("not json")                            # garbage → ignored
        ws.send_json({"type": "metrics", "cpu_pct": 14.0, "mem_pct": 41, "disk_pct": 30})
        assert wait_until(lambda: len(client.get(f"/metrics/{dev}", headers=admin_headers).json()) == 2)
        assert client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "online"
    assert wait_until(lambda: client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "offline")
    hist = client.get(f"/metrics/{dev}", headers=admin_headers).json()
    assert [h["cpu_pct"] for h in hist] == [12.5, 14.0]
    overview = {d["id"]: d for d in client.get("/devices", headers=admin_headers).json()}
    assert overview[dev]["latest"]["cpu_pct"] == 14.0
    assert overview[dev]["status"] == "offline"   # socket closed


def test_buffered_batch_keeps_original_timestamps(client, admin_headers, agent_device):
    dev = agent_device("batch-host")
    base = datetime.now(timezone.utc) - timedelta(minutes=10)
    samples = [{"ts": (base + timedelta(seconds=10 * i)).isoformat(), "cpu_pct": float(i)} for i in range(5)]
    with client.websocket_connect(ws_url(dev)) as ws:
        ws.send_json({"type": "metrics_batch", "samples": samples})
        hist = wait_until(lambda: (lambda h: h if len(h) == 5 else None)(
            client.get(f"/metrics/{dev}?minutes=30", headers=admin_headers).json()))
    assert len(hist) == 5
    got = datetime.fromisoformat(hist[0]["ts"].replace("Z", "+00:00"))
    assert abs((got - base).total_seconds()) < 1


# ── alerts ───────────────────────────────────────────────────────────────────

def test_alert_opens_dedups_escalates_and_resolves(client, admin_headers, agent_device):
    dev = agent_device("alert-host")

    def open_alerts():
        return [a for a in client.get(f"/alerts?device_id={dev}", headers=admin_headers).json() if a["metric"] == "cpu_pct"]

    with client.websocket_connect(ws_url(dev)) as ws:
        ws.send_json({"type": "metrics", "cpu_pct": 85})
        ws.send_json({"type": "metrics", "cpu_pct": 88})
        assert wait_until(lambda: (open_alerts() or [{}])[0].get("value") == 88)
        alerts = open_alerts()
        assert len(alerts) == 1 and alerts[0]["severity"] == "warning"     # deduplicated
        ws.send_json({"type": "metrics", "cpu_pct": 99})
        assert wait_until(lambda: open_alerts()[0]["severity"] == "critical")   # escalated, same alert
        assert len(open_alerts()) == 1
        ws.send_json({"type": "metrics", "cpu_pct": 10})
        assert wait_until(lambda: open_alerts() == [])                     # resolved
    assert wait_until(lambda: any(a["metric"] == "offline" for a in
                      client.get(f"/alerts?device_id={dev}", headers=admin_headers).json()))  # disconnect raised one


# ── commands: offline queue, at-least-once redelivery, idempotency ──────────

def test_offline_command_queue_and_redelivery(client, admin_headers, agent_device):
    dev = agent_device("cmd-host")

    # 1. Device offline → command is persisted as pending
    cmd = client.post(f"/commands/{dev}", headers=admin_headers, json={"command": "uptime"}).json()
    assert cmd["status"] == "pending"

    # 2. Agent connects → receives it immediately; it disconnects before answering
    with client.websocket_connect(ws_url(dev)) as ws:
        msg = ws.receive_json()
        assert msg == {"type": "command", "command_id": cmd["id"], "command": "uptime"}
    assert wait_until(lambda: client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "offline")
    assert client.get(f"/commands/{dev}", headers=admin_headers).json()[0]["status"] == "delivered"

    # 3. Reconnect → delivered-but-unanswered commands are sent AGAIN (at-least-once)
    with client.websocket_connect(ws_url(dev)) as ws:
        again = ws.receive_json()
        assert again["command_id"] == cmd["id"]
        ws.send_json({"type": "command_result", "command_id": cmd["id"], "exit_code": 0, "result": "up 3 days"})
        assert wait_until(lambda: client.get(f"/commands/{dev}", headers=admin_headers).json()[0]["status"] == "executed")
    final = client.get(f"/commands/{dev}", headers=admin_headers).json()[0]
    assert final["status"] == "executed" and final["result"] == "up 3 days"
    assert final["created_by"] == "admin"

    # 4. Executed commands are not re-sent: after reconnecting, the first thing the agent
    #    receives is the NEW command, not the old one.
    assert wait_until(lambda: client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "offline")
    with client.websocket_connect(ws_url(dev)) as ws:
        assert wait_until(lambda: client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "online")
        new = client.post(f"/commands/{dev}", headers=admin_headers, json={"command": "hostname"}).json()
        assert ws.receive_json()["command_id"] == new["id"]


def test_live_command_and_failure_status(client, admin_headers, agent_device):
    dev = agent_device("live-host")
    with client.websocket_connect(ws_url(dev)) as ws:
        assert wait_until(lambda: client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "online")
        cmd = client.post(f"/commands/{dev}", headers=admin_headers, json={"command": "false"}).json()
        assert cmd["status"] == "delivered"
        assert ws.receive_json()["command_id"] == cmd["id"]
        ws.send_json({"type": "command_result", "command_id": cmd["id"], "exit_code": 1, "result": ""})
        assert wait_until(lambda: client.get(f"/commands/{dev}", headers=admin_headers).json()[0]["status"] == "failed")


def test_idempotency_key_prevents_duplicates(client, admin_headers, agent_device):
    dev = agent_device("idem-host")
    a = client.post(f"/commands/{dev}", headers=admin_headers, json={"command": "reboot", "idempotency_key": "k-1"}).json()
    b = client.post(f"/commands/{dev}", headers=admin_headers, json={"command": "reboot", "idempotency_key": "k-1"}).json()
    assert a["id"] == b["id"]
    assert len(client.get(f"/commands/{dev}", headers=admin_headers).json()) == 1


def test_only_admin_runs_commands(client, agent_device):
    dev = agent_device("rbac-host")
    assert client.post(f"/commands/{dev}", json={"command": "id"}).status_code == 401


# ── modes ────────────────────────────────────────────────────────────────────

def test_mode_gating(client, admin_headers):
    original = settings.fleet_mode
    try:
        settings.fleet_mode = "agentless"
        assert client.post("/devices/register", headers=AGENT_HEADERS, json={"hostname": "h"}).status_code == 403
        settings.fleet_mode = "agent"
        r = client.post("/devices/agentless", headers=admin_headers,
                        json={"hostname": "h", "ip_address": "10.0.0.9", "ssh_user": "u"})
        assert r.status_code == 403
    finally:
        settings.fleet_mode = original


def test_agentless_device_unreachable_is_marked(client, admin_headers):
    from app.services.poller import poll_agentless_once

    r = client.post("/devices/agentless", headers=admin_headers,
                    json={"hostname": "ghost", "ip_address": "127.0.0.1", "ssh_user": "nobody", "ssh_port": 1})
    assert r.status_code == 201
    dev = r.json()["id"]
    client.portal.call(poll_agentless_once)   # run on the app's own event loop
    assert client.get(f"/devices/{dev}", headers=admin_headers).json()["status"] == "unreachable"


# ── the agentless shell script, run locally ──────────────────────────────────

def test_metrics_script_runs_and_parses_on_this_machine():
    out = subprocess.run(["sh", "-s"], input=METRICS_SCRIPT, capture_output=True, text=True, timeout=15).stdout
    metrics, info = parse_output(out)
    assert info["os"] in ("Linux", "Darwin")
    for key in ("cpu_pct", "mem_pct", "disk_pct", "load_1m"):
        assert key in metrics, out
        assert metrics[key] >= 0
    assert 0 <= metrics["cpu_pct"] <= 100


def test_parse_output_rejects_garbage():
    metrics, info = parse_output("cpu_pct=abc\nmem_pct=250\nrandom line\nos=Linux\nevil=1")
    assert metrics == {"mem_pct": 100.0}
    assert info == {"os": "Linux"}
