#!/usr/bin/env python3
"""
Fleet Manager agent — runs on any Linux, macOS or Windows machine.

  python agent.py --server http://CONTROL_PLANE:8000 --token AGENT_TOKEN
  python agent.py --once          # print one sample and exit (sanity check)

What it does:
  * Registers with the control plane and saves its device id in ~/.fleet-agent/
    (so a restart keeps the same identity even if two machines share a hostname).
  * A sampler task collects metrics every --interval seconds into a bounded buffer,
    connected or not. While online the buffer drains immediately; while offline it
    fills up (oldest samples dropped when full) and is flushed as one batch on reconnect.
  * Receives commands over the same WebSocket, runs them one at a time with a
    timeout, and sends back exit code + output. Command ids already executed are
    remembered, so a re-delivered command returns its stored result instead of
    running twice (the server delivers at-least-once).
  * Reconnects with exponential backoff + jitter.

Metrics come from psutil, which reads /proc on Linux, sysctl/Mach APIs on macOS
and Win32 APIs on Windows — one code path for every OS.
"""
import argparse
import asyncio
import json
import logging
import os
import platform
import random
import socket
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import urlparse

import httpx
import psutil
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidStatus

logging.basicConfig(level=logging.INFO, format="%(asctime)s [agent] %(levelname)s %(message)s",
                    datefmt="%H:%M:%S")
log = logging.getLogger("fleet-agent")

STATE_DIR = Path(os.environ.get("FLEET_AGENT_HOME", Path.home() / ".fleet-agent"))
COMMAND_TIMEOUT = 60
MAX_OUTPUT = 64 * 1024
EXECUTED_CACHE_SIZE = 200


# ── Local state ──────────────────────────────────────────────────────────────

class State:
    def __init__(self, directory: Path):
        self.dir = directory
        self.dir.mkdir(parents=True, exist_ok=True)
        self.file = self.dir / "state.json"
        self.executed_file = self.dir / "executed.json"
        self.data = self._load(self.file, {})
        self.executed = self._load(self.executed_file, {})

    @staticmethod
    def _load(path: Path, default):
        try:
            return json.loads(path.read_text())
        except (OSError, ValueError):
            return default

    def _save(self, path: Path, data) -> None:
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data))
        tmp.replace(path)   # atomic rename: a crash never leaves a half-written file

    def device_id_for(self, server: str) -> Optional[str]:
        return self.data.get(server)

    def set_device_id(self, server: str, device_id: str) -> None:
        self.data[server] = device_id
        self._save(self.file, self.data)

    def remember_result(self, command_id: str, result: dict) -> None:
        self.executed[command_id] = result
        while len(self.executed) > EXECUTED_CACHE_SIZE:
            self.executed.pop(next(iter(self.executed)))   # dicts keep insertion order
        self._save(self.executed_file, self.executed)


# ── Metric collection (cross-platform) ───────────────────────────────────────

def root_path() -> str:
    if platform.system() == "Windows":
        return os.environ.get("SystemDrive", "C:") + "\\"
    if platform.system() == "Darwin" and os.path.isdir("/System/Volumes/Data"):
        return "/System/Volumes/Data"   # the user-data volume on modern macOS
    return "/"


def distro_name() -> str:
    system = platform.system()
    if system == "Linux":
        try:
            return platform.freedesktop_os_release().get("PRETTY_NAME", "Linux")
        except (OSError, AttributeError):
            return f"Linux {platform.release()}"
    if system == "Darwin":
        return f"macOS {platform.mac_ver()[0]}"
    if system == "Windows":
        return f"Windows {platform.release()} ({platform.version()})"
    return f"{system} {platform.release()}"


