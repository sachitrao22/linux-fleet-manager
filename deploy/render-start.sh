#!/usr/bin/env bash
# Start command for the hosted public demo (Render; works on any Linux host too).
#
# Runs two processes in one container:
#   1. a safe-mode agent reporting THIS machine — the live "showcase" device that every
#      visitor sees and can run read-only actions on, without installing anything;
#   2. the API server (uvicorn), which also serves the dashboard.
set -euo pipefail
PORT="${PORT:-8000}"
PY="${PYTHON:-python}"

if [ "${SHOWCASE_AGENT:-1}" = "1" ] && [ -n "${AGENT_TOKEN:-}" ]; then
  # The container's disk is wiped on every restart, so the agent can't remember its id:
  # SHOWCASE_DEVICE_ID pins it, keeping one device (and its history) across restarts.
  "$PY" agent/agent.py --server "http://127.0.0.1:$PORT" --token "$AGENT_TOKEN" --safe \
      --name "${SHOWCASE_NAME:-live-demo-server}" --tag role=showcase --interval 10 \
      ${SHOWCASE_DEVICE_ID:+--device-id "$SHOWCASE_DEVICE_ID"} &
fi

# --proxy-headers: behind Render's proxy, see the real client IP (rate limits) and https
exec "$PY" -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" --proxy-headers --forwarded-allow-ips '*'
