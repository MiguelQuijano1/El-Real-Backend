"""CRUD genérico para los maestros (clientes, proveedores, productos, categorías, almacenes, conductores, vehículos)."""
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first
from app.security.deps import AuthUser
from app.services.audit import RequestMeta, record


@dataclass(frozen=True)
class MasterConfig:
    table: str            # tabla en Supabase
    module: str           # clave del módulo de permisos
    entity: str           # nombre para la auditoría
    label: str            # texto legible ("Cliente")
    ref_field: str        # campo que identifica al registro en la auditoría
    search_fields: tuple[str, ...]
    code_prefix: str | None = None   # si se define y no llega `code`, se genera (CLI-00001)
    default_order: str = "created_at"
    has_created_at: bool = True
    filters: tuple[str, ...] = ()    # campos filtrables por igualdad en el listado (?category_id=…)


_UNSAFE_SEARCH = re.compile(r"[,()*%\\:]")


def _next_code(prefix: str) -> str:
    n = execute(db().rpc("next_sequence", {"p_type": prefix, "p_year": 0, "p_series": ""})).data
    n = n[0] if isinstance(n, list) else n
    return f"{prefix}-{int(n):05d}"


def list_records(cfg: MasterConfig, *, q: str | None, status: str | None, extra: dict[str, str],
                 limit: int, offset: int) -> dict[str, Any]:
    query = db().table(cfg.table).select("*", count="exact")
    if status:
        query = query.eq("status", status)
    for key, value in extra.items():
        if key in cfg.filters:
            query = query.eq(key, value)
    if q:
        term = _UNSAFE_SEARCH.sub(" ", q).strip()
        if term:
            query = query.or_(",".join(f"{f}.ilike.%{term}%" for f in cfg.search_fields))
    order_col = cfg.default_order if cfg.has_created_at else cfg.search_fields[0]
    res = execute(query.order(order_col, desc=cfg.has_created_at).range(offset, offset + limit - 1))
    return {"items": res.data or [], "total": res.count or 0, "limit": limit, "offset": offset}


def get_record(cfg: MasterConfig, record_id: str) -> dict[str, Any]:
    row = first(db().table(cfg.table).select("*").eq("id", record_id))
    if not row:
        raise not_found(f"{cfg.label} not found")
    return row


def create_record(cfg: MasterConfig, payload: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    data = {k: v for k, v in payload.items()}
    if cfg.code_prefix and not data.get("code"):
        data["code"] = _next_code(cfg.code_prefix)
    row = execute(db().table(cfg.table).insert(data)).data[0]
    record(actor.actor, action="CREATE", module_key=cfg.module, entity_type=cfg.entity, entity_id=row["id"],
           reference=str(row.get(cfg.ref_field)), description=f"{cfg.label} creado · {row.get(cfg.ref_field)}", meta=meta)
    return row


def update_record(cfg: MasterConfig, record_id: str, patch: dict[str, Any], actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    if not patch:
        raise bad_request("At least one field must be provided")
    before = get_record(cfg, record_id)
    execute(db().table(cfg.table).update(patch).eq("id", record_id))
    after = get_record(cfg, record_id)
    changes = [(k, str(before.get(k) if before.get(k) is not None else "—"), str(after.get(k) if after.get(k) is not None else "—"))
               for k in patch if before.get(k) != after.get(k)]
    if changes:
        record(actor.actor, action="EDIT", module_key=cfg.module, entity_type=cfg.entity, entity_id=record_id,
               reference=str(after.get(cfg.ref_field)), description=f"{cfg.label} actualizado · {after.get(cfg.ref_field)}",
               changes=changes, meta=meta)
    return after


def deactivate_record(cfg: MasterConfig, record_id: str, actor: AuthUser, meta: RequestMeta) -> None:
    """Baja lógica: los maestros están referenciados por documentos e historial, así que nunca se borran."""
    before = get_record(cfg, record_id)
    if before["status"] == "INACTIVE":
        return
    execute(db().table(cfg.table).update({"status": "INACTIVE"}).eq("id", record_id))
    record(actor.actor, action="DELETE", module_key=cfg.module, entity_type=cfg.entity, entity_id=record_id,
           reference=str(before.get(cfg.ref_field)), description=f"{cfg.label} dado de baja · {before.get(cfg.ref_field)}",
           changes=[("Estado", "ACTIVE", "INACTIVE")], meta=meta)
