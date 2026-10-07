from fastapi import APIRouter, Depends, Request

from app.core.serialization import camel
from app.schemas.settings import UpdateSettingsIn
from app.security.deps import AuthUser, request_meta, require_permission
from app.services import settings as svc

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings(_: AuthUser = Depends(require_permission("settings", "view"))):
    return camel(svc.get_settings_row())


@router.patch("")
def update_settings(dto: UpdateSettingsIn, request: Request, actor: AuthUser = Depends(require_permission("settings", "edit"))):
    return camel(svc.update_settings(dto, actor, request_meta(request)))
