# Fleet Manager — Setup Guide

## Prerequisites
- Python 3.11+
- PostgreSQL 14+ (or Docker)
- Git

---

## Option A — Local setup (recommended for development)

### 1. Clone and enter the project
```bash
git clone <your-repo-url> fleet-manager
cd fleet-manager
```

### 2. Start Postgres with Docker (easiest)
```bash
docker compose up db -d
```
This starts Postgres on port 5432 with user `fleet`, password `fleet`, database `fleetmanager`.

If you prefer a local Postgres install instead:
```bash
psql -U postgres -c "CREATE USER fleet WITH PASSWORD 'fleet';"
psql -U postgres -c "CREATE DATABASE fleetmanager OWNER fleet;"
```

### 3. Set up the backend
```bash
cd backend
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### 4. Configure environment
```bash
cp .env.example .env
```

Open `.env` and set:
- `SECRET_KEY` — run `openssl rand -hex 32` and paste the output
- `SSH_KEY_PATH` — absolute path to your private key (for agentless mode, e.g. `~/.ssh/id_rsa`)
- `DATABASE_URL` — leave as-is if using Docker Postgres above

### 5. Start the API server
```bash
cd backend
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

You should see:
```
INFO:     Fleet Manager started. Agentless polling loop active.
INFO:     Uvicorn running on http://0.0.0.0:8000
```

Open `http://localhost:8000/docs` — the full interactive API docs are here.

---

## Option B — Docker (run everything at once)

```bash
docker compose up --build
```

API available at `http://localhost:8000/docs`.

---

## First-time: create an admin user

```bash
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "yourpassword", "role": "admin"}'
```

Get a token:
```bash
curl -X POST http://localhost:8000/auth/token \
  -d "username=admin&password=yourpassword"
```

Copy the `access_token` from the response — you'll use it in the `Authorization: Bearer <token>` header.

---

## Running the Agent (agent-based mode)

Copy `agent/agent.py` and `agent/requirements.txt` to the target Linux machine, then:

```bash
# On the target Linux device
pip install -r requirements.txt
python agent.py --server ws://YOUR_SERVER_IP:8000 --http http://YOUR_SERVER_IP:8000
```

The agent will self-register and start streaming metrics.

### Install as a systemd service (runs on boot, auto-restarts)

Create `/etc/systemd/system/fleet-agent.service`:
```ini
[Unit]
Description=Fleet Manager Agent
After=network.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/fleet-agent
ExecStart=/usr/bin/python3 /opt/fleet-agent/agent.py \
    --server ws://YOUR_SERVER_IP:8000 \
    --http http://YOUR_SERVER_IP:8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable fleet-agent
sudo systemctl start fleet-agent
sudo systemctl status fleet-agent
```

---

## Adding an agentless device (SSH mode)

Register the device manually via the API — no software needed on the target:

```bash
curl -X POST http://localhost:8000/devices/register \
  -H "Content-Type: application/json" \
  -d '{
    "hostname": "prod-server-01",
    "ip_address": "192.168.1.50",
    "mode": "agentless",
    "ssh_user": "ubuntu",
    "ssh_port": "22",
    "tags": {"env": "prod", "team": "infra"}
  }'
```

The control plane will SSH in automatically every 30 seconds and collect metrics.
Make sure the `SSH_KEY_PATH` in your `.env` has access to the target host.

---

## Testing the full flow

```bash
# 1. List all devices
curl http://localhost:8000/devices/ -H "Authorization: Bearer <token>"

# 2. Get latest metrics for a device
curl http://localhost:8000/metrics/<device_id>/latest -H "Authorization: Bearer <token>"

# 3. Send a command to a device (admin only)
curl -X POST http://localhost:8000/commands/<device_id> \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"command": "uptime", "idempotency_key": "test-uptime-001"}'

# 4. Check command result
curl http://localhost:8000/commands/<device_id> -H "Authorization: Bearer <token>"
```

---

## Testing offline resilience (the interview demo)

1. Start an agent on a device.
2. Kill the agent's network (`sudo ifconfig eth0 down` or stop the process).
3. Issue a command via the API — it stays in `pending` status.
4. Restore the agent's connection.
5. Watch the command automatically execute and update to `executed`.

This proves the Postgres-backed command queue survives disconnections.

---

## Project structure

```
fleet-manager/
├── backend/
│   ├── app/
│   │   ├── main.py              # FastAPI app, agentless polling loop
│   │   ├── config.py            # Env-var settings
│   │   ├── database.py          # SQLAlchemy async engine
│   │   ├── models/
│   │   │   ├── device.py        # Device table
│   │   │   ├── metric.py        # Metrics table (wide-row)
│   │   │   ├── command.py       # Command queue table
│   │   │   └── user.py          # Auth users
│   │   ├── schemas/             # Pydantic request/response models
│   │   ├── routers/             # API endpoints
│   │   │   ├── auth.py
│   │   │   ├── devices.py
│   │   │   ├── metrics.py
│   │   │   └── commands.py
│   │   ├── services/
│   │   │   ├── auth.py          # JWT logic
│   │   │   ├── ws_manager.py    # WebSocket connection registry
│   │   │   └── ssh_collector.py # Paramiko SSH collector
│   │   └── websocket/
│   │       └── handler.py       # WebSocket lifecycle handler
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .env.example
├── agent/
│   ├── agent.py                 # Standalone agent daemon
│   └── requirements.txt
├── docker-compose.yml
└── SETUP.md
```
