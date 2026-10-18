#!/bin/sh
# Linux Fleet Manager — try-it installer for macOS and Linux.
#
#   curl -fsSL __API__/install.sh | sh -s -- <your token from the dashboard>
#
# What it does: makes a temporary folder with its own Python environment, downloads the
# agent from __API__/agent.py, and runs it in this terminal window in SAFE MODE (it can
# only report stats and run built-in read-only actions — never shell commands).
# Nothing is installed system-wide. Press Ctrl+C to stop; the folder is deleted then.
set -eu

API="__API__"
TOKEN="${1:-}"

bold() { printf '\033[1m%s\033[0m\n' "$*"; }
say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\n\033[1;31mError:\033[0m %s\n' "$*" >&2; exit 1; }

[ -n "$TOKEN" ] || fail "missing token. Copy the full command from the dashboard (it ends with your token)."

# ── 1. Find Python 3.9 or newer ─────────────────────────────────────────────
OS="$(uname -s)"
if [ "$OS" = "Darwin" ] && [ "$(command -v python3 || true)" = "/usr/bin/python3" ] \
   && ! xcode-select -p >/dev/null 2>&1; then
  fail "macOS needs Python first. Run:  xcode-select --install
       (or install Python from https://www.python.org/downloads/), then run this command again."
fi
PY=""
for c in python3 python; do
  if command -v "$c" >/dev/null 2>&1 \
     && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
    PY="$c"; break
  fi
done
if [ -z "$PY" ]; then
  if [ "$OS" = "Darwin" ]; then
    fail "Python 3.9+ is needed. Install it from https://www.python.org/downloads/ and run this again."
  else
    fail "Python 3.9+ is needed. On Ubuntu/Debian:  sudo apt install python3 python3-venv"
  fi
fi

# ── 2. Temporary environment ────────────────────────────────────────────────
DIR="$(mktemp -d "${TMPDIR:-/tmp}/fleet-agent.XXXXXX")"
cleanup() { rm -rf "$DIR"; }
trap cleanup EXIT
trap 'printf "\n"; say "Stopping the agent…"' INT

bold ""
bold "Linux Fleet Manager — adding this computer to the live demo"
say "Using $($PY --version 2>&1) · temporary folder: $DIR"
say "Setting up (about 20–40 seconds)…"
if ! "$PY" -m venv "$DIR/venv" >/dev/null 2>&1; then
  fail "couldn't create a Python environment. On Ubuntu/Debian:  sudo apt install python3-venv"
fi
VPY="$DIR/venv/bin/python"
"$VPY" -m pip install --quiet --disable-pip-version-check "httpx>=0.24" "websockets>=13" "psutil>=5.9" \
  || fail "couldn't install the agent's packages (httpx, websockets, psutil). Check your internet connection."

# ── 3. Download and start the agent ─────────────────────────────────────────
if command -v curl >/dev/null 2>&1; then
  curl -fsSL "$API/agent.py" -o "$DIR/agent.py" || fail "couldn't download the agent from $API"
else
  wget -qO "$DIR/agent.py" "$API/agent.py" || fail "couldn't download the agent from $API"
fi

say "Starting the agent. Your computer appears on the dashboard within a few seconds."
say "Keep this window open. Press Ctrl+C to stop — the temporary folder is removed automatically."
echo
FLEET_AGENT_HOME="$DIR/state" "$VPY" "$DIR/agent.py" --server "$API" --token "$TOKEN" --safe || true
echo
say "Stopped. Nothing was left on this computer. Thanks for trying Linux Fleet Manager!"
