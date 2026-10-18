#!/usr/bin/env bash
# Install the control plane or the agent as a background service:
# starts on boot (Linux) / at login (macOS), restarts if it crashes, runs without a terminal.
#   Linux → systemd     macOS → launchd
#
# Usage (from the repo, after creating ./venv and installing requirements):
#   deploy/install-service.sh server [--mode agent|agentless|hybrid] [--port 8000]
#   deploy/install-service.sh agent  --server http://10.0.0.5:8000 --token <AGENT_TOKEN>
#   deploy/install-service.sh uninstall server|agent
#   deploy/install-service.sh print server|agent [--os linux|macos] [...]   # show the file, install nothing
#
# On Linux it asks for sudo itself; on macOS run it as your normal user (no sudo).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPLOY="$ROOT/deploy"
PY="$ROOT/venv/bin/python"
ORIG_ARGS=("$@")

die() { echo "error: $*" >&2; exit 1; }
say() { echo "==> $*"; }

usage() { sed -n '5,10p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

# ── arguments ────────────────────────────────────────────────────────────────
ACTION="${1:-}"; [ -n "$ACTION" ] || usage; shift
case "$ACTION" in
  server|agent) ROLE="$ACTION" ;;
  uninstall|print) ROLE="${1:-}"; [ -n "$ROLE" ] || usage; shift ;;
  *) usage ;;
esac
case "$ROLE" in server|agent) ;; *) usage ;; esac

MODE=""; PORT="8000"; SERVER=""; TOKEN=""; OS_NAME=""
while [ $# -gt 0 ]; do
  case "$1" in
    --mode)   MODE="${2:-}"; shift 2 ;;
    --port)   PORT="${2:-}"; shift 2 ;;
    --server) SERVER="${2:-}"; shift 2 ;;
    --token)  TOKEN="${2:-}"; shift 2 ;;
    --os)     OS_NAME="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) die "unknown option: $1" ;;
  esac
done

if [ -z "$OS_NAME" ]; then
  case "$(uname -s)" in
    Linux)  OS_NAME=linux ;;
    Darwin) OS_NAME=macos ;;
    *) die "unsupported OS $(uname -s). On Windows see docs/SERVICES.md (Task Scheduler)." ;;
  esac
fi
[ "$OS_NAME" = linux ] || [ "$OS_NAME" = macos ] || die "--os must be linux or macos"

# ── checks (values are pasted into config files, so keep them simple) ───────
case "$PORT" in ''|*[!0-9]*) die "--port must be a number" ;; esac
case "$MODE" in ''|agent|agentless|hybrid) ;; *) die "--mode must be agent, agentless or hybrid" ;; esac
case "$ROOT" in *['&<>|"']*) die "move the project to a path without & < > | or \" characters" ;; esac

if [ "$ROLE" = agent ] && [ "$ACTION" != uninstall ]; then
  [ -n "$SERVER" ] || die "agent needs --server, e.g. --server http://10.0.0.5:8000"
  [ -n "$TOKEN" ]  || die "agent needs --token (AGENT_TOKEN from the server's .env)"
  case "$SERVER" in http://*|https://*) ;; *) die "--server must start with http:// or https://" ;; esac
  case "$SERVER" in *[!A-Za-z0-9.:/_~-]*) die "--server contains unexpected characters" ;; esac
  case "$TOKEN"  in *[!A-Za-z0-9._~-]*) die "--token contains unexpected characters" ;; esac
fi

# ── where things go ──────────────────────────────────────────────────────────
if [ "$OS_NAME" = linux ]; then
  NAME="fleet-$ROLE"
  TEMPLATE="$DEPLOY/systemd/$NAME.service"
  DEST="/etc/systemd/system/$NAME.service"
  RUN_USER="${SUDO_USER:-$(id -un)}"
  LOGDIR=""
else
  NAME="com.fleet.$ROLE"
  TEMPLATE="$DEPLOY/launchd/$NAME.plist"
  DEST="$HOME/Library/LaunchAgents/$NAME.plist"
  RUN_USER="$(id -un)"
  LOGDIR="$HOME/Library/Logs"
fi

render() {
  sed -e "s|{{ROOT}}|$ROOT|g" -e "s|{{PYTHON}}|$PY|g" -e "s|{{USER}}|$RUN_USER|g" \
      -e "s|{{PORT}}|$PORT|g" -e "s|{{SERVER}}|$SERVER|g" -e "s|{{TOKEN}}|$TOKEN|g" \
      -e "s|{{LOGDIR}}|$LOGDIR|g" "$TEMPLATE"
}

