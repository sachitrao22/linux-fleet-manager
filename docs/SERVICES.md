# Running as a service

`python launch.py` is for trying things out: it stops when you close the terminal.
For anything you leave running, install the server and/or the agent as a **service**:
it starts automatically (on boot on Linux, at login on macOS), restarts after a crash,
and runs without a terminal.

| OS | Service manager | Files used |
|---|---|---|
| Linux (incl. Raspberry Pi) | systemd | `deploy/systemd/fleet-server.service`, `fleet-agent.service` |
| macOS | launchd | `deploy/launchd/com.fleet.server.plist`, `com.fleet.agent.plist` |
| Windows | Task Scheduler | see below |

`deploy/install-service.sh` fills in your paths, user and port, installs the file,
and starts the service. Run it from the project folder **after** creating the virtualenv:

```bash
python3 -m venv venv
venv/bin/pip install -r requirements.txt
```

## Control plane (server)

```bash
deploy/install-service.sh server --mode hybrid        # or agent / agentless; --port 8000
```

- A service can't answer the launcher's questions, so the script first runs
  `python launch.py init --mode …`, which writes `.env` (owner-only) with the secrets and mode.
  Existing secrets are kept; running it again with a different `--mode` just switches mode.
- It prints the `AGENT_TOKEN` you'll need for agents.
- Linux: runs as the user who ran the script (it calls `sudo` itself), never as root.

## Agent (each monitored machine)

Copy the project (or just `agent/`, `deploy/` and a venv with `agent/requirements.txt`), then:

```bash
deploy/install-service.sh agent --server http://<control-plane-ip>:8000 --token <AGENT_TOKEN>
```

- Linux: the server URL and token go in `/etc/fleet-agent/agent.env` (root-only, mode 600),
  so the token never appears in `ps`.
- macOS: they go in the plist's environment; the plist is written with mode 600.
- Remote commands run with the permissions of the user the agent runs as.

## Everyday commands

| Task | Linux | macOS |
|---|---|---|
| Is it running? | `systemctl status fleet-server` | `launchctl print gui/$(id -u)/com.fleet.server` |
| Follow the logs | `journalctl -u fleet-server -f` | `tail -f ~/Library/Logs/fleet-server.log` |
| Stop / start | `sudo systemctl stop fleet-server` / `start` | `launchctl bootout gui/$(id -u)/com.fleet.server` / re-run the installer |
| Remove | `deploy/install-service.sh uninstall server` | same |
| See the file without installing | `deploy/install-service.sh print server` | `… print server --os macos` |

Use `agent` / `fleet-agent` / `com.fleet.agent` in place of `server` for the agent.
Uninstall keeps your data: `.env`, `fleet.db`, and the agent's `~/.fleet-agent` identity.

## macOS notes

- **Keep the project out of Desktop, Documents and Downloads.** macOS privacy rules usually
  stop background services from reading those folders, and the service fails to start.
  `~/code/linux-fleet-manager` works.
- These are launch **agents**: they run while you're logged in, which suits a laptop.
  For a headless Mac server, copy the plist to `/Library/LaunchDaemons/`, add a `UserName`
  key, and load it with `sudo launchctl bootstrap system <plist>`.
- The agent's PATH includes Homebrew, so remote commands like `brew list` work.

## Windows (agent)

```powershell
$py = "$env:USERPROFILE\linux-fleet-manager\venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "agent\agent.py" -WorkingDirectory "$env:USERPROFILE\linux-fleet-manager"
$trigger = New-ScheduledTaskTrigger -AtLogOn
[Environment]::SetEnvironmentVariable("FLEET_SERVER", "http://CONTROL_PLANE:8000", "User")
[Environment]::SetEnvironmentVariable("FLEET_AGENT_TOKEN", "YOUR_AGENT_TOKEN", "User")
Register-ScheduledTask -TaskName "FleetAgent" -Action $action -Trigger $trigger -RunLevel Limited
```

## Why these settings

| Setting | Why |
|---|---|
| `After=network-online.target` | Don't start before the network exists: agents connect to the server, the poller needs SSH |
| Server `Restart=on-failure` (launchd: `SuccessfulExit=false`) | Restart after a crash, but a deliberate stop stays stopped |
| Agent `Restart=always` (launchd: `KeepAlive=true`) | The agent should never be down; restart it whatever the reason |
| `RestartSec=5` / `ThrottleInterval=5` | A crash loop can't spin the CPU |
| `User=` your user, never root | Least privilege: a bug or a remote command can't take over the machine |
| `EnvironmentFile` (root-only) | Secrets stay out of the command line, which every user can read with `ps` |
| `NoNewPrivileges`, `PrivateTmp`, `ProtectSystem=full` | Basic systemd hardening for the server: no privilege escalation, private /tmp, read-only system folders |
| `PYTHONUNBUFFERED=1` | Log lines reach journald / the log file immediately |
