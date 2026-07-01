"""
SSH-based metric collection and remote command execution for agentless devices.
Uses Paramiko to SSH into Linux hosts and read /proc + /sys directly.

Interview talking point: this is the same approach Ansible uses — no software installed
on the target, just SSH access. Tradeoff vs agent: higher connection overhead per poll,
can't buffer offline data, blocked by NAT/firewall. Good for locked-down corporate VMs.
"""
from typing import Any, Dict, Optional

import paramiko

# All metric commands read directly from /proc and /sys — same sources
# as Prometheus Node Exporter. No external tools required on the target host.
METRIC_COMMANDS: Dict[str, str] = {
    "cpu_pct": (
        "awk '/^cpu /{idle=$5; total=$2+$3+$4+$5+$6+$7+$8} "
        "END{printf \"%.2f\", (1 - idle/total) * 100}' /proc/stat"
    ),
    "mem_pct": (
        "awk '/MemTotal/{t=$2} /MemAvailable/{a=$2} "
        "END{printf \"%.2f\", (t-a)*100/t}' /proc/meminfo"
    ),
    "disk_pct": "df / --output=pcent | tail -1 | tr -d ' %'",
    "net_in_kb":  "awk 'NR>2{sum+=$2} END{printf \"%.2f\", sum/1024}' /proc/net/dev",
    "net_out_kb": "awk 'NR>2{sum+=$10} END{printf \"%.2f\", sum/1024}' /proc/net/dev",
    "load_1m":  "awk '{print $1}' /proc/loadavg",
    "load_5m":  "awk '{print $2}' /proc/loadavg",
    "load_15m": "awk '{print $3}' /proc/loadavg",
}


def _make_client(
    hostname: str,
    username: str,
    port: int = 22,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: Dict[str, Any] = {"hostname": hostname, "port": port, "username": username, "timeout": 10}
    if key_path:
        kwargs["key_filename"] = key_path
    elif password:
        kwargs["password"] = password
    client.connect(**kwargs)
    return client


def collect_metrics_ssh(
    hostname: str,
    username: str,
    port: int = 22,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> Dict[str, Any]:
    """
    SSH into a Linux host and collect all metrics in a single connection.
    Returns a dict matching MetricPayload fields.
    """
    client = _make_client(hostname, username, port, password, key_path)
    metrics: Dict[str, Any] = {}
    try:
        # Run all commands in one SSH session (not one connection per metric)
        combined = " && ".join(f"echo {k}=$({cmd})" for k, cmd in METRIC_COMMANDS.items())
        _, stdout, _ = client.exec_command(combined, timeout=10)
        for line in stdout.read().decode().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                try:
                    metrics[k.strip()] = float(v.strip())
                except ValueError:
                    metrics[k.strip()] = None
    finally:
        client.close()
    return metrics


def run_command_ssh(
    hostname: str,
    username: str,
    command: str,
    port: int = 22,
    password: Optional[str] = None,
    key_path: Optional[str] = None,
) -> str:
    """Run an arbitrary shell command on a remote Linux host via SSH."""
    client = _make_client(hostname, username, port, password, key_path)
    try:
        _, stdout, stderr = client.exec_command(command, timeout=30)
        out = stdout.read().decode()
        err = stderr.read().decode()
        return out if out else err
    finally:
        client.close()
