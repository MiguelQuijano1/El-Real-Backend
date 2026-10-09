"""Avisos en tiempo real: cuando alguien guarda un cambio, los demás navegadores conectados se enteran.

El aviso no lleva datos, solo qué recurso cambió (`{"type": "changed", "resource": "sales-orders", "by": <id de pestaña>}`);
cada cliente vuelve a pedir por HTTP lo que necesita, así que los permisos de siempre siguen aplicando.

Las conexiones viven en memoria del proceso: con varios workers de uvicorn un aviso solo llegaría a los clientes
conectados al mismo worker. Para escalar a varios procesos habría que pasar los avisos por Redis o Supabase Realtime.
"""
import asyncio
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("realtime")

MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
SEND_TIMEOUT_SECONDS = 2.0


class Hub:
    def __init__(self) -> None:
        self._clients: set[WebSocket] = set()

    def add(self, ws: WebSocket) -> None:
        self._clients.add(ws)

    def remove(self, ws: WebSocket) -> None:
        self._clients.discard(ws)

    @property
    def size(self) -> int:
        return len(self._clients)

    async def publish(self, event: dict[str, Any]) -> None:
        clients = list(self._clients)
        if not clients:
            return

        async def send(ws: WebSocket) -> None:
            try:
                await asyncio.wait_for(ws.send_json(event), SEND_TIMEOUT_SECONDS)
            except Exception:  # cliente caído o lento: se descarta, vuelve a conectar solo
                self._clients.discard(ws)

        await asyncio.gather(*(send(ws) for ws in clients))


hub = Hub()


class ChangeNotifier:
    """Middleware ASGI: tras cada escritura exitosa (POST/PUT/PATCH/DELETE bajo /api/v1) avisa a los clientes."""

    def __init__(self, app) -> None:
        self.app = app

    async def __call__(self, scope, receive, send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http" or scope["method"] not in MUTATING or not path.startswith("/api/v1/"):
            return await self.app(scope, receive, send)

        parts = path.split("/")
        resource = parts[3] if len(parts) > 3 else ""
        if resource in ("", "auth"):
            return await self.app(scope, receive, send)
        by = next((v.decode("latin-1") for k, v in scope.get("headers", []) if k == b"x-client-id"), None)
        status = 0

        async def wrapped(message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)
            if message["type"] == "http.response.body" and not message.get("more_body") and status < 400:
                try:
                    await hub.publish({"type": "changed", "resource": resource, "by": by})
                except Exception:  # un fallo al avisar nunca debe afectar la respuesta ya enviada
                    log.exception("no se pudo publicar el aviso de cambio")

        await self.app(scope, receive, wrapped)
