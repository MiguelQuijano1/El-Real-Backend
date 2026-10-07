import uuid
from datetime import datetime, timezone

from app.core.config import get_settings
from app.core.errors import ApiError, bad_request, unauthorized
from app.repositories.client import db, execute, first
from app.schemas.auth import ChangePasswordIn, LoginIn
from app.security.deps import ROLE_EMBED, USER_COLUMNS, AuthUser
from app.security.passwords import dummy_hash, hash_password, verify_password
from app.security.tokens import create_access_token, hash_token
from app.services import permissions as permissions_service
from app.services.audit import Actor, RequestMeta, clean_ip, record

DEFAULT_MAX_ATTEMPTS = 5


def _invalid_credentials() -> ApiError:
    return unauthorized("Invalid email or password")


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def _denial(status: str, role_active: bool, locked_until: datetime | None, now: datetime):
    if status == "LOCKED" or (locked_until is not None and locked_until > now):
        return "ACCOUNT_LOCKED", "Account locked. Ask an administrator to unlock it."
    if status == "INACTIVE" or not role_active:
        return "ACCOUNT_INACTIVE", "Account inactive. Contact an administrator."
    return None


def _register_failed_attempt(user_id: str) -> bool:
    """Suma un intento fallido de forma atómica (función SQL); al llegar al máximo bloquea la cuenta."""
    settings = first(db().table("company_settings").select("max_login_attempts").eq("id", 1))
    max_attempts = settings["max_login_attempts"] if settings else DEFAULT_MAX_ATTEMPTS
    result = execute(db().rpc("register_failed_login", {"p_user_id": user_id, "p_max": max_attempts})).data
    row = result[0] if isinstance(result, list) and result else result
    return bool(row and row.get("locked"))


def login(dto: LoginIn, meta: RequestMeta) -> dict:
    email = dto.email.strip().lower()
    user = first(db().table("users").select(f"{USER_COLUMNS}, password_hash, {ROLE_EMBED}").eq("email", email))

    # Se verifica siempre una contraseña (real o falsa) para que el tiempo no revele si el correo existe.
    password_ok = verify_password((user or {}).get("password_hash") or dummy_hash(), dto.password)

    if not user or not user.get("password_hash"):
        record(Actor(user_name=email), action="LOGIN", module_key="security", reference=email, meta=meta,
               description="Inicio de sesión con correo no registrado", result="FAILURE")
        raise _invalid_credentials()

    actor = Actor(user_name=user["full_name"], user_id=user["id"], role_id=user["role_id"], role_name=user["role"]["name"])
    common = dict(action="LOGIN", module_key="security", entity_type="User", entity_id=user["id"], reference=email, meta=meta)

    if not password_ok:
        locked = _register_failed_attempt(user["id"]) if user["status"] in ("ACTIVE", "INVITED") else False
        record(actor, description="Inicio de sesión", result="FAILURE",
               detail="Cuenta bloqueada por exceder los intentos fallidos." if locked else "Contraseña incorrecta.", **common)
        raise _invalid_credentials()

    # Contraseña correcta: ahora sí se informa el motivo si la cuenta no puede entrar.
    now = datetime.now(timezone.utc)
    denial = _denial(user["status"], user["role"]["is_active"], _parse_ts(user["locked_until"]), now)
    if denial:
        code, message = denial
        record(actor, description="Intento de ingreso con cuenta no habilitada", result="DENIED", detail=message, **common)
        raise unauthorized(message, code)

    session_id = str(uuid.uuid4())
    token, expires_at = create_access_token(user["id"], session_id)

    # Una invitación pendiente se confirma con el primer ingreso exitoso.
    updated = execute(
        db().table("users")
        .update({"status": "ACTIVE", "failed_login_attempts": 0, "locked_until": None, "last_login_at": now.isoformat()})
        .eq("id", user["id"])
        .in_("status", ["ACTIVE", "INVITED"])
    ).data
    if not updated:
        raise _invalid_credentials()

    execute(db().table("auth_sessions").insert({
        "id": session_id, "user_id": user["id"], "token_hash": hash_token(token), "persistent": True,
        "expires_at": expires_at.isoformat(), "ip": clean_ip(meta.ip), "user_agent": meta.user_agent,
    }))

    safe_user = first(db().table("users").select(f"{USER_COLUMNS}, {ROLE_EMBED}").eq("id", user["id"]))
    safe_user.pop("locked_until", None)
    record(actor, description="Inicio de sesión", **common)

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": get_settings().jwt_expires_in,
        "user": safe_user,
        "permissions": permissions_service.map_for_role(safe_user["role_id"], safe_user["role"]["code"]),
    }


def logout(user: AuthUser, meta: RequestMeta) -> None:
    execute(
        db().table("auth_sessions").update({"revoked_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", user.session_id).eq("user_id", user.id).is_("revoked_at", "null")
    )
    record(user.actor, action="LOGIN", module_key="security", entity_type="User", entity_id=user.id,
           reference=user.email, description="Cierre de sesión", meta=meta)


def me(user: AuthUser) -> dict:
    return {**user.public(), "permissions": permissions_service.map_for_role(user.role_id, user.role["code"])}


def change_password(user: AuthUser, dto: ChangePasswordIn, meta: RequestMeta) -> None:
    """Cambia la contraseña propia y cierra las demás sesiones abiertas."""
    row = first(db().table("users").select("password_hash").eq("id", user.id))
    if not row or not row["password_hash"] or not verify_password(row["password_hash"], dto.current_password):
        raise bad_request("Current password is incorrect")
    if dto.new_password == dto.current_password:
        raise bad_request("New password must be different from the current one")

    execute(db().table("users").update({"password_hash": hash_password(dto.new_password)}).eq("id", user.id))
    execute(
        db().table("auth_sessions").update({"revoked_at": datetime.now(timezone.utc).isoformat()})
        .eq("user_id", user.id).is_("revoked_at", "null").neq("id", user.session_id)
    )
    record(user.actor, action="EDIT", module_key="security", entity_type="User", entity_id=user.id,
           reference=user.email, description="Cambio de contraseña", meta=meta)
