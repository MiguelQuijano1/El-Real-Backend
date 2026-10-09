import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.core.realtime import hub
from app.security.deps import user_from_token

log = logging.getLogger("realtime")
router = APIRouter()

AUTH_TIMEOUT_SECONDS = 10
CLOSE_UNAUTHORIZED = 4401
CLOSE_BAD_ORIGIN = 4403


@router.websocket("/api/v1/ws")
async def realtime(ws: WebSocket) -> None:
    """Canal de avisos. El cliente se identifica con su primer mensaje `{"type":"auth","token":"…"}`
    (así el token no viaja en la URL ni queda en los registros de acceso)."""
    origin = ws.headers.get("origin")
    if origin and origin not in get_settings().cors_origin_list:
        await ws.close(code=CLOSE_BAD_ORIGIN)
        return
    await ws.accept()
    try:
        msg = await asyncio.wait_for(ws.receive_json(), AUTH_TIMEOUT_SECONDS)
        if not isinstance(msg, dict) or msg.get("type") != "auth":
            raise ValueError("primer mensaje inválido")
        await run_in_threadpool(user_from_token, str(msg.get("token") or ""))
    except Exception:
        await ws.close(code=CLOSE_UNAUTHORIZED)
        return

    hub.add(ws)
    try:
        await ws.send_json({"type": "ready"})
        while True:
            # El cliente manda "ping" cada tanto para mantener viva la conexión; se responde "pong".
            if await ws.receive_text() == "ping":
                await ws.send_text("pong")
    except WebSocketDisconnect:
        pass
    except Exception:
        log.debug("conexión realtime cerrada", exc_info=True)
    finally:
        hub.remove(ws)
