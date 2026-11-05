"""Tareas en segundo plano que no deben frenar la respuesta HTTP (correos, geocodificación)."""
import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from typing import Any

logger = logging.getLogger(__name__)


def run_in_background(fn: Callable[..., Awaitable[Any]], *args: Any, **kwargs: Any) -> None:
    """Ejecuta la corrutina `fn(*args, **kwargs)` en un hilo propio con su propio event loop.

    Las rutas síncronas de FastAPI corren en un threadpool sin event loop, así que
    `asyncio.ensure_future` no sirve ahí.
    """
    def runner() -> None:
        try:
            asyncio.run(fn(*args, **kwargs))
        except Exception:
            logger.exception("Falló la tarea en segundo plano %s", getattr(fn, "__name__", fn))

    threading.Thread(target=runner, name=f"bg-{getattr(fn, '__name__', 'task')}", daemon=True).start()
