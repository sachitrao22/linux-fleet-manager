from typing import Dict

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self):
        self.connections: Dict[str, WebSocket] = {}

    async def connect(self, device_id: str, websocket: WebSocket):
        await websocket.accept()
        self.connections[device_id] = websocket

    def disconnect(self, device_id: str):
        self.connections.pop(device_id, None)

    def is_connected(self, device_id: str) -> bool:
        return device_id in self.connections

    async def send_command(self, device_id: str, payload: dict) -> bool:
        ws = self.connections.get(device_id)
        if not ws:
            return False
        try:
            await ws.send_json(payload)
            return True
        except Exception:
            self.disconnect(device_id)
            return False

    def active_devices(self) -> list:
        return list(self.connections.keys())


manager = ConnectionManager()
