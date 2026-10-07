from typing import Any

from app.core.errors import bad_request, not_found
from app.repositories.client import db, execute, first, rows
from app.schemas.roles import CreateRoleIn, SetRolePermissionsIn, UpdateRoleIn
from app.security.catalog import ADMIN_ROLE_CODE
from app.security.deps import AuthUser
from app.services import permissions as permissions_service
from app.services.audit import RequestMeta, record

ROLE_SELECT = "id, code, name, description, role_type, is_active, created_at, updated_at"


def list_roles() -> list[dict[str, Any]]:
    return rows(db().table("roles").select(ROLE_SELECT).order("name").order("id"))


def get_role(role_id: str) -> dict[str, Any]:
    role = first(db().table("roles").select(ROLE_SELECT).eq("id", role_id))
    if not role:
        raise not_found("Role not found")
    return role


def create_role(dto: CreateRoleIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    created = execute(db().table("roles").insert({
        "code": dto.code, "name": dto.name, "description": dto.description, "role_type": "CUSTOM",
    })).data[0]
    # El rol nace sin permisos: se completan después con PUT /roles/{id}/permissions.
    all_perms = permissions_service.list_permissions()
    execute(db().table("role_permissions").insert(
        [{"role_id": created["id"], "permission_id": p["id"], "granted": False} for p in all_perms]
    ))
    record(actor.actor, action="CREATE", module_key="roles", entity_type="Role", entity_id=created["id"],
           reference=dto.code, description=f"Rol creado · {dto.name}", meta=meta)
    return get_role(created["id"])


def update_role(role_id: str, dto: UpdateRoleIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    data = dto.model_dump(exclude_unset=True)
    if not data:
        raise bad_request("At least one field must be provided")
    before = get_role(role_id)
    if before["code"] == ADMIN_ROLE_CODE and data.get("is_active") is False:
        raise bad_request("The administrator role cannot be deactivated")
    execute(db().table("roles").update(data).eq("id", role_id))
    after = get_role(role_id)
    changes = [(k, str(before[k]), str(after[k])) for k in data if before[k] != after[k]]
    if changes:
        record(actor.actor, action="EDIT", module_key="roles", entity_type="Role", entity_id=role_id,
               reference=after["code"], description=f"Rol actualizado · {after['name']}", changes=changes, meta=meta)
    return after


def set_role_permissions(role_id: str, dto: SetRolePermissionsIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    role = get_role(role_id)
    if role["code"] == ADMIN_ROLE_CODE:
        raise bad_request("The administrator role always has full access and cannot be edited")

    wanted = {(m, a) for m, actions in dto.permissions.items() for a in actions}
    current = permissions_service.permissions_for_role(role_id)["permissions"]
    before = {(p["permission"]["module_key"], p["permission"]["action_key"]) for p in current if p["granted"]}

    all_perms = permissions_service.list_permissions()
    execute(db().table("role_permissions").upsert(
        [{"role_id": role_id, "permission_id": p["id"], "granted": (p["module_key"], p["action_key"]) in wanted} for p in all_perms],
        on_conflict="role_id,permission_id",
    ))

    changes = [(f"{m}:{a}", "Sí" if (m, a) in before else "No", "Sí" if (m, a) in wanted else "No")
               for m, a in sorted(before ^ wanted)]
    if changes:
        record(actor.actor, action="EDIT", module_key="permissions", entity_type="Role", entity_id=role_id,
               reference=role["code"], description=f"Permisos actualizados · {role['name']}", changes=changes, meta=meta)
    return permissions_service.permissions_for_role(role_id)