def local_ip(server_host: str) -> Optional[str]:
    """The address this machine uses to reach the server (no packet is actually sent)."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect((server_host or "8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


class Collector:
    def __init__(self):
        psutil.cpu_percent(interval=None)   # first call primes the counter and returns 0
        self._net = psutil.net_io_counters()
        self._net_time = time.monotonic()
        self._root = root_path()

    def sample(self) -> dict:
        now = time.monotonic()
        net = psutil.net_io_counters()
        elapsed = max(now - self._net_time, 1e-6)
        rx = max(net.bytes_recv - self._net.bytes_recv, 0) / 1024 / elapsed
        tx = max(net.bytes_sent - self._net.bytes_sent, 0) / 1024 / elapsed
        self._net, self._net_time = net, now

        try:
            load = [round(x, 2) for x in psutil.getloadavg()]   # emulated on Windows
        except (AttributeError, OSError):
            load = [None, None, None]

        return {
            "ts": datetime.now(timezone.utc).isoformat(),
            "cpu_pct": round(psutil.cpu_percent(interval=None), 2),
            "mem_pct": round(psutil.virtual_memory().percent, 2),
            "disk_pct": round(psutil.disk_usage(self._root).percent, 2),
            "net_in_kbps": round(rx, 2),
            "net_out_kbps": round(tx, 2),
            "load_1m": load[0], "load_5m": load[1], "load_15m": load[2],
        }


# ── The agent ────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, server: str, token: Optional[str], interval: int, buffer_size: int, tags: dict):
        self.server = server.rstrip("/")
        self.token = token
        self.interval = interval
        self.tags = tags
        self.state = State(STATE_DIR)
        self.collector = Collector()
        self.buffer: deque = deque(maxlen=buffer_size)   # bounded: oldest dropped when full
        self.dropped = 0
        self.new_sample = asyncio.Event()

    @property
    def ws_base(self) -> str:
        u = urlparse(self.server)
        scheme = "wss" if u.scheme == "https" else "ws"
        return f"{scheme}://{u.netloc}"

    async def register(self) -> str:
        payload = {
            "device_id": self.state.device_id_for(self.server),
            "hostname": socket.gethostname(),
            "ip_address": local_ip(urlparse(self.server).hostname),
            "os": platform.system(),
            "distro": distro_name(),
            "arch": platform.machine(),
            "tags": self.tags,
        }
        headers = {"X-Agent-Token": self.token} if self.token else {}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.server}/devices/register", json=payload, headers=headers)
        if resp.status_code in (401, 403):
            raise PermissionError(resp.json().get("detail", resp.text))
        resp.raise_for_status()
        device_id = resp.json()["id"]
        self.state.set_device_id(self.server, device_id)
        return device_id

    async def sampler(self) -> None:
        """Runs forever, online or offline."""
        while True:
            try:
                sample = await asyncio.to_thread(self.collector.sample)
                if len(self.buffer) == self.buffer.maxlen:
                    self.dropped += 1
                self.buffer.append(sample)
                self.new_sample.set()
            except Exception as exc:
                log.warning("sample failed: %s", exc)
            await asyncio.sleep(self.interval)

    async def sender(self, ws) -> None:
        while True:
            await self.new_sample.wait()
            self.new_sample.clear()
            samples = list(self.buffer)
            if not samples:
                continue
            if len(samples) == 1:
                msg = {"type": "metrics", **samples[0]}
            else:
                msg = {"type": "metrics_batch", "samples": samples}
                log.info("flushing %d buffered samples%s", len(samples),
                         f" ({self.dropped} dropped while offline)" if self.dropped else "")
            await ws.send(json.dumps(msg))
            # Only remove what was actually sent; if send raised, everything stays buffered
            for _ in samples:
                self.buffer.popleft()
            self.dropped = 0

    async def run_command(self, command: str) -> dict:
        proc = await asyncio.create_subprocess_shell(
            command, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=COMMAND_TIMEOUT)
            return {"exit_code": proc.returncode, "result": out.decode(errors="replace")[:MAX_OUTPUT]}
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"exit_code": 124, "result": f"ERROR: timed out after {COMMAND_TIMEOUT}s"}

    async def receiver(self, ws) -> None:
        async for raw in ws:
            try:
                msg = json.loads(raw)
            except ValueError:
                continue
            if msg.get("type") != "command":
                continue
            cmd_id, command = str(msg.get("command_id")), str(msg.get("command", ""))
            if cmd_id in self.state.executed:
                log.info("command %s already executed — replaying stored result", cmd_id[:8])
                result = self.state.executed[cmd_id]
            else:
                log.info("running command %s: %s", cmd_id[:8], command)
                result = await self.run_command(command)
                self.state.remember_result(cmd_id, result)   # persist BEFORE replying
            await ws.send(json.dumps({"type": "command_result", "command_id": cmd_id, **result}))

    async def run(self) -> None:
        asyncio.create_task(self.sampler())
        backoff = 1
        while True:
            try:
                device_id = await self.register()
                url = f"{self.ws_base}/ws/{device_id}"
                # Token in a header, never the URL (URLs get written to access logs)
                headers = {"X-Agent-Token": self.token} if self.token else None
                async with connect(url, additional_headers=headers, ping_interval=20,
                                   ping_timeout=20, max_size=2 ** 20) as ws:
                    log.info("connected to %s as %s (%s)", self.server, socket.gethostname(), device_id)
                    backoff = 1
                    self.new_sample.set()   # flush anything buffered while offline
                    tasks = [asyncio.create_task(self.sender(ws)), asyncio.create_task(self.receiver(ws))]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for t in pending:
                        t.cancel()
                    for t in done:
                        if t.exception():
                            raise t.exception()
                    log.warning("server closed the connection")
            except PermissionError as exc:
                log.error("rejected by server: %s — check --token / the server's FLEET_MODE", exc)
                backoff = 60
            except InvalidStatus as exc:
                log.error("WebSocket rejected (HTTP %s) — check --token", exc.response.status_code)
                backoff = 60
            except (OSError, ConnectionClosed, httpx.HTTPError) as exc:
                log.warning("connection problem: %s", exc or type(exc).__name__)
            # Exponential backoff with jitter: a restarted server isn't hit by every agent at once
            delay = min(backoff, 60) * random.uniform(0.5, 1.5)
            log.info("retrying in %.0fs (%d sample(s) buffered)", delay, len(self.buffer))
            await asyncio.sleep(delay)
            backoff = min(backoff * 2, 60)


def parse_tags(pairs):
    tags = {}
    for p in pairs or []:
        key, _, value = p.partition("=")
        if key:
            tags[key] = value
    return tags


def main() -> None:
    p = argparse.ArgumentParser(description="Fleet Manager agent (Linux / macOS / Windows)")
    p.add_argument("--server", default=os.environ.get("FLEET_SERVER", "http://localhost:8000"),
                   help="control plane URL, e.g. http://10.0.0.5:8000")
    p.add_argument("--token", default=os.environ.get("FLEET_AGENT_TOKEN"), help="agent token from the server's .env")
    p.add_argument("--interval", type=int, default=10, help="seconds between samples")
    p.add_argument("--buffer", type=int, default=1000, help="max samples kept while offline")
    p.add_argument("--tag", action="append", metavar="KEY=VALUE", help="label, repeatable: --tag env=prod")
    p.add_argument("--once", action="store_true", help="print one sample and exit")
    args = p.parse_args()

    if args.once:
        c = Collector()
        time.sleep(1)
        print(json.dumps({"hostname": socket.gethostname(), "os": platform.system(),
                          "distro": distro_name(), "arch": platform.machine(), **c.sample()}, indent=2))
        return

    # (On Windows, Python's default Proactor event loop supports subprocesses, so no extra setup.)
    agent = Agent(args.server, args.token, args.interval, args.buffer, parse_tags(args.tag))
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        log.info("stopped")


if __name__ == "__main__":
    main()
