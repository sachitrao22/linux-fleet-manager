#!/usr/bin/env python3
"""
Fleet Manager agent — runs on any Linux, macOS or Windows machine.

  python agent.py --server http://CONTROL_PLANE:8000 --token AGENT_TOKEN
  python agent.py --once          # print one sample and exit (sanity check)
  python agent.py --safe ...      # only built-in read-only actions, never shell commands

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
  * Built-in read-only actions (@uptime, @disk, @top, …) are implemented in Python with
    psutil — no shell, same output on every OS. With --safe, the agent refuses everything
    else, so even a compromised server can't run arbitrary code on this machine.
  * Reconnects with exponential backoff + jitter. Stops for good if the server says the
    token is invalid or the demo session has ended.

Runs on Python 3.9+ (the version macOS ships as /usr/bin/python3).

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
import ssl
import sys
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
logging.getLogger("httpx").setLevel(logging.WARNING)      # no line per HTTP request

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


# ── Built-in read-only actions (keep names in sync with app/actions.py) ──────

def _size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return str(n)


def action_uptime() -> str:
    boot = psutil.boot_time()
    secs = int(time.time() - boot)
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    lines = [f"Up {days}d {hours}h {rem // 60}m (since {datetime.fromtimestamp(boot):%Y-%m-%d %H:%M})"]
    try:
        lines.append(f"Logged-in users: {len(psutil.users())}")
    except Exception:
        pass
    try:
        lines.append("Load average (1/5/15 min): " + " / ".join(f"{x:.2f}" for x in psutil.getloadavg()))
    except (AttributeError, OSError):
        pass
    return "\n".join(lines)


def action_os() -> str:
    vm = psutil.virtual_memory()
    rows = [
        ("Hostname", socket.gethostname()),
        ("OS", distro_name()),
        ("Kernel / release", platform.release()),
        ("Architecture", platform.machine()),
        ("CPU cores", f"{psutil.cpu_count(logical=False) or '?'} physical, {psutil.cpu_count()} logical"),
        ("Memory", _size(vm.total)),
        ("Python", platform.python_version()),
    ]
    return "\n".join(f"{k:<18}{v}" for k, v in rows)


def action_memory() -> str:
    vm, sw = psutil.virtual_memory(), psutil.swap_memory()
    return (f"{'':<8}{'Total':>10}{'Used':>10}{'Free':>10}{'Use%':>7}\n"
            f"{'RAM':<8}{_size(vm.total):>10}{_size(vm.total - vm.available):>10}{_size(vm.available):>10}{vm.percent:>6.0f}%\n"
            f"{'Swap':<8}{_size(sw.total):>10}{_size(sw.used):>10}{_size(sw.free):>10}{sw.percent:>6.0f}%")


def action_disk() -> str:
    out = [f"{'Mount':<28}{'Size':>10}{'Used':>10}{'Free':>10}{'Use%':>7}"]
    seen = set()
    for part in psutil.disk_partitions(all=False):
        if part.device in seen or part.fstype in ("squashfs", "tmpfs", "devtmpfs", "overlay", ""):
            continue
        try:
            u = psutil.disk_usage(part.mountpoint)
        except (PermissionError, OSError):
            continue
        seen.add(part.device)
        out.append(f"{part.mountpoint[:27]:<28}{_size(u.total):>10}{_size(u.used):>10}{_size(u.free):>10}{u.percent:>6.0f}%")
        if len(out) > 15:
            break
    if len(out) == 1:      # containers often expose only an overlay root
        u = psutil.disk_usage(root_path())
        out.append(f"{root_path():<28}{_size(u.total):>10}{_size(u.used):>10}{_size(u.free):>10}{u.percent:>6.0f}%")
    return "\n".join(out)


def action_top() -> str:
    procs = []
    for p in psutil.process_iter(["pid", "name"]):
        try:
            p.cpu_percent(None)          # first call primes the counter
            procs.append(p)
        except (psutil.Error, OSError):
            pass
    time.sleep(0.5)
    rows = []
    for p in procs:
        try:
            rows.append((p.cpu_percent(None), p.memory_percent(), p.info["pid"], p.info["name"] or "?"))
        except (psutil.Error, OSError):
            pass
    rows.sort(reverse=True)
    out = [f"{'PID':>7}  {'CPU%':>6}  {'MEM%':>6}  NAME"]
    out += [f"{pid:>7}  {cpu:>6.1f}  {mem:>6.1f}  {name[:40]}" for cpu, mem, pid, name in rows[:10]]
    return "\n".join(out) + f"\n\n{len(rows)} processes visible to this agent"


def action_network() -> str:
    out = [f"{'Interface':<20}IPv4 address"]
    for name, addrs in psutil.net_if_addrs().items():
        for a in addrs:
            if a.family == socket.AF_INET and not a.address.startswith("127."):
                out.append(f"{name[:19]:<20}{a.address}")
    io = psutil.net_io_counters()
    out.append(f"\nTotal since boot: received {_size(io.bytes_recv)}, sent {_size(io.bytes_sent)}")
    return "\n".join(out)


ACTIONS = {
    "@uptime": action_uptime,
    "@os": action_os,
    "@memory": action_memory,
    "@disk": action_disk,
    "@top": action_top,
    "@network": action_network,
}


class AgentStop(Exception):
    """The server refused this agent for good (bad token, demo session over, limit reached)."""


# WebSocket close codes the server uses to mean "don't come back"
FINAL_CLOSE_CODES = {
    4401: "the server rejected this agent's token",
    4410: "your demo session has ended — thanks for trying it! "
          "To try again, open the dashboard and click 'Add my device'.",
}


def check_final_close(code: Optional[int]) -> None:
    if code in FINAL_CLOSE_CODES:
        raise AgentStop(FINAL_CLOSE_CODES[code])


def tls_context() -> ssl.SSLContext:
    """Certificates from certifi when available: python.org builds on macOS ship without a
    usable system CA store, so wss:// would otherwise fail to verify the server."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


