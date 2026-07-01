#!/usr/bin/env python3
"""
Fleet Manager Agent
Runs as a daemon on any Linux device. Reads /proc metrics and pushes
to the control plane via WebSocket. Handles disconnects with auto-reconnect
and polls for pending commands on each reconnect.

Usage:
  python agent.py --server ws://your-server:8000 --http http://your-server:8000

Install as systemd service: see SETUP.md
"""
import argparse
import asyncio
import json
import logging
import platform
import socket
import subprocess
import time
import uuid
from pathlib import Path
from typing import Tuple

import httpx
import websockets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [agent] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("fleet-agent")


# ── Metric collection from /proc ─────────────────────────────────────────────

def _read(path: str) -> str:
    try:
        return Path(path).read_text().strip()
    except OSError:
        return ""


def get_cpu_percent() -> float:
    """Two-sample diff against /proc/stat for accurate CPU usage."""
    def read_stat():
        line = [l for l in _read("/proc/stat").splitlines() if l.startswith("cpu ")][0]
        vals = [int(x) for x in line.split()[1:]]
        return sum(vals), vals[3]  # total, idle

    total1, idle1 = read_stat()
    time.sleep(0.2)
    total2, idle2 = read_stat()
    delta_total = total2 - total1 or 1
    return round((1 - (idle2 - idle1) / delta_total) * 100, 2)


def get_mem_percent() -> float:
    data = {}
    for line in _read("/proc/meminfo").splitlines():
        k, _, v = line.partition(":")
        try:
            data[k.strip()] = int(v.strip().split()[0])
        except (ValueError, IndexError):
            pass
    total = data.get("MemTotal", 1)
    available = data.get("MemAvailable", 0)
    return round((total - available) / total * 100, 2)


def get_disk_percent() -> float:
    try:
        out = subprocess.run(
            ["df", "/", "--output=pcent"], capture_output=True, text=True, timeout=5
        ).stdout
        return float(out.splitlines()[-1].strip().rstrip("%"))
    except Exception:
        return 0.0


def get_network_kb() -> Tuple[float, float]:
    lines = _read("/proc/net/dev").splitlines()[2:]
    rx_kb = sum(int(l.split()[1]) for l in lines if l.strip()) / 1024
    tx_kb = sum(int(l.split()[9]) for l in lines if l.strip()) / 1024
    return round(rx_kb, 2), round(tx_kb, 2)


def get_load_avg() -> Tuple[float, float, float]:
    parts = _read("/proc/loadavg").split()
    return float(parts[0]), float(parts[1]), float(parts[2])


def collect_metrics() -> dict:
    net_in, net_out = get_network_kb()
    load_1, load_5, load_15 = get_load_avg()
    return {
        "type": "metrics",
        "cpu_pct": get_cpu_percent(),
        "mem_pct": get_mem_percent(),
        "disk_pct": get_disk_percent(),
        "net_in_kb": net_in,
        "net_out_kb": net_out,
        "load_1m": load_1,
        "load_5m": load_5,
        "load_15m": load_15,
    }


# ── Command execution ─────────────────────────────────────────────────────────

def run_shell(cmd: str) -> str:
    try:
        result = subprocess.run(
            cmd, shell=True, capture_output=True, text=True, timeout=30
        )
        return result.stdout or result.stderr or "(no output)"
    except subprocess.TimeoutExpired:
        return "ERROR: command timed out after 30s"
    except Exception as e:
        return f"ERROR: {e}"


# ── Registration & main loop ──────────────────────────────────────────────────

async def register(http_url: str) -> str:
    """Register this device with the control plane. Returns device_id (UUID)."""
    payload = {
        "hostname": socket.gethostname(),
        "ip_address": socket.gethostbyname(socket.gethostname()),
        "os": platform.system(),
        "distro": platform.version()[:80],
        "arch": platform.machine(),
        "mode": "agent",
    }
    async with httpx.AsyncClient(timeout=10) as client:
        resp = await client.post(f"{http_url}/devices/register", json=payload)
        resp.raise_for_status()
        data = resp.json()
        log.info(f"Registered as '{data['hostname']}' (id={data['id']})")
        return data["id"]


async def run(ws_url: str, http_url: str, interval: int):
    device_id = await register(http_url)
    full_ws = f"{ws_url}/ws/{device_id}"

    while True:
        try:
            log.info(f"Connecting to {full_ws} ...")
            async with websockets.connect(full_ws, ping_interval=20, ping_timeout=10) as ws:
                log.info("Connected. Streaming metrics.")

                while True:
                    # Push metrics
                    metrics = collect_metrics()
                    await ws.send(json.dumps(metrics))

                    # Non-blocking check for incoming commands
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=0.5)
                        msg = json.loads(raw)
                        if msg.get("type") == "command":
                            cmd_text = msg["command"]
                            cmd_id = msg["command_id"]
                            log.info(f"Running command [{cmd_id[:8]}]: {cmd_text}")
                            output = run_shell(cmd_text)
                            await ws.send(json.dumps({
                                "type": "command_result",
                                "command_id": cmd_id,
                                "status": "executed",
                                "result": output,
                            }))
                    except asyncio.TimeoutError:
                        pass  # no message waiting, that's fine

                    await asyncio.sleep(interval)

        except (OSError, websockets.exceptions.WebSocketException) as e:
            log.warning(f"Connection lost ({e}). Retrying in 5s...")
            await asyncio.sleep(5)
        except Exception as e:
            log.error(f"Unexpected error: {e}. Retrying in 10s...")
            await asyncio.sleep(10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fleet Manager Agent")
    parser.add_argument("--server", default="ws://localhost:8000", help="WebSocket server base URL")
    parser.add_argument("--http",   default="http://localhost:8000", help="HTTP server base URL")
    parser.add_argument("--interval", type=int, default=10, help="Metric push interval in seconds")
    args = parser.parse_args()

    asyncio.run(run(args.server, args.http, args.interval))