# ── print: show the rendered file, change nothing ────────────────────────────
if [ "$ACTION" = print ]; then
  render
  exit 0
fi

# ── Linux: become root (sudo) once, remembering who asked ───────────────────
if [ "$OS_NAME" = linux ] && [ "$(id -u)" -ne 0 ]; then
  say "systemd needs root — re-running with sudo"
  exec sudo -- "$0" "${ORIG_ARGS[@]}"
fi
if [ "$OS_NAME" = macos ] && [ "$(id -u)" -eq 0 ]; then
  die "on macOS run this as your normal user, without sudo"
fi

# ── uninstall ────────────────────────────────────────────────────────────────
if [ "$ACTION" = uninstall ]; then
  if [ "$OS_NAME" = linux ]; then
    systemctl disable --now "$NAME" 2>/dev/null || true
    rm -f "$DEST"
    [ "$ROLE" = agent ] && rm -rf /etc/fleet-agent
    systemctl daemon-reload
  else
    launchctl bootout "gui/$(id -u)/$NAME" 2>/dev/null || true
    rm -f "$DEST"
  fi
  say "$NAME removed. (Your data, .env and the agent's ~/.fleet-agent are left in place.)"
  exit 0
fi

# ── install ──────────────────────────────────────────────────────────────────
[ -x "$PY" ] || die "no virtualenv at $ROOT/venv — run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"
[ "$RUN_USER" = root ] && echo "warning: the $ROLE will run as root. Run this script as your normal user instead." >&2

as_user() {   # run a command as the user the service will run as
  if [ "$(id -un)" = "$RUN_USER" ]; then "$@"; else sudo -u "$RUN_USER" -- "$@"; fi
}

if [ "$ROLE" = server ]; then
  # A service can't answer launch.py's questions, so prepare .env now (keeps existing secrets)
  if [ -n "$MODE" ] || [ ! -f "$ROOT/.env" ]; then
    say "preparing .env"
    if [ -n "$MODE" ]; then
      (cd "$ROOT" && as_user "$PY" launch.py init --mode "$MODE")
    else
      (cd "$ROOT" && as_user "$PY" launch.py init)
    fi
  fi
fi

if [ "$OS_NAME" = linux ]; then
  if [ "$ROLE" = agent ]; then
    # Secrets live in a root-only file that systemd reads before dropping to RUN_USER
    install -d -m 700 /etc/fleet-agent
    chmod 700 /etc/fleet-agent
    ( umask 077; printf 'FLEET_SERVER=%s\nFLEET_AGENT_TOKEN=%s\n' "$SERVER" "$TOKEN" > /etc/fleet-agent/agent.env )
    chmod 600 /etc/fleet-agent/agent.env    # also fixes a file left over from an earlier install
  fi
  render > "$DEST"
  chmod 644 "$DEST"
  systemctl daemon-reload
  systemctl enable "$NAME" >/dev/null 2>&1
  systemctl restart "$NAME"
  sleep 2
  systemctl --no-pager --lines=5 status "$NAME" || true
  say "installed $DEST"
  say "logs:   journalctl -u $NAME -f"
  say "stop:   sudo systemctl stop $NAME     remove: deploy/install-service.sh uninstall $ROLE"
else
  case "$ROOT" in
    "$HOME/Desktop"*|"$HOME/Documents"*|"$HOME/Downloads"*)
      echo "warning: macOS privacy rules usually block background services from reading" >&2
      echo "         Desktop, Documents and Downloads. If the service won't start, move the" >&2
      echo "         project to e.g. ~/code/linux-fleet-manager and run this again." >&2 ;;
  esac
  mkdir -p "$(dirname "$DEST")" "$LOGDIR"
  ( umask 077; render > "$DEST" )
  chmod 600 "$DEST"                    # the agent's file contains its token
  plutil -lint "$DEST" >/dev/null || die "generated plist is invalid: $DEST"
  launchctl bootout "gui/$(id -u)/$NAME" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$DEST"
  sleep 2
  launchctl print "gui/$(id -u)/$NAME" 2>/dev/null | grep -E '^\s*(state|pid|last exit code)' || true
  say "installed $DEST"
  say "logs:   tail -f $LOGDIR/fleet-$ROLE.log"
  say "remove: deploy/install-service.sh uninstall $ROLE"
fi

if [ "$ROLE" = server ]; then
  say "dashboard: http://localhost:$PORT"
fi
