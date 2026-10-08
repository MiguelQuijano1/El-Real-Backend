"""Cliente de Supabase (service_role). Único punto de acceso a la base de datos."""
from functools import lru_cache
from typing import Any
from uuid import UUID

import httpx
from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.config import get_settings
from app.core.errors import ApiError, bad_request, conflict

_override: Any = None


def use_http1(pg: Any) -> None:
    """Sustituye la sesión HTTP de PostgREST por una HTTP/1.1 con pool de conexiones.

    postgrest crea su cliente httpx con http2=True: toda la API comparte UNA conexión multiplexada entre
    los hilos de FastAPI y, bajo concurrencia (la app lanza ~40 peticiones al cargar), httpcore falla con
    httpx.ReadError y la API responde 500 sin cabeceras CORS (el navegador lo muestra como error de CORS).
    """
    pg.session.close()
    pg.session = httpx.Client(
        base_url=str(pg.base_url),
        headers=pg.headers,
        timeout=pg.timeout,
        verify=pg.verify,
        follow_redirects=True,
        http2=False,
    )


@lru_cache
def _build() -> Client:
    s = get_settings()
    client = create_client(s.supabase_url, s.supabase_service_role_key)
    use_http1(client.postgrest)
    return client


def db() -> Any:
    """Cliente Supabase. Devuelve el cliente inyectado en pruebas si existe."""
    return _override if _override is not None else _build()


def set_client(client: Any) -> None:
    """Solo para pruebas: sustituye el cliente (p. ej. un PostgREST local)."""
    global _override
    _override = client


def execute(builder: Any) -> Any:
    """Ejecuta una consulta traduciendo los errores de Postgres a errores HTTP claros."""
    try:
        return builder.execute()
    except APIError as e:
        code = getattr(e, "code", None)
        if code == "23505":
            raise conflict("A record with the same unique value already exists") from e
        if code == "23503":
            raise bad_request("A referenced record does not exist, or the record is still in use") from e
        if code in ("22P02", "23502", "23514", "22001", "22003"):
            raise bad_request("Invalid value in request") from e
        raise ApiError(500, "Database error") from e


def rows(builder: Any) -> list[dict[str, Any]]:
    return execute(builder).data or []


def first(builder: Any) -> dict[str, Any] | None:
    data = rows(builder.limit(1))
    return data[0] if data else None


def _is_uuid(value: Any) -> bool:
    try:
        UUID(str(value))
        return True
    except ValueError:
        return False


def find_by_id_or_code(table: str, value: str, select: str = "*", **filters: Any) -> dict[str, Any] | None:
    """Fila por UUID o por código legible (OV-2026-0001…); `filters` añade igualdades (p. ej. kind="venta").

    La columna se elige por el formato del valor: comparar un código con la columna uuid `id` hace que
    Postgres falle con 22P02 antes de llegar a la búsqueda por código.
    """
    builder = db().table(table).select(select).eq("id" if _is_uuid(value) else "code", value)
    for column, expected in filters.items():
        builder = builder.eq(column, expected)
    return first(builder)
