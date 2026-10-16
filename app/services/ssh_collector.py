"""
Agentless collection: SSH into a host, run ONE shell script, parse key=value output.

- One SSH session per poll (not one per metric) — connection setup dominates the cost.
- The script is piped to `sh -s` on stdin, so it works whatever the user's login shell is.
- It takes two samples one second apart, because CPU usage and network throughput
  are rates: a single read of /proc/stat only gives the average since boot.
- Linux targets read /proc and /sys directly (same sources as Prometheus node_exporter).
  macOS targets use top / vm_stat / sysctl. Windows hosts should run the agent instead.

Paramiko is blocking, so callers run these functions in a thread (asyncio.to_thread).
"""
from typing import Dict, Optional, Tuple

import paramiko

from app.config import settings

METRICS_SCRIPT = r"""
OS=$(uname -s)
echo "os=$OS"
echo "arch=$(uname -m)"
if [ "$OS" = "Linux" ]; then
  if [ -r /etc/os-release ]; then . /etc/os-release; echo "distro=$PRETTY_NAME"; fi
  cpu() { awk '/^cpu /{t=0; for(i=2;i<=9;i++) t+=$i; print t, $5+$6}' /proc/stat; }
  net() { awk 'NR>2 {sub(/:/," "); if ($1 != "lo") {rx+=$2; tx+=$10}} END{print rx+0, tx+0}' /proc/net/dev; }
  set -- $(cpu) $(net); T1=$1; I1=$2; R1=$3; X1=$4
  sleep 1
  set -- $(cpu) $(net); T2=$1; I2=$2; R2=$3; X2=$4
  awk -v t1="$T1" -v i1="$I1" -v t2="$T2" -v i2="$I2" -v r1="$R1" -v r2="$R2" -v x1="$X1" -v x2="$X2" 'BEGIN{
    dt=t2-t1; if (dt<=0) dt=1;
    printf "cpu_pct=%.2f\n", (1-(i2-i1)/dt)*100;
    printf "net_in_kbps=%.2f\nnet_out_kbps=%.2f\n", (r2-r1)/1024, (x2-x1)/1024 }'
  awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{if (t>0) printf "mem_pct=%.2f\n", (t-a)*100/t}' /proc/meminfo
  awk '{printf "load_1m=%s\nload_5m=%s\nload_15m=%s\n", $1, $2, $3}' /proc/loadavg
  df -P / | awk 'NR==2{gsub("%","",$5); print "disk_pct=" $5}'
elif [ "$OS" = "Darwin" ]; then
  echo "distro=macOS $(sw_vers -productVersion)"
  top -l 2 -n 0 -s 1 | awk '/CPU usage/{v=$7} END{gsub("%","",v); printf "cpu_pct=%.2f\n", 100-v}'
  PS=$(sysctl -n hw.pagesize); TOT=$(sysctl -n hw.memsize)
  vm_stat | awk -v ps="$PS" -v tot="$TOT" '
    /Pages free/{f=$3} /Pages inactive/{i=$3} /Pages speculative/{s=$3}
    END{gsub(/\./,"",f); gsub(/\./,"",i); gsub(/\./,"",s); printf "mem_pct=%.2f\n", 100-(f+i+s)*ps*100/tot}'
  sysctl -n vm.loadavg | awk '{printf "load_1m=%s\nload_5m=%s\nload_15m=%s\n", $2, $3, $4}'
  D=/; [ -d /System/Volumes/Data ] && D=/System/Volumes/Data
  df -P "$D" | awk 'NR==2{gsub("%","",$5); print "disk_pct=" $5}'
fi
"""

NUMERIC_FIELDS = {
    "cpu_pct", "mem_pct", "disk_pct", "net_in_kbps", "net_out_kbps",
    "load_1m", "load_5m", "load_15m",
}
TEXT_FIELDS = {"os", "distro", "arch"}
MAX_OUTPUT = 64 * 1024   # don't let one command flood the database


def parse_output(text: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    """Split the script's key=value lines into (metrics, host_info). Bad values are skipped."""
    metrics: Dict[str, float] = {}
    info: Dict[str, str] = {}
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if not sep:
            continue
        if key in NUMERIC_FIELDS:
            try:
                metrics[key] = round(max(float(value), 0.0), 2)
            except ValueError:
                pass
        elif key in TEXT_FIELDS and value:
            info[key] = value.strip('"')[:120]
    for pct in ("cpu_pct", "mem_pct", "disk_pct"):
        if pct in metrics:
            metrics[pct] = min(metrics[pct], 100.0)
    return metrics, info


def _connect(host: str, port: int, username: str) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    if settings.ssh_strict_host_keys:
        # Production: only talk to hosts already in known_hosts (prevents man-in-the-middle)
        client.load_system_host_keys()
        client.set_missing_host_key_policy(paramiko.RejectPolicy())
    else:
        # Development convenience: trust on first use
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host,
        port=port,
        username=username,
        key_filename=settings.ssh_key_path or None,
        allow_agent=True,
        look_for_keys=True,
        timeout=10,
        banner_timeout=10,
        auth_timeout=10,
    )
    return client


def collect_metrics_ssh(host: str, port: int, username: str) -> Tuple[Dict[str, float], Dict[str, str]]:
    client = _connect(host, port, username)
    try:
        stdin, stdout, _ = client.exec_command("sh -s", timeout=20)
        stdin.write(METRICS_SCRIPT)
        stdin.channel.shutdown_write()
        return parse_output(stdout.read().decode(errors="replace"))
    finally:
        client.close()


def run_command_ssh(host: str, port: int, username: str, command: str,
                    timeout: Optional[int] = 60) -> Tuple[int, str]:
    """Run one shell command. Returns (exit_code, combined stdout+stderr)."""
    client = _connect(host, port, username)
    try:
        _, stdout, stderr = client.exec_command(command, timeout=timeout)
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        code = stdout.channel.recv_exit_status()
        return code, (out + err)[:MAX_OUTPUT]
    finally:
        client.close()
