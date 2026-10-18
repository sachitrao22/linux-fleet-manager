import logging
from typing import Dict

from fastapi import WebSocket

log = logging.getLogger("fleet.ws")


class ConnectionManager:
    """
    In-memory registry of live agent WebSockets, keyed by device id.

    One instance per server process (the module-level `manager` singleton), so every
    router and the WebSocket handler see the same connections. Running several server
    processes would need a shared broker (e.g. Redis pub/sub) — see DESIGN_DECISIONS.md.
    """

    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        old = self.connections.get(device_id)
        self.connections[device_id] = websocket
        if old is not None:
            # Same device reconnected before the old socket was noticed as dead.
            log.info("device %s reconnected; closing stale socket", device_id)
            try:
                await old.close(code=4000)
            except Exception:
                pass

    def disconnect(self, device_id: str, websocket: WebSocket) -> bool:
        """
        Remove the socket ONLY if it is still the current one for this device.
        Returns True if it was current. Without this check, a slow-dying old socket
        could mark a freshly reconnected device as offline (a race condition).
        """
        if self.connections.get(device_id) is websocket:
            del self.connections[device_id]
            return True
        return False

    async def close(self, device_id: str, code: int = 1000, reason: str = "") -> None:
        """Server-initiated close, e.g. when a demo sandbox expires."""
        ws = self.connections.pop(device_id, None)
        if ws is not None:
            try:
                await ws.close(code=code, reason=reason)
            except Exception:
                pass

    def is_connected(self, device_id: str) -> bool:
        return device_id in self.connections

    async def send(self, device_id: str, payload: dict) -> bool:
        ws = self.connections.get(device_id)
        if ws is None:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            self.disconnect(device_id, ws)
            return False


manager = ConnectionManager()
