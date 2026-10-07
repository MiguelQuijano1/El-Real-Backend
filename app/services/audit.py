import ipaddress
import logging
from dataclasses import dataclass, field
from typing import Any

from app.repositories.client import db, execute

log = logging.getLogger("app.audit")


@dataclass
class Actor:
    user_name: str
    user_id: str | None = None
    role_id: str | None = None
    role_name: str | None = None


@dataclass
class RequestMeta:
    ip: str | None = None
    user_agent: str | None = None


def clean_ip(ip: str | None) -> str | None:
    if not ip:
        return None
    try:
        return str(ipaddress.ip_address(ip))
    except ValueError:
        return None  # la columna es de tipo inet


def record(
    actor: Actor,
    *,
    action: str,  # LOGIN | CREATE | EDIT | DELETE | EXPORT | DOWNLOAD
    module_key: str,
    description: str,
    meta: RequestMeta | None = None,
    result: str = "SUCCESS",  # SUCCESS | FAILURE | DENIED
    entity_type: str | None = None,
    entity_id: str | None = None,
    reference: str | None = None,
    changes: list[tuple[str, str, str]] | None = None,
    detail: str | None = None,
) -> None:
    """Registra un evento. Un fallo al auditar no debe tumbar la operación principal."""
    meta = meta or RequestMeta()
    try:
        execute(
            db().table("audit_events").insert(
                {
                    "user_id": actor.user_id,
                    "role_id": actor.role_id,
                    "user_name_snapshot": actor.user_name,
                    "role_name_snapshot": actor.role_name,
                    "action": action,
                    "module_key": module_key,
                    "entity_type": entity_type,
                    "entity_id": entity_id,
                    "reference_snapshot": reference,
                    "description": description,
                    "result": result,
                    "changes": [list(c) for c in (changes or [])],
                    "detail": detail,
                    "ip": clean_ip(meta.ip),
                    "device": meta.user_agent,
                }
            )
        )
    except Exception:  # noqa: BLE001
        log.exception("No se pudo registrar el evento de auditoría: %s", description)