# ── The agent ────────────────────────────────────────────────────────────────

class Agent:
    def __init__(self, server: str, token: Optional[str], interval: int, buffer_size: int, tags: dict,
                 name: Optional[str] = None, safe: bool = False, device_id: Optional[str] = None):
        self.server = server.rstrip("/")
        self.token = token
        self.interval = interval
        self.tags = tags
        self.name = name or socket.gethostname()
        self.safe = safe
        self.pinned_id = device_id
        self.state = State(STATE_DIR)
        self.collector = Collector()
        self.buffer: deque = deque(maxlen=buffer_size)   # bounded: oldest dropped when full
        self.dropped = 0
        # Created inside run(): on Python 3.9 an Event made before asyncio.run() binds
        # to a different event loop and fails when awaited.
        self.new_sample: Optional[asyncio.Event] = None

    @property
    def ws_base(self) -> str:
        u = urlparse(self.server)
        scheme = "wss" if u.scheme == "https" else "ws"
        return f"{scheme}://{u.netloc}"

    async def register(self) -> str:
        payload = {
            "device_id": self.state.device_id_for(self.server) or self.pinned_id,
            "hostname": self.name,
            "ip_address": local_ip(urlparse(self.server).hostname),
            "os": platform.system(),
            "distro": distro_name(),
            "arch": platform.machine(),
            "tags": self.tags,
        }
        headers = {"X-Agent-Token": self.token} if self.token else {}
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self.server}/devices/register", json=payload, headers=headers)
        if resp.status_code in (401, 403, 410, 429):
            try:
                detail = resp.json().get("detail", resp.text)
            except ValueError:
                detail = resp.text[:200]
            raise AgentStop(detail)
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
        action = ACTIONS.get(command.strip())
        if action is not None:
            try:
                return {"exit_code": 0, "result": (await asyncio.to_thread(action))[:MAX_OUTPUT]}
            except Exception as exc:
                return {"exit_code": 1, "result": f"ERROR: {exc}"}
        if self.safe:
            return {"exit_code": 126, "result": "Refused: this agent runs in safe mode and only runs its "
                    "built-in read-only actions (" + ", ".join(ACTIONS) + ")."}
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
        self.new_sample = asyncio.Event()
        asyncio.create_task(self.sampler())
        backoff = 1
        while True:
            try:
                device_id = await self.register()
                url = f"{self.ws_base}/ws/{device_id}"
                # Token in a header, never the URL (URLs get written to access logs)
                headers = {"X-Agent-Token": self.token} if self.token else None
                tls = {"ssl": tls_context()} if url.startswith("wss://") else {}
                async with connect(url, additional_headers=headers, ping_interval=20,
                                   ping_timeout=20, max_size=2 ** 20, **tls) as ws:
                    log.info("connected to %s as %s (%s)%s", self.server, self.name, device_id,
                             " — safe mode: built-in read-only actions only" if self.safe else "")
                    backoff = 1
                    self.new_sample.set()   # flush anything buffered while offline
                    tasks = [asyncio.create_task(self.sender(ws)), asyncio.create_task(self.receiver(ws))]
                    try:
                        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    finally:
                        # Always cancel and collect both tasks — also on Ctrl+C — so no
                        # "Task exception was never retrieved" noise is printed
                        for t in tasks:
                            t.cancel()
                        await asyncio.gather(*tasks, return_exceptions=True)
                    for t in done:
                        if not t.cancelled() and t.exception():
                            raise t.exception()
                    check_final_close(ws.close_code)
                    log.warning("server closed the connection")
            except AgentStop:
                raise
            except InvalidStatus as exc:
                log.error("WebSocket rejected (HTTP %s) — check --token", exc.response.status_code)
                backoff = 60
            except ConnectionClosed as exc:
                check_final_close(exc.rcvd.code if exc.rcvd else None)
                log.warning("connection lost: %s", exc)
            except (OSError, httpx.HTTPError) as exc:
                log.warning("connection problem: %s", exc or type(exc).__name__)
            except Exception as exc:     # e.g. a proxy's HTML page while the server wakes up
                log.warning("unexpected response: %s: %s", type(exc).__name__, exc)
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
    p.add_argument("--name", default=os.environ.get("FLEET_AGENT_NAME"),
                   help="name shown in the dashboard (default: this machine's hostname)")
    p.add_argument("--safe", action="store_true", default=os.environ.get("FLEET_SAFE") == "1",
                   help="only run built-in read-only actions, never shell commands")
    p.add_argument("--device-id", default=os.environ.get("FLEET_DEVICE_ID"),
                   help="use this device id when none is saved yet (owner token only)")
    p.add_argument("--once", action="store_true", help="print one sample and exit")
    p.add_argument("--action", choices=sorted(ACTIONS), help="run one built-in action locally and exit")
    args = p.parse_args()

    if args.action:
        print(ACTIONS[args.action]())
        return

    if args.once:
        c = Collector()
        time.sleep(1)
        print(json.dumps({"hostname": socket.gethostname(), "os": platform.system(),
                          "distro": distro_name(), "arch": platform.machine(), **c.sample()}, indent=2))
        return

    # (On Windows, Python's default Proactor event loop supports subprocesses, so no extra setup.)
    agent = Agent(args.server, args.token, args.interval, args.buffer, parse_tags(args.tag),
                  name=args.name, safe=args.safe, device_id=args.device_id)
    try:
        asyncio.run(agent.run())
    except KeyboardInterrupt:
        log.info("stopped")
    except AgentStop as exc:
        log.warning("Stopped: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
