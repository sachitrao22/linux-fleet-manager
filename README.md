# Linux Fleet Manager

A self-hosted control plane for monitoring and managing a fleet of machines — servers,
VMs, laptops and edge boards — from one dashboard. It collects CPU, memory, disk,
network and load metrics, raises threshold alerts, and runs remote commands, with
**two monitoring modes you choose at launch**:

| Mode | How it works | Use it when |
|---|---|---|
| **Agentless** | The control plane SSHes into each host and runs one script | You can't install software on the hosts (locked-down VMs, client machines) |
| **Agent** | A small Python agent on each device pushes metrics over WebSocket | Devices are behind NAT, go offline, or run Windows/macOS |
| **Hybrid** | Both at once (default) | Mixed fleets |

The agent runs on **Linux, macOS and Windows** (x86_64 and ARM, including Raspberry Pi).
Agentless targets are Linux or macOS hosts reachable over SSH.

![Architecture](./docs/architecture.svg)

## Features

- **Dual-mode collection** — agentless SSH pull (Paramiko) and agent WebSocket push, selectable per deployment, mixed freely per device
- **Offline resilience** — commands issued to an offline device are queued and delivered on reconnect; the agent buffers samples during outages and flushes them with their original timestamps
- **At-least-once command delivery with idempotent execution** — re-sent commands return their stored result instead of running twice
- **Threshold alerts** — warning/critical per metric, deduplicated per incident, auto-resolving; "device not reporting" alerts
- **Built-in dashboard** — fleet table, per-device charts, remote command console, alerts, CloudWatch cost calculator
- **Security** — bcrypt passwords, JWT with admin/viewer roles, first-run admin bootstrap, shared agent token sent as a header, SSH keys only (no stored passwords), optional strict host-key checking
- **Zero-setup by default** — SQLite out of the box; PostgreSQL for production (same code, tested on both)

## Quick start (macOS / Linux / Windows)

Requires Python 3.10+.

```bash
git clone https://github.com/sachitrao22/linux-fleet-manager.git
cd linux-fleet-manager
python3 -m venv venv
source venv/bin/activate            # Windows: venv\Scripts\activate
pip install -r requirements.txt
python launch.py
```

The launcher asks what to run and **which monitoring mode** to use:

```
What should this machine run?
  1) Control plane (server + dashboard)
  2) Agent only — report this machine to an existing control plane
  3) Both — server plus an agent for this machine (great for a demo)

Monitoring mode for this control plane:
  1) Agentless — this server SSHes into your hosts. Nothing installed on them.
  2) Agent-based — each device runs the agent and pushes metrics to this server.
  3) Hybrid — accept both (recommended).
```

Open **http://localhost:8000**. The first account you create becomes the admin.
The first run writes `.env` with a random `SECRET_KEY` and `AGENT_TOKEN`.

Non-interactive equivalents:

```bash
python launch.py both   --mode hybrid                     # server + local agent
python launch.py server --mode agentless --hosts hosts.yaml
python launch.py agent  --server http://10.0.0.5:8000 --token <AGENT_TOKEN>
```

### Run it as a service (starts on boot, restarts on crash)

`launch.py` stops when you close the terminal. To keep the server or an agent running,
install it as a systemd (Linux) or launchd (macOS) service:

```bash
deploy/install-service.sh server --mode hybrid
deploy/install-service.sh agent  --server http://10.0.0.5:8000 --token <AGENT_TOKEN>
```

Details, logs, uninstalling and Windows: [docs/SERVICES.md](./docs/SERVICES.md).

## Adding devices

**Agent (any OS).** Copy the `agent/` folder to the machine, then:

```bash
pip install -r agent/requirements.txt
python agent/agent.py --server http://<control-plane-ip>:8000 --token <AGENT_TOKEN> --tag env=prod
python agent/agent.py --once        # just print one sample, to check it works
```

The agent saves its device id in `~/.fleet-agent/`, so restarts keep the same identity.
To run it on boot, install it as a service: `deploy/install-service.sh agent …`
([docs/SERVICES.md](./docs/SERVICES.md); Windows uses Task Scheduler, also described there).

**Agentless (Linux / macOS hosts).** Nothing is installed on the host. The control
plane must be able to `ssh user@host` with a key and no password prompt:

```bash
ssh-copy-id user@host                     # once, from the control-plane machine
```

Then add it from the dashboard ("Add SSH host"), or list hosts in `hosts.yaml`
(see `hosts.example.yaml`) and start with `--hosts hosts.yaml`. On macOS targets,
enable System Settings → General → Sharing → Remote Login.

## Production: Docker + PostgreSQL

```bash
cp .env.example .env        # set SECRET_KEY, AGENT_TOKEN, FLEET_MODE
docker compose up --build
```

Or point a local install at Postgres:
`python launch.py server --database-url postgresql+asyncpg://user:pass@host:5432/fleet`.
For agentless in production, set `SSH_STRICT_HOST_KEYS=true` and pre-populate `known_hosts`.

## API

Interactive docs at `/docs`. Main endpoints:

| Method | Path | Who | Purpose |
|---|---|---|---|
| POST | `/auth/register` | first user / admin | Create a user (first one is admin) |
| POST | `/auth/token` | anyone | Log in, get a JWT |
| POST | `/devices/register` | agent (token) | Agent registers / re-registers |
| POST | `/devices/agentless` | admin | Add an SSH host |
| GET | `/devices` | viewer | Fleet overview with latest metrics and alert counts |
| GET | `/metrics/{id}?minutes=60` | viewer | Metric history |
| POST | `/commands/{id}` | admin | Run a command (queued if offline; `idempotency_key` supported) |
| GET | `/alerts?status=open` | viewer | Alerts |
| WS | `/ws/{id}` | agent (token) | Metrics up, commands down |

## Tests

```bash
pip install -r requirements-dev.txt
pytest
TEST_DATABASE_URL=postgresql+asyncpg://user@localhost:5432/fleet_test pytest   # same suite on Postgres
```

16 tests cover auth bootstrap and roles, agent tokens, identity on re-register, metric
ingestion and validation, buffered batches, alert open/escalate/resolve, the offline
command queue and at-least-once redelivery, idempotency keys, mode gating, unreachable
agentless hosts, and the agentless shell script itself.

## Project structure

```
app/
  main.py              app + lifespan: starts the SSH poller and offline sweeper
  config.py            all settings (env vars / .env)
  database.py          async SQLAlchemy engine and sessions
  models/              devices, metrics (wide-row), commands (queue), alerts, users
  schemas/             Pydantic request/response validation
  routers/             auth, devices, metrics, commands, alerts, ws (agent socket)
  services/            ingest, alerts, ssh_collector, poller, ws_manager, auth
  static/index.html    dashboard (vanilla JS, no build step)
agent/agent.py         cross-platform agent (psutil)
launch.py              interactive launcher: choose role and monitoring mode; `init` writes .env only
deploy/                systemd units, launchd plists, install-service.sh
tests/                 pytest suite
DESIGN_DECISIONS.md    why things are built the way they are
```

## Known limitations

- One server process: live agent connections are tracked in memory, so horizontal scaling needs a shared broker (Redis pub/sub) — see `DESIGN_DECISIONS.md`.
- Metrics are kept forever; add retention/downsampling (or TimescaleDB) for long-running fleets.
- Agentless polling opens one SSH session per host per interval; past a few hundred hosts, prefer agents.
- Schema is created with `create_all`; adopt Alembic migrations before changing it in production.
