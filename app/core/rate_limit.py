"""Límite de peticiones en memoria (ventana deslizante por IP).

Suficiente para una sola instancia. Con varias instancias/workers detrás de un balanceador cada
proceso lleva su propio contador; para un límite global usa un WAF/API Gateway o Redis.
"""
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

from app.core.config import get_settings
from app.core.errors import ApiError


def client_ip(request: Request) -> str | None:
    if get_settings().trust_proxy:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()
    return request.client.host if request.client else None


class SlidingWindowLimiter:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()
        self._last_sweep = time.monotonic()

    def hit(self, key: str, limit: int, window: float) -> bool:
        """Registra una petición. Devuelve False si se superó el límite."""
        now = time.monotonic()
        with self._lock:
            if now - self._last_sweep > 300:
                self._sweep(now)
            q = self._hits[key]
            while q and now - q[0] > window:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def _sweep(self, now: float) -> None:
        for k in [k for k, q in self._hits.items() if not q or now - q[-1] > 3600]:
            del self._hits[k]
        self._last_sweep = now

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = SlidingWindowLimiter()


def rate_limit(name: str, limit: int, window_seconds: int = 60):
    """Dependencia para limitar una ruta concreta (login, cambio de contraseña…)."""

    def dependency(request: Request) -> None:
        if not limiter.hit(f"{name}:{client_ip(request)}", limit, window_seconds):
            raise ApiError(429, "Too many requests")

    return dependency
