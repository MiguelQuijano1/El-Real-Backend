from typing import Any

from app.core.errors import not_found
from app.repositories.client import db, first, rows
from app.security.catalog import (
    ADMIN_ROLE_CODE,
    ALL_MODULE_KEYS,
    PERMISSION_ACTIONS,
    PERMISSION_GROUPS,
)

PermissionMap = dict[str, list[str]]


def list_permissions() -> list[dict[str, Any]]:
    return rows(db().table("permissions").select("id, module_key, action_key").order("module_key").order("action_key"))


def catalog() -> dict[str, Any]:
    return {"actions": PERMISSION_ACTIONS, "groups": PERMISSION_GROUPS}


def permissions_for_role(role_id: str) -> dict[str, Any]:
    role = first(db().table("roles").select("id, code, name").eq("id", role_id))
    if not role:
        raise not_found("Role not found")
    perms = rows(
        db().table("role_permissions")
        .select("granted, updated_at, permission:permissions(id, module_key, action_key)")
        .eq("role_id", role_id)
        .order("permission_id")
    )
    return {"role": role, "permissions": perms}


def role_has_permission(role_id: str, module: str, action: str) -> bool:
    found = first(
        db().table("role_permissions")
        .select("role_id, permissions!inner(module_key, action_key)")
        .eq("role_id", role_id)
        .eq("granted", True)
        .eq("permissions.module_key", module)
        .eq("permissions.action_key", action)
    )
    return found is not None


def map_for_role(role_id: str, role_code: str) -> PermissionMap:
    """Permisos efectivos de un rol (módulo → acciones) para que el frontend decida qué mostrar."""
    if role_code == ADMIN_ROLE_CODE:
        return {m: list(PERMISSION_ACTIONS) for m in ALL_MODULE_KEYS}
    grants = rows(
        db().table("role_permissions")
        .select("permission:permissions(module_key, action_key)")
        .eq("role_id", role_id)
        .eq("granted", True)
    )
    out: PermissionMap = {}
    for g in grants:
        p = g["permission"]
        out.setdefault(p["module_key"], []).append(p["action_key"])
    return out
