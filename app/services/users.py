from datetime import datetime, timezone
from typing import Any

from pydantic import TypeAdapter, EmailStr, ValidationError

from app.core.errors import ApiError, bad_request, forbidden, not_found
from app.repositories.client import db, execute, first, rows
from app.schemas.users import CreateUserIn, UpdateUserIn
from app.security.catalog import ADMIN_ROLE_CODE
from app.security.deps import AuthUser
from app.security.passwords import hash_password
from app.services.audit import RequestMeta, record

PUBLIC_SELECT = (
    "id, role_id, full_name, email, area, status, mfa_enabled, failed_login_attempts, locked_until, "
    "last_login_at, created_at, updated_at, role:roles(id, code, name, role_type, is_active)"
)


def list_users() -> list[dict[str, Any]]:
    return rows(db().table("users").select(PUBLIC_SELECT).order("full_name").order("id"))


def get_user(user_id: str) -> dict[str, Any]:
    user = first(db().table("users").select(PUBLIC_SELECT).eq("id", user_id))
    if not user:
        raise not_found("User not found")
    return user


def get_by_email(email: str) -> dict[str, Any]:
    normalized = email.strip().lower()
    try:
        TypeAdapter(EmailStr).validate_python(normalized)
    except ValidationError:
        raise bad_request("A valid email is required") from None
    user = first(db().table("users").select(PUBLIC_SELECT).eq("email", normalized))
    if not user:
        raise not_found("User not found")
    return user


def _require_assignable_role(role_id: str, actor: AuthUser) -> dict[str, Any]:
    """El rol existe y está activo; solo un Administrador puede asignar el rol Administrador."""
    role = first(db().table("roles").select("id, code, name, is_active").eq("id", role_id))
    if not role:
        raise not_found("Role not found")
    if not role["is_active"]:
        raise bad_request("Role is inactive")
    if role["code"] == ADMIN_ROLE_CODE and not actor.is_admin:
        raise forbidden("Only administrators can assign the administrator role")
    return role


def _require_manageable(user_id: str, actor: AuthUser) -> dict[str, Any]:
    """El usuario existe y el actor puede gestionarlo (solo un Administrador gestiona a otro)."""
    target = get_user(user_id)
    if target["role"]["code"] == ADMIN_ROLE_CODE and not actor.is_admin:
        raise forbidden("Only administrators can manage administrator accounts")
    return target


def _audit(actor: AuthUser, meta: RequestMeta, target: dict, action: str, description: str, changes: list[tuple[str, str, str]]):
    record(actor.actor, action=action, module_key="users", entity_type="User", entity_id=target["id"],
           reference=target["email"], description=description, changes=changes, meta=meta)


def create_user(dto: CreateUserIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    role = _require_assignable_role(str(dto.role_id), actor)
    created = execute(db().table("users").insert({
        "full_name": dto.full_name.strip(),
        "email": dto.email.strip().lower(),
        "password_hash": hash_password(dto.password),
        "area": (dto.area or "").strip() or None,
        "role_id": role["id"],
    })).data[0]
    user = get_user(created["id"])
    _audit(actor, meta, user, "CREATE", f"Usuario creado · {user['email']}", [("Rol", "—", role["name"])])
    return user


def update_user(user_id: str, dto: UpdateUserIn, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    data = dto.model_dump(exclude_unset=True)
    if not data:
        raise bad_request("At least one field must be provided")
    before = _require_manageable(user_id, actor)

    patch: dict[str, Any] = {}
    if "full_name" in data and data["full_name"] is not None:
        patch["full_name"] = data["full_name"].strip()
    if "email" in data and data["email"] is not None:
        patch["email"] = data["email"].strip().lower()
    if "area" in data:
        patch["area"] = (data["area"] or "").strip() or None
    if not patch:
        raise bad_request("At least one field must be provided")

    execute(db().table("users").update(patch).eq("id", user_id))
    after = get_user(user_id)

    changes: list[tuple[str, str, str]] = []
    if before["full_name"] != after["full_name"]:
        changes.append(("Nombre", before["full_name"], after["full_name"]))
    if before["email"] != after["email"]:
        changes.append(("Correo", before["email"], after["email"]))
    if (before["area"] or "") != (after["area"] or ""):
        changes.append(("Área", before["area"] or "—", after["area"] or "—"))
    if changes:
        _audit(actor, meta, after, "EDIT", f"Usuario actualizado · {after['email']}", changes)
    return after


def set_status(user_id: str, status: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    if user_id == actor.id:
        raise forbidden("You cannot change the status of your own account")
    before = _require_manageable(user_id, actor)
    # Reactivar una cuenta bloqueada también limpia el contador de intentos.
    patch = {"status": status, "failed_login_attempts": 0, "locked_until": None} if status == "ACTIVE" else {"status": status}
    execute(db().table("users").update(patch).eq("id", user_id))
    after = get_user(user_id)
    _audit(actor, meta, after, "EDIT", f"Estado de usuario: {after['email']}", [("Estado", before["status"], after["status"])])
    return after


def assign_role(user_id: str, role_id: str, actor: AuthUser, meta: RequestMeta) -> dict[str, Any]:
    if user_id == actor.id:
        raise forbidden("You cannot change the role of your own account")
    before = _require_manageable(user_id, actor)
    role = _require_assignable_role(role_id, actor)
    execute(db().table("users").update({"role_id": role["id"]}).eq("id", user_id))
    after = get_user(user_id)
    _audit(actor, meta, after, "EDIT", f"Rol de usuario: {after['email']}", [("Rol", before["role"]["name"], role["name"])])
    return after


def reset_password(user_id: str, new_password: str, actor: AuthUser, meta: RequestMeta) -> None:
    if user_id == actor.id:
        raise bad_request("Use POST /auth/change-password for your own account")
    target = _require_manageable(user_id, actor)
    execute(db().table("users").update({
        "password_hash": hash_password(new_password), "failed_login_attempts": 0, "locked_until": None,
    }).eq("id", user_id))
    execute(
        db().table("auth_sessions").update({"revoked_at": datetime.now(timezone.utc).isoformat()})
        .eq("user_id", user_id).is_("revoked_at", "null")
    )
    _audit(actor, meta, target, "EDIT", f"Contraseña restablecida · {target['email']}", [])
