"""Cliente de Supabase (service_role). Único punto de acceso a la base de datos."""
from functools import lru_cache
from typing import Any

from postgrest.exceptions import APIError
from supabase import Client, create_client

from app.core.config import get_settings
from app.core.errors import ApiError, bad_request, conflict

_override: Any = None


@lru_cache
def _build() -> Client:
    s = get_settings()
    return create_client(s.supabase_url, s.supabase_service_role_key)


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
