#!/usr/bin/env python3
"""
One entry point for everything. Works on Linux, macOS and Windows.

Interactive (asks you what to run and which monitoring mode):
    python launch.py

Non-interactive:
    python launch.py server --mode agentless|agent|hybrid [--hosts hosts.yaml] [--port 8000]
    python launch.py agent  --server http://HOST:8000 --token TOKEN
    python launch.py both   --mode hybrid      # server + an agent for this machine (demo)

The first server run writes .env with a random SECRET_KEY and AGENT_TOKEN.
"""
import argparse
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ENV_FILE = ROOT / ".env"
MODES = {
    "1": ("agentless", "Agentless — this server SSHes into your hosts. Nothing installed on them."),
    "2": ("agent", "Agent-based — each device runs the agent and pushes metrics to this server."),
    "3": ("hybrid", "Hybrid — accept both (recommended)."),
}


# ── .env handling ────────────────────────────────────────────────────────────

def read_env() -> dict:
    env = {}
    if ENV_FILE.exists():
        for line in ENV_FILE.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.strip()
    return env


def write_env(env: dict) -> None:
    lines = ["# Written by launch.py — edit freely. Never commit this file."]
    lines += [f"{k}={v}" for k, v in env.items()]
    ENV_FILE.write_text("\n".join(lines) + "\n")


def ensure_env(mode: str, hosts: str = None, database_url: str = None) -> dict:
    env = read_env()
    env.setdefault("SECRET_KEY", secrets.token_hex(32))
    env.setdefault("AGENT_TOKEN", secrets.token_urlsafe(24))
    env.setdefault("DATABASE_URL", "sqlite+aiosqlite:///./fleet.db")
    env["FLEET_MODE"] = mode
    if database_url:
        env["DATABASE_URL"] = database_url
    if hosts:
        env["HOSTS_FILE"] = str(Path(hosts).resolve())
    write_env(env)
    return env


# ── prompts ──────────────────────────────────────────────────────────────────

def ask(prompt: str, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    answer = input(f"{prompt}{suffix}: ").strip()
    return answer or default


def choose(title: str, options: dict, default: str) -> str:
    print(f"\n{title}")
    for key, (_, label) in options.items():
        print(f"  {key}) {label}")
    while True:
        pick = ask("Choose", default)
        if pick in options:
            return options[pick][0]
        print("  Please type one of:", ", ".join(options))


def interactive() -> argparse.Namespace:
    print("\n=== Linux Fleet Manager ===")
    role = choose("What should this machine run?", {
        "1": ("server", "Control plane (server + dashboard)"),
        "2": ("agent", "Agent only — report this machine to an existing control plane"),
        "3": ("both", "Both — server plus an agent for this machine (great for a demo)"),
    }, "1")

    ns = argparse.Namespace(role=role, mode="hybrid", hosts=None, port=8000, host="0.0.0.0",
                            server=None, token=None, database_url=None, interval=10)
    if role in ("server", "both"):
        ns.mode = choose("Monitoring mode for this control plane:", MODES, "3")
        if ns.mode in ("agentless", "hybrid"):
            hosts = ask("Path to a hosts.yaml to import (blank = add hosts later from the dashboard)", "")
            ns.hosts = hosts or None
        ns.port = int(ask("Port", "8000"))
    else:
        ns.server = ask("Control plane URL", "http://localhost:8000")
        ns.token = ask("Agent token (AGENT_TOKEN from the server's .env)", read_env().get("AGENT_TOKEN", ""))
    return ns


# ── runners ──────────────────────────────────────────────────────────────────

def server_cmd(host: str, port: int) -> list:
    return [sys.executable, "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port)]


def agent_cmd(server: str, token: str, interval: int) -> list:
    cmd = [sys.executable, str(ROOT / "agent" / "agent.py"), "--server", server, "--interval", str(interval)]
    if token:
        cmd += ["--token", token]
    return cmd


def print_server_banner(env: dict, port: int, mode: str) -> None:
    print(f"""
  Dashboard / API : http://localhost:{port}      (API docs: /docs)
  Monitoring mode : {mode}
  Database        : {env['DATABASE_URL'].split('://')[0]}""")
    if mode in ("agent", "hybrid"):
        print(f"""
  Add a device (on that machine, with the agent/ folder copied over):
    pip install -r agent/requirements.txt
    python agent/agent.py --server http://<this-machine-ip>:{port} --token {env['AGENT_TOKEN']}""")
    if mode in ("agentless", "hybrid"):
        print("""
  Add an agentless host: dashboard → "Add SSH host", or list it in hosts.yaml.
  This server must be able to `ssh user@host` with a key (no password prompt).""")
    print()


def run(ns: argparse.Namespace) -> None:
    os.chdir(ROOT)
    if ns.role == "agent":
        sys.exit(subprocess.call(agent_cmd(ns.server, ns.token, ns.interval)))

    env = ensure_env(ns.mode, ns.hosts, ns.database_url)
    print_server_banner(env, ns.port, ns.mode)

    if ns.role == "server":
        sys.exit(subprocess.call(server_cmd(ns.host, ns.port)))

    # both: start the server, wait until healthy, then start a local agent
    server = subprocess.Popen(server_cmd(ns.host, ns.port))
    agent = None
    try:
        import httpx
        for _ in range(50):
            try:
                if httpx.get(f"http://127.0.0.1:{ns.port}/health", timeout=1).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        if ns.mode == "agentless":
            print("Mode is agentless, so no local agent was started.")
        else:
            agent = subprocess.Popen(agent_cmd(f"http://127.0.0.1:{ns.port}", env["AGENT_TOKEN"], ns.interval))
        server.wait()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in (agent, server):
            if proc and proc.poll() is None:
                proc.terminate()


def main() -> None:
    if len(sys.argv) == 1:
        try:
            run(interactive())
        except (KeyboardInterrupt, EOFError):
            print()
        return

    p = argparse.ArgumentParser(description="Launch the Fleet Manager control plane and/or agent")
    sub = p.add_subparsers(dest="role", required=True)
    for role in ("server", "both"):
        s = sub.add_parser(role)
        s.add_argument("--mode", choices=["agentless", "agent", "hybrid"], default="hybrid",
                       help="agentless = central SSH pull, agent = devices push, hybrid = both")
        s.add_argument("--hosts", help="hosts.yaml to import for agentless mode")
        s.add_argument("--port", type=int, default=8000)
        s.add_argument("--host", default="0.0.0.0")
        s.add_argument("--database-url", help="e.g. postgresql+asyncpg://fleet:fleet@localhost:5432/fleet")
        s.add_argument("--interval", type=int, default=10)
        s.set_defaults(server=None, token=None)
    a = sub.add_parser("agent")
    a.add_argument("--server", required=True)
    a.add_argument("--token")
    a.add_argument("--interval", type=int, default=10)
    run(p.parse_args())


if __name__ == "__main__":
    main()
