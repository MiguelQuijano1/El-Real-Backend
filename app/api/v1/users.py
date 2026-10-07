from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.core.serialization import camel
from app.schemas.users import AssignRoleIn, CreateUserIn, ResetPasswordIn, SetStatusIn, UpdateUserIn
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import users as svc

router = APIRouter(prefix="/users", tags=["users"])


@router.get("")
def list_users(_: AuthUser = Depends(require_permission("users", "view"))):
    return camel(svc.list_users())


@router.get("/by-email/{email}")
def by_email(email: str, _: AuthUser = Depends(require_permission("users", "view"))):
    return camel(svc.get_by_email(email))


@router.get("/{user_id}")
def get_user(user_id: UUID, _: AuthUser = Depends(require_permission("users", "view"))):
    return camel(svc.get_user(str(user_id)))


@router.post("", status_code=201)
def create_user(dto: CreateUserIn, request: Request, actor: AuthUser = Depends(require_permission("users", "create"))):
    return camel(svc.create_user(dto, actor, request_meta(request)))


@router.patch("/{user_id}")
def update_user(user_id: UUID, dto: UpdateUserIn, request: Request, actor: AuthUser = Depends(require_permission("users", "edit"))):
    return camel(svc.update_user(str(user_id), dto, actor, request_meta(request)))


@router.patch("/{user_id}/status")
def set_status(user_id: UUID, dto: SetStatusIn, request: Request, actor: AuthUser = Depends(require_permission("users", "edit"))):
    return camel(svc.set_status(str(user_id), dto.status, actor, request_meta(request)))


@router.patch("/{user_id}/role")
def assign_role(user_id: UUID, dto: AssignRoleIn, request: Request, actor: AuthUser = Depends(require_permission("users", "edit"))):
    return camel(svc.assign_role(str(user_id), str(dto.role_id), actor, request_meta(request)))


@router.post("/{user_id}/reset-password", status_code=204)
def reset_password(user_id: UUID, dto: ResetPasswordIn, request: Request, actor: AuthUser = Depends(require_permission("users", "edit"))):
    """Restablece la contraseña de otro usuario, desbloquea intentos y cierra sus sesiones."""
    svc.reset_password(str(user_id), dto.new_password, actor, request_meta(request))
    return Response(status_code=204)
