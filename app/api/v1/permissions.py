from fastapi import APIRouter, Depends

from app.core.serialization import camel
from app.security.deps import AuthUser, require_permission
from app.services import permissions as svc

router = APIRouter(prefix="/permissions", tags=["permissions"])


@router.get("")
def list_permissions(_: AuthUser = Depends(require_permission("permissions", "view"))):
    return camel(svc.list_permissions())


@router.get("/catalog")
def catalog(_: AuthUser = Depends(require_permission("permissions", "view"))):
    return svc.catalog()
