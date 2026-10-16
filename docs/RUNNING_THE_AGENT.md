# Running the agent as a background service

Install once on the machine (any OS):

```bash
python3 -m venv ~/.fleet-agent/venv
~/.fleet-agent/venv/bin/pip install -r agent/requirements.txt    # Windows: %USERPROFILE%\.fleet-agent\venv\Scripts\pip
```

## Linux (systemd)

`/etc/systemd/system/fleet-agent.service`:

```ini
[Unit]
Description=Fleet Manager agent
After=network-online.target
Wants=network-online.target

[Service]
User=YOUR_USER
ExecStart=/home/YOUR_USER/.fleet-agent/venv/bin/python /opt/fleet-agent/agent.py --server http://CONTROL_PLANE:8000
Environment=FLEET_AGENT_TOKEN=YOUR_AGENT_TOKEN
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload && sudo systemctl enable --now fleet-agent
journalctl -u fleet-agent -f
```

## macOS (launchd)

`~/Library/LaunchAgents/com.fleet.agent.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.fleet.agent</string>
  <key>ProgramArguments</key><array>
    <string>/Users/YOU/.fleet-agent/venv/bin/python</string>
    <string>/Users/YOU/fleet-agent/agent.py</string>
    <string>--server</string><string>http://CONTROL_PLANE:8000</string>
  </array>
  <key>EnvironmentVariables</key><dict>
    <key>FLEET_AGENT_TOKEN</key><string>YOUR_AGENT_TOKEN</string>
  </dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardErrorPath</key><string>/tmp/fleet-agent.log</string>
</dict></plist>
```

```bash
launchctl load ~/Library/LaunchAgents/com.fleet.agent.plist
```

## Windows (Task Scheduler)

```powershell
$py = "$env:USERPROFILE\.fleet-agent\venv\Scripts\pythonw.exe"
$action = New-ScheduledTaskAction -Execute $py -Argument "C:\fleet-agent\agent.py --server http://CONTROL_PLANE:8000 --token YOUR_AGENT_TOKEN"
$trigger = New-ScheduledTaskTrigger -AtLogOn
Register-ScheduledTask -TaskName "FleetAgent" -Action $action -Trigger $trigger -RunLevel Limited
```
