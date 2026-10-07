"""Dependencias de autenticación y autorización.

FastAPI permite todo por defecto, así que cada ruta DEBE declarar una política con una de estas
dependencias (public / authenticated / require_permission). tests/test_route_policies.py falla
si alguna ruta no declara ninguna: así se conserva el "denegar por defecto".
"""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.errors import forbidden, unauthorized
from app.core.rate_limit import client_ip
from app.repositories.client import db, execute, first
from app.security.catalog import ADMIN_ROLE_CODE
from app.security.tokens import decode_access_token, hash_token
from app.services import permissions as permissions_service
from app.services.audit import Actor, RequestMeta

_bearer = HTTPBearer(auto_error=False)

USER_COLUMNS = "id, role_id, full_name, email, area, status, last_login_at, locked_until"
ROLE_EMBED = "role:roles(id, code, name, is_active)"


@dataclass
class AuthUser:
    id: str
    role_id: str
    full_name: str
    email: str
    area: str | None
    status: str
    last_login_at: str | None
    role: dict[str, Any]
    session_id: str

    @property
    def is_admin(self) -> bool:
        return self.role["code"] == ADMIN_ROLE_CODE

    @property
    def actor(self) -> Actor:
        return Actor(user_name=self.full_name, user_id=self.id, role_id=self.role_id, role_name=self.role["name"])

    def public(self) -> dict[str, Any]:
        """Datos del usuario para el frontend (sin hash ni campos internos)."""
        return {
            "id": self.id, "role_id": self.role_id, "full_name": self.full_name, "email": self.email,
            "area": self.area, "status": self.status, "last_login_at": self.last_login_at,
            "role": self.role,
        }


def request_meta(request: Request) -> RequestMeta:
    return RequestMeta(ip=client_ip(request), user_agent=request.headers.get("user-agent"))


def _parse_ts(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> AuthUser:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized()
    payload = decode_access_token(credentials.credentials)
    if payload is None:
        raise unauthorized()

    now = datetime.now(timezone.utc)
    session = first(
        db().table("auth_sessions")
        .select(f"id, last_seen_at, user:users({USER_COLUMNS}, {ROLE_EMBED})")
        .eq("id", payload["sid"])
        .eq("user_id", payload["sub"])
        .eq("token_hash", hash_token(credentials.credentials))
        .is_("revoked_at", "null")
        .gt("expires_at", now.isoformat())
    )
    user = session["user"] if session else None
    locked_until = _parse_ts(user["locked_until"]) if user else None
    if (
        not user
        or user["status"] != "ACTIVE"
        or not user["role"]["is_active"]
        or (locked_until is not None and locked_until > now)
    ):
        raise unauthorized()

    # Se refresca last_seen como máximo una vez por minuto para no sumar una escritura a cada petición.
    last_seen = _parse_ts(session["last_seen_at"])
    if last_seen is None or now - last_seen > timedelta(seconds=60):
        execute(db().table("auth_sessions").update({"last_seen_at": now.isoformat()}).eq("id", session["id"]))

    return AuthUser(
        id=user["id"], role_id=user["role_id"], full_name=user["full_name"], email=user["email"],
        area=user["area"], status=user["status"], last_login_at=user["last_login_at"],
        role=user["role"], session_id=session["id"],
    )


# ─── Políticas de acceso ────────────────────────────────────────────────────────

def public() -> None:
    """Ruta sin sesión (login, health)."""


public.policy = "public"  # type: ignore[attr-defined]


def authenticated(user: AuthUser = Depends(get_current_user)) -> AuthUser:
    """Cualquier usuario con sesión, sin permiso específico (p. ej. /auth/me)."""
    return user


authenticated.policy = "authenticated"  # type: ignore[attr-defined]


def require_permission(module: str, action: str):
    """La ruta exige que el rol del usuario tenga `action` sobre `module`. El rol ADMIN pasa siempre."""

    def dependency(user: AuthUser = Depends(get_current_user)) -> AuthUser:
        if user.is_admin:
            return user
        if not permissions_service.role_has_permission(user.role_id, module, action):
            raise forbidden("Insufficient permissions")
        return user

    dependency.policy = (module, action)  # type: ignore[attr-defined]
    return dependency


CurrentUser = Annotated[AuthUser, Depends(authenticated)]
