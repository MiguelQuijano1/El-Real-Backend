from uuid import UUID

from fastapi import APIRouter, Depends, Request

from app.core.serialization import camel
from app.schemas.roles import CreateRoleIn, SetRolePermissionsIn, UpdateRoleIn
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import permissions as permissions_service
from app.services import roles as svc

router = APIRouter(prefix="/roles", tags=["roles"])


@router.get("")
def list_roles(_: AuthUser = Depends(require_permission("roles", "view"))):
    return camel(svc.list_roles())


@router.post("", status_code=201)
def create_role(dto: CreateRoleIn, request: Request, actor: AuthUser = Depends(require_permission("roles", "create"))):
    return camel(svc.create_role(dto, actor, request_meta(request)))


@router.get("/{role_id}/permissions")
def role_permissions(role_id: UUID, _: AuthUser = Depends(require_permission("permissions", "view"))):
    return camel(permissions_service.permissions_for_role(str(role_id)))


@router.put("/{role_id}/permissions")
def set_role_permissions(role_id: UUID, dto: SetRolePermissionsIn, request: Request,
                         actor: AuthUser = Depends(require_permission("permissions", "edit"))):
    """Reemplaza la matriz completa del rol: lo que no aparece en el cuerpo queda sin permiso."""
    return camel(svc.set_role_permissions(str(role_id), dto, actor, request_meta(request)))


@router.get("/{role_id}")
def get_role(role_id: UUID, _: AuthUser = Depends(require_permission("roles", "view"))):
    return camel(svc.get_role(str(role_id)))


@router.patch("/{role_id}")
def update_role(role_id: UUID, dto: UpdateRoleIn, request: Request, actor: AuthUser = Depends(require_permission("roles", "edit"))):
    return camel(svc.update_role(str(role_id), dto, actor, request_meta(request)))
